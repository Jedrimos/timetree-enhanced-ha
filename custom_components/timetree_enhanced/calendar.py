"""Calendar platform for TimeTree Enhanced.

Creates:
  • One "Alle" entity        → every event from the calendar
  • One entity per calendar member → only events whose label matches the member

Calendar members (people with email access) are fetched from the API on setup.
Events are assigned to members by matching event.label.name to the member's
label name (which is typically the member's own name in TimeTree).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .api import TimeTreeAPI, TimeTreeAPIError
from .const import (
    CONF_CALENDAR_NAME,
    CONF_TIMEZONE,
    DEFAULT_TIMEZONE,
    DOMAIN,
    MEMBER_COLORS,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up calendar entities – one per calendar member plus an 'Alle' entity."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: DataUpdateCoordinator = data["coordinator"]
    api: TimeTreeAPI = data["api"]
    calendar_id: str = data["calendar_id"]
    calendar_name: str = entry.data.get(CONF_CALENDAR_NAME, "TimeTree")
    tz: str = entry.data.get(CONF_TIMEZONE, DEFAULT_TIMEZONE)
    members: list[dict] = data.get("members", [])

    entities: list[CalendarEntity] = [
        TimeTreeAllCalendar(coordinator, api, calendar_id, calendar_name, tz)
    ]

    for idx, member in enumerate(members):
        # Use the color from the TimeTree label; fall back to a rotating palette
        color = member.get("color") or MEMBER_COLORS[idx % len(MEMBER_COLORS)]
        entities.append(
            TimeTreeMemberCalendar(
                coordinator=coordinator,
                api=api,
                calendar_id=calendar_id,
                calendar_name=calendar_name,
                member_name=member["name"],
                member_label=member["label_name"],
                color=color,
                tz=tz,
            )
        )
        _LOGGER.debug(
            "TimeTree Enhanced: member calendar → %s (label: %s, color: %s)",
            member["name"],
            member["label_name"],
            color,
        )

    async_add_entities(entities, True)


def _event_label_name(event: dict) -> str:
    """Return the label name of a raw TimeTree event, or empty string."""
    label = event.get("label") or {}
    if isinstance(label, dict):
        return (label.get("name") or "").strip()
    return ""


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

    def _event_belongs(self, event: dict) -> bool:
        """Return True if this event should appear in this calendar."""
        return True

    def _iter_calendar_events(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=timezone.utc)
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)

        result: list[CalendarEvent] = []
        for raw in self.coordinator.data or []:
            if not self._event_belongs(raw):
                continue
            raw_title = (raw.get("title") or "").strip() or "(Kein Titel)"
            label_name = _event_label_name(raw)
            display_title = f"{label_name}: {raw_title}" if label_name else raw_title
            cal_event = _raw_to_calendar_event(raw, display_title)
            if cal_event is None:
                continue
            event_start = _as_datetime(cal_event.start)
            event_end = _as_datetime(cal_event.end)
            if event_start < end_date and event_end > start_date:
                result.append(cal_event)

        result.sort(key=lambda e: _as_datetime(e.start))
        return result

    @property
    def event(self) -> CalendarEvent | None:
        now = datetime.now(tz=timezone.utc)
        for cal_event in self._iter_calendar_events(now, now + timedelta(days=60)):
            if _as_datetime(cal_event.end) > now:
                return cal_event
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
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
        return self._iter_calendar_events(start_date, end_date)

    async def async_create_event(self, **kwargs: Any) -> None:
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

        try:
            await self._api.create_event(
                self._calendar_id,
                title=summary,
                start=dtstart,
                end=dtend,
                all_day=all_day,
                description=description,
                location=location,
            )
            _LOGGER.debug("TimeTree Enhanced: event created – '%s'", summary)
        except TimeTreeAPIError as err:
            _LOGGER.error("TimeTree Enhanced: event creation failed: %s", err)
            raise

        await self.coordinator.async_request_refresh()


class TimeTreeAllCalendar(TimeTreeBaseCalendar):
    """Shows every event from the TimeTree calendar."""

    def __init__(self, coordinator, api, calendar_id, calendar_name, tz) -> None:
        super().__init__(coordinator, api, calendar_id, calendar_name, tz)
        self._attr_name = f"{calendar_name} – Alle"
        self._attr_unique_id = f"{DOMAIN}_{calendar_id}_all"


class TimeTreeMemberCalendar(TimeTreeBaseCalendar):
    """Shows only events whose label matches this calendar member."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        api: TimeTreeAPI,
        calendar_id: str,
        calendar_name: str,
        member_name: str,
        member_label: str,
        color: str,
        tz: str,
    ) -> None:
        super().__init__(coordinator, api, calendar_id, calendar_name, tz)
        self._member_name = member_name
        self._member_label = member_label
        self._color = color
        self._attr_name = f"{calendar_name} – {member_name}"
        self._attr_unique_id = (
            f"{DOMAIN}_{calendar_id}_member_{member_name.lower().replace(' ', '_')}"
        )

    def _event_belongs(self, event: dict) -> bool:
        return _event_label_name(event).lower() == self._member_label.lower()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        base = super().extra_state_attributes
        base["member"] = self._member_name
        base["color_hint"] = self._color
        return base


