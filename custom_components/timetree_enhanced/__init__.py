"""TimeTree Enhanced – Home Assistant integration setup."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TimeTreeAPI, TimeTreeAPIError, TimeTreeAuthError
from .const import (
    CONF_CALENDAR_ID,
    CONF_EMAIL,
    CONF_FETCH_DAYS,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_TIMEZONE,
    DEFAULT_FETCH_DAYS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEZONE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CALENDAR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up TimeTree Enhanced from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    session = async_get_clientsession(hass)
    api = TimeTreeAPI(session)

    scan_interval: int = entry.options.get(
        CONF_SCAN_INTERVAL,
        entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )
    tz: str = entry.data.get(CONF_TIMEZONE, DEFAULT_TIMEZONE)
    fetch_days: int = entry.options.get(
        CONF_FETCH_DAYS,
        entry.data.get(CONF_FETCH_DAYS, DEFAULT_FETCH_DAYS),
    )
    calendar_id: str = entry.data[CONF_CALENDAR_ID]

    # Mutable container so _fetch() can write the timestamp and sensors can read it
    last_sync: dict[str, datetime | None] = {"time": None}

    async def _fetch() -> list[dict]:
        """Login + fetch all upcoming events including recurring ones."""
        try:
            await api.login(entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])

            now = datetime.now(timezone.utc)
            # 14 days back to catch ongoing multi-day events, fetch_days forward
            start = now - timedelta(days=14)
            end = now + timedelta(days=fetch_days)

            # Range endpoint includes recurring events (birthdays, anniversaries)
            try:
                events = await api.get_events_in_range(calendar_id, start, end, tz=tz)
                _LOGGER.debug(
                    "TimeTree Enhanced: %d events via range endpoint for %s",
                    len(events),
                    calendar_id,
                )
                last_sync["time"] = datetime.now(timezone.utc)
                return events
            except TimeTreeAPIError as range_err:
                _LOGGER.warning(
                    "TimeTree Enhanced: range endpoint failed (%s), falling back to upcoming_events",
                    range_err,
                )

            # Fallback: upcoming_events (no recurring)
            events = await api.get_upcoming_events(calendar_id, days=fetch_days, tz=tz)
            _LOGGER.debug(
                "TimeTree Enhanced: %d events via upcoming_events for %s",
                len(events),
                calendar_id,
            )
            last_sync["time"] = datetime.now(timezone.utc)
            return events

        except TimeTreeAuthError as err:
            raise UpdateFailed(f"Auth-Fehler: {err}") from err
        except TimeTreeAPIError as err:
            raise UpdateFailed(f"API-Fehler: {err}") from err
        except Exception as err:
            _LOGGER.exception("TimeTree Enhanced: unexpected error during fetch")
            raise UpdateFailed(str(err)) from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_{calendar_id}",
        update_method=_fetch,
        update_interval=timedelta(minutes=scan_interval),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "api": api,
        "calendar_id": calendar_id,
        "last_sync": last_sync,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
