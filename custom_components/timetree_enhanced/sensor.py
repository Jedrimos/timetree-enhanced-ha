"""Sensor platform for TimeTree Enhanced.

Provides:
  • a timestamp sensor with the time of the last successful sync
  • one sensor per label with the number of upcoming events
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TimeTreeConfigEntry
from .const import DOMAIN
from .coordinator import TimeTreeCoordinator
from .entity import TimeTreeEntity, slug
from .events import label_name


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TimeTreeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    entities: list[SensorEntity] = [TimeTreeLastSyncSensor(coordinator)]
    entities.extend(
        TimeTreeMemberCountSensor(coordinator, m["name"], m["label_name"])
        for m in entry.runtime_data.members
    )
    async_add_entities(entities)


class TimeTreeLastSyncSensor(TimeTreeEntity, SensorEntity):
    """Timestamp of the last successful TimeTree sync."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "last_sync"

    def __init__(self, coordinator: TimeTreeCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.calendar_id}_last_updated"

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.last_sync


class TimeTreeMemberCountSensor(TimeTreeEntity, SensorEntity):
    """Number of upcoming events for one label."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "event_count"

    def __init__(
        self, coordinator: TimeTreeCoordinator, member_name: str, member_label: str
    ) -> None:
        super().__init__(coordinator)
        self._member_name = member_name
        self._member_label = member_label
        self._attr_translation_placeholders = {"member": member_name}
        self._attr_unique_id = f"{DOMAIN}_{coordinator.calendar_id}_count_{slug(member_name)}"

    @property
    def native_value(self) -> int:
        wanted = self._member_label.lower()
        return sum(1 for ev in self.coordinator.data or [] if label_name(ev).lower() == wanted)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"member": self._member_name, "member_label": self._member_label}
