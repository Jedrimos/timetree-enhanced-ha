"""Calendar platform for TimeTree Enhanced.

Creates:
  • one "All" calendar with every event of the TimeTree calendar
  • one calendar per label that has upcoming events (e.g. one per person)
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import logging
from typing import Any

from homeassistant.components.calendar import (
    CalendarEntity,
    CalendarEntityFeature,
    CalendarEvent,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import TimeTreeConfigEntry
from .api import TimeTreeError
from .const import DOMAIN, MEMBER_COLORS
from .coordinator import TimeTreeCoordinator
from .entity import TimeTreeEntity, slug
from .events import bounds_as_utc, event_bounds, label_name

_LOGGER = logging.getLogger(__name__)

NO_TITLE = "(Kein Titel)"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TimeTreeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the "All" calendar plus one calendar per active label."""
    coordinator = entry.runtime_data.coordinator
    entities: list[TimeTreeBaseCalendar] = [TimeTreeAllCalendar(coordinator)]
    for idx, member in enumerate(entry.runtime_data.members):
        entities.append(
            TimeTreeMemberCalendar(
                coordinator,
                member_name=member["name"],
                member_label=member["label_name"],
                color=member.get("color") or MEMBER_COLORS[idx % len(MEMBER_COLORS)],
                label_id=member.get("label_id"),
            )
        )
    async_add_entities(entities)


def raw_to_calendar_event(raw: dict[str, Any]) -> CalendarEvent | None:
    """Convert a raw (already expanded) TimeTree event into a CalendarEvent."""
    bounds = event_bounds(raw)
    if bounds is None:
        return None
    title = str(raw.get("title") or "").strip() or NO_TITLE
    lbl = label_name(raw)
    summary = f"{lbl}: {title}" if lbl else title
    uid = str(raw.get("uuid") or raw.get("id") or "") or None
    try:
        return CalendarEvent(
            start=bounds[0],
            end=bounds[1],
            summary=summary,
            description=str(raw.get("note") or raw.get("description") or "") or None,
            location=str(raw.get("location") or "") or None,
            uid=uid,
            recurrence_id=raw.get("recurrence_id"),
        )
    except (ValueError, TypeError):
        _LOGGER.debug("TimeTree Enhanced: cannot convert event %s", uid)
        return None


class TimeTreeBaseCalendar(TimeTreeEntity, CalendarEntity):
    """Shared logic of the "All" and the per-label calendars."""

    _attr_supported_features = CalendarEntityFeature.CREATE_EVENT
    _label_id: Any = None

    def _event_belongs(self, raw: dict[str, Any]) -> bool:
        return True

    def _events_between(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        start = start if start.tzinfo else start.replace(tzinfo=UTC)
        end = end if end.tzinfo else end.replace(tzinfo=UTC)
        found: list[tuple[datetime, CalendarEvent]] = []
        for raw in self.coordinator.data or []:
            if not self._event_belongs(raw):
                continue
            cal_event = raw_to_calendar_event(raw)
            if cal_event is None:
                continue
            ev_start, ev_end = bounds_as_utc((cal_event.start, cal_event.end))
            if ev_start < end and ev_end > start:
                found.append((ev_start, cal_event))
        found.sort(key=lambda item: item[0])
        return [ev for _, ev in found]

    @property
    def event(self) -> CalendarEvent | None:
        """Return the current or next upcoming event."""
        now = dt_util.utcnow()
        events = self._events_between(now, now + timedelta(days=366))
        return events[0] if events else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ev = self.event
        if ev is None:
            return {}
        all_day = not isinstance(ev.start, datetime)
        start_local = ev.start if all_day else dt_util.as_local(ev.start)
        end_local = ev.end if all_day else dt_util.as_local(ev.end)
        return {
            "next_event_summary": ev.summary,
            "next_event_start": start_local.isoformat(),
            "next_event_end": end_local.isoformat(),
            "next_event_all_day": all_day,
            "next_event_time": "Ganztags" if all_day else start_local.strftime("%H:%M"),
            "next_event_date": start_local.strftime("%d.%m.%Y"),
            "next_event_location": ev.location or "",
            "next_event_description": ev.description or "",
        }

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return self._events_between(start_date, end_date)

    async def async_create_event(self, **kwargs: Any) -> None:
        """Create an event in TimeTree (label is set for per-label calendars)."""
        dtstart: datetime | date = kwargs["dtstart"]
        dtend: datetime | date = kwargs["dtend"]
        if kwargs.get("rrule"):
            raise HomeAssistantError("Recurring events cannot be created via Home Assistant")

        all_day = not isinstance(dtstart, datetime)
        if not all_day:
            assert isinstance(dtend, datetime)
            dtstart = dt_util.as_utc(
                dtstart
                if dtstart.tzinfo
                else dtstart.replace(tzinfo=dt_util.get_default_time_zone())
            )
            dtend = dt_util.as_utc(
                dtend if dtend.tzinfo else dtend.replace(tzinfo=dt_util.get_default_time_zone())
            )

        coordinator: TimeTreeCoordinator = self.coordinator
        try:
            await coordinator.async_call(
                lambda: coordinator.api.create_event(
                    coordinator.calendar_id,
                    title=kwargs.get("summary") or "",
                    start=dtstart,
                    end=dtend,
                    all_day=all_day,
                    tz_name=self.hass.config.time_zone,
                    description=kwargs.get("description") or "",
                    location=kwargs.get("location") or "",
                    label_id=self._label_id,
                )
            )
        except ConfigEntryAuthFailed as err:
            coordinator.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(f"TimeTree authentication failed: {err}") from err
        except TimeTreeError as err:
            raise HomeAssistantError(f"Could not create TimeTree event: {err}") from err

        await coordinator.async_request_refresh()


class TimeTreeAllCalendar(TimeTreeBaseCalendar):
    """Every event of the TimeTree calendar."""

    _attr_translation_key = "all"

    def __init__(self, coordinator: TimeTreeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.calendar_id}_all"


class TimeTreeMemberCalendar(TimeTreeBaseCalendar):
    """Only events with a given label."""

    def __init__(
        self,
        coordinator: TimeTreeCoordinator,
        *,
        member_name: str,
        member_label: str,
        color: str,
        label_id: Any = None,
    ) -> None:
        super().__init__(coordinator)
        self._member_name = member_name
        self._member_label = member_label.lower()
        self._color = color
        self._label_id = label_id
        self._attr_name = member_name
        self._attr_unique_id = f"{DOMAIN}_{coordinator.calendar_id}_member_{slug(member_name)}"

    def _event_belongs(self, raw: dict[str, Any]) -> bool:
        return label_name(raw).lower() == self._member_label

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            **super().extra_state_attributes,
            "member": self._member_name,
            "color_hint": self._color,
        }
