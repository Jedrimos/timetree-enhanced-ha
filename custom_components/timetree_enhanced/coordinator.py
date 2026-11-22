"""Data update coordinator for TimeTree Enhanced."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
import logging
from typing import Any, TypeVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    TimeTreeAPI,
    TimeTreeAPIError,
    TimeTreeAuthError,
    TimeTreeInvalidCredentials,
)
from .const import (
    CONF_CALENDAR_ID,
    CONF_EMAIL,
    CONF_FETCH_DAYS,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_TIMEZONE,
    DEFAULT_FETCH_DAYS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .events import expand_event, filter_events_in_range

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

_T = TypeVar("_T")


def session_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Return the store that persists the session cookie of a config entry."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry_id}_session")


class TimeTreeCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    """Fetches events of one TimeTree calendar and keeps the session alive."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: TimeTreeAPI) -> None:
        self.api = api
        self.calendar_id: str = entry.data[CONF_CALENDAR_ID]
        self.tz_name: str = entry.data.get(CONF_TIMEZONE) or hass.config.time_zone
        self.fetch_days: int = int(
            entry.options.get(CONF_FETCH_DAYS, entry.data.get(CONF_FETCH_DAYS, DEFAULT_FETCH_DAYS))
        )
        scan_interval = int(
            entry.options.get(
                CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            )
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{self.calendar_id}",
            update_interval=timedelta(minutes=scan_interval),
        )
        self._store = session_store(hass, entry.entry_id)
        #: label id → {"name", "color"} (refreshed on every poll)
        self.labels: dict[Any, dict[str, Any]] = {}
        #: time of the last successful sync
        self.last_sync: datetime | None = None

    async def async_restore_session(self) -> None:
        """Load a persisted session cookie to avoid logging in on every restart."""
        stored = await self._store.async_load() or {}
        if session_id := stored.get("session_id"):
            self.api.restore_session(session_id)
            _LOGGER.debug("TimeTree Enhanced: restored session cookie from storage")

    async def _async_login(self) -> None:
        entry = self.config_entry
        try:
            await self.api.login(entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
        except TimeTreeInvalidCredentials as err:
            await self._store.async_remove()
            raise ConfigEntryAuthFailed(str(err)) from err
        if session_id := self.api.session_id:
            await self._store.async_save({"session_id": session_id})

    async def async_call(self, func: Callable[[], Awaitable[_T]]) -> _T:
        """Run an API call, logging in first or re-logging in once on expiry.

        Raises ConfigEntryAuthFailed when the stored credentials are rejected.
        """
        if not self.api.is_authenticated:
            await self._async_login()
        try:
            return await func()
        except TimeTreeAuthError:
            _LOGGER.debug("TimeTree Enhanced: session expired, logging in again")
            await self._async_login()
            return await func()

    async def _async_update_data(self) -> list[dict[str, Any]]:
        try:
            return await self.async_call(self._async_fetch_events)
        except ConfigEntryAuthFailed:
            raise
        except TimeTreeAuthError as err:
            raise UpdateFailed(f"Authentication error: {err}") from err
        except TimeTreeAPIError as err:
            raise UpdateFailed(f"TimeTree API error: {err}") from err

    async def _async_fetch_events(self) -> list[dict[str, Any]]:
        raw_events = await self.api.get_all_events_sync(self.calendar_id)

        try:
            labels = await self.api.get_calendar_labels(self.calendar_id)
        except TimeTreeAuthError:
            raise
        except TimeTreeAPIError as err:
            _LOGGER.debug("TimeTree Enhanced: could not fetch labels: %s", err)
        else:
            self.labels = {
                lbl["id"]: {"name": lbl["name"], "color": lbl["color"]}
                for lbl in labels
                if lbl.get("id") is not None
            }

        now = datetime.now(UTC)
        # Start of today so events that began earlier today are included.
        window_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        window_end = window_start + timedelta(days=self.fetch_days)

        expanded: list[dict[str, Any]] = []
        for ev in raw_events:
            if not ev.get("label") and ev.get("label_id") is not None:
                lbl = self.labels.get(ev["label_id"]) or self.labels.get(str(ev["label_id"]))
                if lbl:
                    ev = {**ev, "label": lbl}
            expanded.extend(expand_event(ev, window_start, window_end, self.tz_name))

        events = filter_events_in_range(expanded, window_start, window_end)
        _LOGGER.debug(
            "TimeTree Enhanced: %d events in window (%d raw, %d after expansion) for %s",
            len(events),
            len(raw_events),
            len(expanded),
            self.calendar_id,
        )
        self.last_sync = datetime.now(UTC)
        return events

    def label_id_for(self, name: str) -> Any:
        """Return the TimeTree label id for a label name (case-insensitive)."""
        for label_id, lbl in self.labels.items():
            if lbl["name"].lower() == name.lower():
                return label_id
        return None

    def label_color_for(self, name: str) -> str:
        for lbl in self.labels.values():
            if lbl["name"].lower() == name.lower():
                return lbl.get("color") or ""
        return ""
