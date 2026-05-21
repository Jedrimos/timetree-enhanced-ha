"""Calendar platform for TimeTree Enhanced.

Creates:
  • One "Alle" entity  →  every event from the calendar
  • One entity per detected member  →  only that member's events

New members discovered on later coordinator refreshes automatically
get their own entity (no restart required).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .api import TimeTreeAPI, TimeTreeAPIError
from .const import (
    CONF_CALENDAR_NAME,
    CONF_TIMEZONE,
    DEFAULT_TIMEZONE,
    DOMAIN,
    MEMBER_COLORS,
    NO_MEMBER,
)
from .helpers import (
    event_to_calendar_event,
    extract_unique_members,
    parse_member_and_title,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up calendar entities."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: DataUpdateCoordinator = data["coordinator"]
    api: TimeTreeAPI = data["api"]
    calendar_id: str = data["calendar_id"]
    calendar_name: str = entry.data.get(CONF_CALENDAR_NAME, "TimeTree")
    tz: str = entry.data.get(CONF_TIMEZONE, DEFAULT_TIMEZONE)

    known_members: set[str] = set()
    color_index: dict[str, int] = {}

    def _add_new_members(events: list[dict]) -> None:
        new_entities: list[CalendarEntity] = []
        for member in extract_unique_members(events):
            if member in known_members:
                continue
            known_members.add(member)
            idx = len(color_index)
            color_index[member] = idx % len(MEMBER_COLORS)
            new_entities.append(
                TimeTreeMemberCalendar(
                    coordinator=coordinator,
                    api=api,
                    calendar_id=calendar_id,
                    calendar_name=calendar_name,
                    member_name=member,
                    color=MEMBER_COLORS[color_index[member]],
                    tz=tz,
                )
            )
            _LOGGER.info("TimeTree Enhanced: new member calendar created → %s", member)

        if new_entities:
            async_add_entities(new_entities, True)

    async_add_entities(
        [
            TimeTreeAllCalendar(
                coordinator=coordinator,
                api=api,
                calendar_id=calendar_id,
                calendar_name=calendar_name,
                tz=tz,
            )
        ],
        True,
    )

    if coordinator.data:
        _add_new_members(coordinator.data)

    @callback
    def _on_coordinator_update() -> None:
        if coordinator.data:
            _add_new_members(coordinator.data)

    entry.async_on_unload(coordinator.async_add_listener(_on_coordinator_update))


class TimeTreeBaseCalendar(CoordinatorEntity, CalendarEntity):
    """Shared base for All- and Member-calendar entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        api: TimeTreeAPI,
        calendar_id: str,
        calendar_name: str,
        tz: str,
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._calendar_id = calendar_id
        self._calendar_name = calendar_name
        self._tz = tz

    def _iter_calendar_events(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        # Ensure bounds are timezone-aware (HA calendar card may pass naive datetimes)
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)

        result: list[CalendarEvent] = []
        for raw in self.coordinator.data or []:
            member, display_title = parse_member_and_title(raw)
            if not self._event_belongs(member):
                continue
            cal_event = event_to_calendar_event(raw, display_title)
            if cal_event is None:
                continue
            event_start = _as_datetime(cal_event.start)
            event_end = _as_datetime(cal_event.end)
            if event_start < end_date and event_end > start_date:
                result.append(cal_event)

        result.sort(key=lambda e: _as_datetime(e.start))
        return result

    def _event_belongs(self, member: str) -> bool:  # noqa: ARG002
        return True

    @property
    def event(self) -> CalendarEvent | None:
        """Return the current or next upcoming event."""
        now = datetime.now(tz=timezone.utc)
        window_end = now + timedelta(days=60)
        for cal_event in self._iter_calendar_events(now, window_end):
            if _as_datetime(cal_event.end) > now:
                return cal_event
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose next event time info for dashboard cards."""
        attrs: dict[str, Any] = {}
        ev = self.event
        if ev is None:
            return attrs

        if isinstance(ev.start, datetime):
            attrs["next_event_start"] = ev.start.isoformat()
            attrs["next_event_end"] = ev.end.isoformat()
            attrs["next_event_all_day"] = False
            attrs["next_event_time"] = ev.start.strftime("%H:%M")
            attrs["next_event_date"] = ev.start.strftime("%d.%m.%Y")
        else:
            attrs["next_event_start"] = ev.start.isoformat()
            attrs["next_event_end"] = ev.end.isoformat()
            attrs["next_event_all_day"] = True
            attrs["next_event_time"] = "Ganztags"
            attrs["next_event_date"] = ev.start.strftime("%d.%m.%Y")

        attrs["next_event_summary"] = ev.summary
        attrs["next_event_location"] = ev.location or ""
        attrs["next_event_description"] = ev.description or ""
        return attrs

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return all events in the requested range (used by calendar card)."""
        return self._iter_calendar_events(start_date, end_date)

    async def async_create_event(self, **kwargs: Any) -> None:
        """Create an event in TimeTree."""
        dtstart: datetime | date = kwargs["dtstart"]
        dtend: datetime | date = kwargs["dtend"]
        summary: str = kwargs.get("summary", "")
        description: str = kwargs.get("description", "") or ""
        location: str = kwargs.get("location", "") or ""

        all_day = isinstance(dtstart, date) and not isinstance(dtstart, datetime)
        if not all_day:
            if isinstance(dtstart, datetime) and dtstart.tzinfo is None:
                dtstart = dtstart.replace(tzinfo=timezone.utc)
            if isinstance(dtend, datetime) and dtend.tzinfo is None:
                dtend = dtend.replace(tzinfo=timezone.utc)
        else:
            dtstart = datetime(dtstart.year, dtstart.month, dtstart.day, tzinfo=timezone.utc)
            dtend = datetime(dtend.year, dtend.month, dtend.day, tzinfo=timezone.utc)

        title = self._build_create_title(summary)

        try:
            await self._api.create_event(
                self._calendar_id,
                title=title,
                start=dtstart,
                end=dtend,
                all_day=all_day,
                description=description,
                location=location,
            )
            _LOGGER.debug("TimeTree Enhanced: event created – '%s'", title)
        except TimeTreeAPIError as err:
            _LOGGER.error("TimeTree Enhanced: event creation failed: %s", err)
            raise

        await self.coordinator.async_request_refresh()

    def _build_create_title(self, summary: str) -> str:
        return summary


