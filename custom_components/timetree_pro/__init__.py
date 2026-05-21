"""TimeTree Pro – Home Assistant integration setup."""
from __future__ import annotations

import logging
from datetime import timedelta

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
    """Set up TimeTree Pro from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    session = async_get_clientsession(hass)
    api = TimeTreeAPI(session)

    # Prefer options (reconfigurable) over initial config data
    scan_interval: int = entry.options.get(
        CONF_SCAN_INTERVAL,
        entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )
    tz: str = entry.data.get(CONF_TIMEZONE, DEFAULT_TIMEZONE)
    fetch_days: int = entry.data.get(CONF_FETCH_DAYS, DEFAULT_FETCH_DAYS)
    calendar_id: str = entry.data[CONF_CALENDAR_ID]

    async def _fetch() -> list[dict]:
        """Login + fetch all upcoming events."""
        try:
            await api.login(entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
            events = await api.get_upcoming_events(
                calendar_id, days=fetch_days, tz=tz
            )
            _LOGGER.debug(
                "TimeTree Pro fetched %d events for calendar %s",
                len(events),
                calendar_id,
            )
            return events
        except TimeTreeAuthError as err:
            raise UpdateFailed(f"Auth error: {err}") from err
        except TimeTreeAPIError as err:
            raise UpdateFailed(f"API error: {err}") from err

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
    """Reload when options change (e.g. new scan interval)."""
    await hass.config_entries.async_reload(entry.entry_id)
