"""Sensor platform for TimeTree Enhanced.

Provides:
  • sensor.timetree_enhanced_<name>_zuletzt_synchronisiert  – last sync timestamp
  • sensor.timetree_enhanced_<name>_<member>_anzahl         – event count per member
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import CONF_CALENDAR_NAME, DOMAIN, NO_MEMBER
from .helpers import extract_unique_members, parse_member_and_title

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: DataUpdateCoordinator = data["coordinator"]
    calendar_id: str = data["calendar_id"]
    calendar_name: str = entry.data.get(CONF_CALENDAR_NAME, "TimeTree")

    sensors: list[SensorEntity] = [
        TimeTreeLastUpdatedSensor(coordinator, calendar_id, calendar_name)
    ]

    if coordinator.data:
        for member in extract_unique_members(coordinator.data):
            sensors.append(
                TimeTreeMemberCountSensor(coordinator, calendar_id, calendar_name, member)
            )

    async_add_entities(sensors, True)


class TimeTreeLastUpdatedSensor(CoordinatorEntity, SensorEntity):
    """Timestamp of the last successful TimeTree sync."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:calendar-sync"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        calendar_id: str,
        calendar_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_name = f"{calendar_name} – Zuletzt synchronisiert"
        self._attr_unique_id = f"{DOMAIN}_{calendar_id}_last_updated"

    @property
    def native_value(self) -> datetime | None:
        if self.coordinator.last_update_success:
            return getattr(
                self.coordinator,
                "last_update_success_time",
                datetime.now(tz=timezone.utc),
            )
        return None


class TimeTreeMemberCountSensor(CoordinatorEntity, SensorEntity):
    """Number of upcoming events for one member."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:calendar-account"
    _attr_native_unit_of_measurement = "Termine"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        calendar_id: str,
        calendar_name: str,
        member: str,
    ) -> None:
        super().__init__(coordinator)
        self._member = member
        self._attr_name = f"{calendar_name} – {member} (Anzahl)"
        self._attr_unique_id = (
            f"{DOMAIN}_{calendar_id}_count_{member.lower().replace(' ', '_')}"
        )

    @property
    def native_value(self) -> int:
        events = self.coordinator.data or []
        return sum(
            1 for e in events if parse_member_and_title(e)[0] == self._member
        )

    @property
    def extra_state_attributes(self) -> dict:
        return {"member": self._member}