class TimeTreeAllCalendar(TimeTreeBaseCalendar):
    """Shows every event from the TimeTree calendar."""

    def __init__(self, coordinator, api, calendar_id, calendar_name, tz) -> None:
        super().__init__(coordinator, api, calendar_id, calendar_name, tz)
        self._attr_name = f"{calendar_name} – Alle"
        self._attr_unique_id = f"{DOMAIN}_{calendar_id}_all"

    def _event_belongs(self, member: str) -> bool:  # noqa: ARG002
        return True


class TimeTreeMemberCalendar(TimeTreeBaseCalendar):
    """Shows only events belonging to one calendar member."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        api: TimeTreeAPI,
        calendar_id: str,
        calendar_name: str,
        member_name: str,
        color: str,
        tz: str,
    ) -> None:
        super().__init__(coordinator, api, calendar_id, calendar_name, tz)
        self._member = member_name
        self._color = color
        self._attr_name = f"{calendar_name} – {member_name}"
        self._attr_unique_id = (
            f"{DOMAIN}_{calendar_id}_{member_name.lower().replace(' ', '_')}"
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        base = super().extra_state_attributes
        base["member"] = self._member
        base["color_hint"] = self._color
        return base

    def _event_belongs(self, member: str) -> bool:
        return member == self._member

    def _build_create_title(self, summary: str) -> str:
        if summary.startswith(f"{self._member}:"):
            return summary
        return f"{self._member}: {summary}"


def _as_datetime(dt: datetime | date) -> datetime:
    """Coerce a date to a timezone-aware midnight datetime for range comparisons."""
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    return datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
