"""Calendar platform for TimeTree Pro.

Creates:
  • One "Alle" entity  →  every event from the calendar
  • One entity per detected member  →  only that member's events

Member detection uses helpers.parse_member_and_title():
  1. "Name: Termin" prefix in the event title  (e.g. "Mama: Zahnarzt")
  2. TimeTree label name as fallback           (if user-renamed, e.g. "Mama")

Event titles are shown as "Member · Termin".

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
    DEFAULT_TIMEZONE,
    DOMAIN,
    MEMBER_COLORS,
    NO_MEMBER,
    CONF_TIMEZONE,
)
from .helpers import (
    event_to_calendar_event,
    extract_unique_members,
    parse_member_and_title,
)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Platform setup
# ---------------------------------------------------------------------------

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
        """Detect new members in *events* and register their calendar entities."""
        new_entities: list[CalendarEntity] = []
        members = extract_unique_members(events)

        for member in members:
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
            _LOGGER.info("TimeTree Pro: new member calendar → %s", member)

        if new_entities:
            async_add_entities(new_entities, True)

    # "Alle" entity (shown always)
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

    # Initial member discovery from first coordinator data
    if coordinator.data:
        _add_new_members(coordinator.data)

    # Dynamic discovery on subsequent refreshes
    @callback
    def _on_coordinator_update() -> None:
        if coordinator.data:
            _add_new_members(coordinator.data)

    entry.async_on_unload(coordinator.async_add_listener(_on_coordinator_update))


# ---------------------------------------------------------------------------
# Base entity
# ---------------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _iter_calendar_events(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """
        Filter coordinator events into CalendarEvent objects within the window.
        Subclasses override _event_belongs() to select which events to include.
        """
        result: list[CalendarEvent] = []
        for raw in self.coordinator.data or []:
            member, display_title = parse_member_and_title(raw)
            if not self._event_belongs(member):
                continue
            cal_event = event_to_calendar_event(raw, display_title)
            if cal_event is None:
                continue
            # Range check
            event_start = _as_datetime(cal_event.start)
            event_end = _as_datetime(cal_event.end)
            if event_start < end_date and event_end > start_date:
                result.append(cal_event)

        result.sort(key=lambda e: _as_datetime(e.start))
        return result

    def _event_belongs(self, member: str) -> bool:  # noqa: ARG002
        """Return True if this entity should include events from *member*."""
        return True  # All-calendar default

    # ------------------------------------------------------------------
    # CalendarEntity API
    # ------------------------------------------------------------------

    @property
    def event(self) -> CalendarEvent | None:
        """Return the current or next upcoming event."""
        now = datetime.now(tz=timezone.utc)
        window_end = now + timedelta(days=60)
        upcoming = self._iter_calendar_events(now, window_end)
        # Prefer events that are currently active, then the soonest future one
        for cal_event in upcoming:
            end = _as_datetime(cal_event.end)
            if end > now:
                return cal_event
        return None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return all events in the requested range (used by calendar card)."""
        return self._iter_calendar_events(start_date, end_date)

    # ------------------------------------------------------------------
    # Event creation
    # ------------------------------------------------------------------

    async def async_create_event(self, **kwargs: Any) -> None:
        """Create an event in TimeTree.

        For member calendars the member name is automatically prepended
        to the summary so it appears in the correct per-person calendar
        after the next sync.
        """
        dtstart: datetime | date = kwargs["dtstart"]
        dtend: datetime | date = kwargs["dtend"]
        summary: str = kwargs.get("summary", "")
        description: str = kwargs.get("description", "") or ""
        location: str = kwargs.get("location", "") or ""

        # Ensure datetimes are timezone-aware
        all_day = isinstance(dtstart, date) and not isinstance(dtstart, datetime)
        if not all_day:
            if isinstance(dtstart, datetime) and dtstart.tzinfo is None:
                dtstart = dtstart.replace(tzinfo=timezone.utc)
            if isinstance(dtend, datetime) and dtend.tzinfo is None:
                dtend = dtend.replace(tzinfo=timezone.utc)
        else:
            # Convert date → datetime at midnight UTC for the API call
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
        except TimeTreeAPIError as err:
            _LOGGER.error("Failed to create TimeTree event: %s", err)
            raise

        await self.coordinator.async_request_refresh()

    def _build_create_title(self, summary: str) -> str:
        """Return the title string to send to TimeTree. Overridden by member entity."""
        return summary


# ---------------------------------------------------------------------------
# "Alle" entity
# ---------------------------------------------------------------------------

class TimeTreeAllCalendar(TimeTreeBaseCalendar):
    """Shows every event from the TimeTree calendar."""

    def __init__(self, coordinator, api, calendar_id, calendar_name, tz) -> None:
        super().__init__(coordinator, api, calendar_id, calendar_name, tz)
        self._attr_name = f"{calendar_name} – Alle"
        self._attr_unique_id = f"{DOMAIN}_{calendar_id}_all"

    def _event_belongs(self, member: str) -> bool:  # noqa: ARG002
        return True


# ---------------------------------------------------------------------------
# Per-member entity
# ---------------------------------------------------------------------------

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
        self._attr_name = f"{calendar_name} – {member_name}"
        self._attr_unique_id = f"{DOMAIN}_{calendar_id}_{member_name.lower().replace(' ', '_')}"
        # HA uses entity_description or extra_state_attributes for color hints;
        # we expose it as an attribute so themes/custom-cards can pick it up.
        self._color = color

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"member": self._member, "color_hint": self._color}

    def _event_belongs(self, member: str) -> bool:
        return member == self._member

    def _build_create_title(self, summary: str) -> str:
        """Prepend member name so the event lands back in this member's calendar."""
        if summary.startswith(f"{self._member}:"):
            return summary  # already prefixed
        return f"{self._member}: {summary}"


# ---------------------------------------------------------------------------
# Util
# ---------------------------------------------------------------------------

def _as_datetime(dt: datetime | date) -> datetime:
    """Coerce a date to a timezone-aware midnight datetime for range comparisons."""
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    return datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
