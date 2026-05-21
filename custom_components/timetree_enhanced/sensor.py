"""Sensor platform for TimeTree Enhanced.

Provides:
  • sensor.timetree_enhanced_<name>_zuletzt_synchronisiert  – last sync timestamp
  • sensor.timetree_enhanced_<name>_<member>_anzahl         – event count per member
"""
from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import CONF_CALENDAR_NAME, DOMAIN

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
    last_sync: dict = data["last_sync"]
    members: list[dict] = data.get("members", [])

    entities: list[SensorEntity] = [
        TimeTreeLastUpdatedSensor(coordinator, calendar_id, calendar_name, last_sync)
    ]

    for member in members:
        entities.append(
            TimeTreeMemberCountSensor(
                coordinator=coordinator,
                calendar_id=calendar_id,
                calendar_name=calendar_name,
                member_name=member["name"],
                member_label=member["label_name"],
            )
        )

    async_add_entities(entities, True)


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
        last_sync: dict,
    ) -> None:
        super().__init__(coordinator)
        self._last_sync = last_sync
        self._attr_name = f"{calendar_name} – Zuletzt synchronisiert"
        self._attr_unique_id = f"{DOMAIN}_{calendar_id}_last_updated"

    @property
    def native_value(self) -> datetime | None:
        return self._last_sync.get("time")


class TimeTreeMemberCountSensor(CoordinatorEntity, SensorEntity):
    """Number of upcoming events for one calendar member, matched by label name."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:calendar-account"
    _attr_native_unit_of_measurement = "Termine"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        calendar_id: str,
        calendar_name: str,
        member_name: str,
        member_label: str,
    ) -> None:
        super().__init__(coordinator)
        self._member_name = member_name
        self._member_label = member_label
        self._attr_name = f"{calendar_name} – {member_name} (Anzahl)"
        self._attr_unique_id = (
            f"{DOMAIN}_{calendar_id}_count_{member_name.lower().replace(' ', '_')}"
        )

    @property
    def native_value(self) -> int:
        events = self.coordinator.data or []
        return sum(
            1 for e in events
            if _event_label_name(e).lower() == self._member_label.lower()
        )

    @property
    def extra_state_attributes(self) -> dict:
        return {"member": self._member_name, "member_label": self._member_label}


def _event_label_name(event: dict) -> str:
    label = event.get("label") or {}
    if isinstance(label, dict):
        return (label.get("name") or "").strip()
    return ""
