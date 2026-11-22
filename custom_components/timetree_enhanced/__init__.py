"""TimeTree Enhanced – Home Assistant integration setup."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .api import TimeTreeAPI
from .const import DEFAULT_LABEL_NAMES
from .coordinator import TimeTreeCoordinator, session_store
from .events import active_labels

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CALENDAR, Platform.SENSOR]


@dataclass
class TimeTreeRuntimeData:
    """Runtime data stored on the config entry."""

    coordinator: TimeTreeCoordinator
    #: one entry per label that gets its own sub-calendar
    members: list[dict[str, Any]] = field(default_factory=list)


type TimeTreeConfigEntry = ConfigEntry[TimeTreeRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: TimeTreeConfigEntry) -> bool:
    """Set up TimeTree Enhanced from a config entry."""
    api = TimeTreeAPI()
    try:
        coordinator = TimeTreeCoordinator(hass, entry, api)
        await coordinator.async_restore_session()
        await coordinator.async_config_entry_first_refresh()
    except BaseException:
        await api.close()
        raise

    entry.runtime_data = TimeTreeRuntimeData(
        coordinator=coordinator,
        members=_discover_members(coordinator),
    )
    entry.async_on_unload(api.close)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _discover_members(coordinator: TimeTreeCoordinator) -> list[dict[str, Any]]:
    """Return one sub-calendar definition per label with upcoming events."""
    members: list[dict[str, Any]] = []
    for lbl in active_labels(coordinator.data or [], datetime.now(UTC)):
        name = lbl["name"]
        if name.lower() in DEFAULT_LABEL_NAMES:
            continue
        members.append(
            {
                "name": name,
                "label_name": name,
                "color": coordinator.label_color_for(name) or lbl["color"],
                "label_id": coordinator.label_id_for(name),
            }
        )
    if members:
        _LOGGER.debug(
            "TimeTree Enhanced: %d label calendars: %s",
            len(members),
            [m["name"] for m in members],
        )
    return members


async def async_unload_entry(hass: HomeAssistant, entry: TimeTreeConfigEntry) -> bool:
    """Unload a config entry (the session cookie is kept for the next load)."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the persisted session cookie when the entry is removed."""
    await session_store(hass, entry.entry_id).async_remove()


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
