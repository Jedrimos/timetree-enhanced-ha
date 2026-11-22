"""Diagnostics support for TimeTree Enhanced."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import TimeTreeConfigEntry
from .const import CONF_EMAIL, CONF_PASSWORD

TO_REDACT = {CONF_EMAIL, CONF_PASSWORD, "title", "note", "location", "description"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TimeTreeConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry (credentials and event texts redacted)."""
    data = entry.runtime_data
    coordinator = data.coordinator
    events = coordinator.data or []
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "last_sync": coordinator.last_sync.isoformat() if coordinator.last_sync else None,
        "last_update_success": coordinator.last_update_success,
        "labels": coordinator.labels,
        "members": data.members,
        "event_count": len(events),
        "sample_events": async_redact_data(
            [
                {
                    k: ev.get(k)
                    for k in (
                        "title",
                        "all_day",
                        "start_at",
                        "end_at",
                        "start_timezone",
                        "label_id",
                        "recurrences",
                        "recurrence_id",
                        "parent_id",
                    )
                }
                for ev in events[:20]
            ],
            TO_REDACT,
        ),
    }