def _raw_to_calendar_event(event: dict, display_title: str) -> CalendarEvent | None:
    """Convert a raw TimeTree event dict to a HA CalendarEvent."""
    try:
        uid = str(event.get("id", ""))
        all_day: bool = bool(event.get("all_day", False))
        start_raw = event.get("start_at") or event.get("start")
        end_raw = event.get("end_at") or event.get("end")

        if not start_raw or not end_raw:
            return None

        if all_day:
            start_dt = _parse_date(start_raw)
            end_dt = _parse_date(end_raw)
            if start_dt is None or end_dt is None:
                return None
            if isinstance(start_dt, datetime):
                start_dt = start_dt.date()
            if isinstance(end_dt, datetime):
                end_dt = end_dt.date()
            # HA CalendarEvent requires exclusive end for all-day events.
            # TimeTree sync stores end_at as the last visible day (inclusive),
            # so we always add 1 day to get the iCal-style exclusive end.
            if end_dt <= start_dt:
                end_dt = start_dt + timedelta(days=1)
            else:
                end_dt = end_dt + timedelta(days=1)
            return CalendarEvent(
                summary=display_title,
                start=start_dt,
                end=end_dt,
                description=event.get("note") or event.get("description") or None,
                location=event.get("location") or None,
                uid=uid,
            )
        else:
            start_dt = _parse_datetime(start_raw)
            end_dt = _parse_datetime(end_raw)
            if start_dt is None or end_dt is None:
                return None
            if end_dt <= start_dt:
                end_dt = start_dt + timedelta(minutes=30)
            return CalendarEvent(
                summary=display_title,
                start=start_dt,
                end=end_dt,
                description=event.get("note") or event.get("description") or None,
                location=event.get("location") or None,
                uid=uid,
            )
    except Exception:
        _LOGGER.debug("TimeTree Enhanced: failed to parse event %s", event.get("id"))
        return None


def _as_datetime(dt: datetime | date) -> datetime:
    if isinstance(dt, datetime):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)


def _parse_datetime(raw: str | int | float) -> datetime | None:
    """Parse ISO string or Unix timestamp (int/float, ms or s) to UTC-aware datetime."""
    try:
        if isinstance(raw, (int, float)):
            secs = raw / 1000 if raw > 1e10 else raw
            return datetime.fromtimestamp(secs, tz=timezone.utc)
        raw = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError, OSError):
        return None


def _parse_date(raw: str | int | float) -> date | None:
    """Parse ISO date string or Unix timestamp (ms or s) to a date."""
    try:
        if isinstance(raw, (int, float)):
            secs = raw / 1000 if raw > 1e10 else raw
            return datetime.fromtimestamp(secs, tz=timezone.utc).date()
        raw = str(raw)
        if "T" in raw or " " in raw:
            dt = _parse_datetime(raw)
            return dt.date() if dt else None
        return date.fromisoformat(raw[:10])
    except (ValueError, AttributeError, OSError):
        return None
