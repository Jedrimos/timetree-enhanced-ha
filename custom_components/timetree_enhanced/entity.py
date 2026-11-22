"""Base entity for TimeTree Enhanced."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_CALENDAR_NAME, DOMAIN
from .coordinator import TimeTreeCoordinator


class TimeTreeEntity(CoordinatorEntity[TimeTreeCoordinator]):
    """Common base: groups all entities of a calendar under one service device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TimeTreeCoordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.calendar_id)},
            name=entry.data.get(CONF_CALENDAR_NAME) or entry.title or "TimeTree",
            manufacturer="TimeTree",
            model="Calendar",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://timetreeapp.com",
        )


def slug(value: str) -> str:
    """Slug used in unique ids (kept identical to v1.0.x for compatibility)."""
    return value.lower().replace(" ", "_")
