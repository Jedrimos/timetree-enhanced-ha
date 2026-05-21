"""Helpers for parsing TimeTree event data."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from homeassistant.components.calendar import CalendarEvent

from .const import DEFAULT_LABEL_NAMES, DISPLAY_SEPARATOR, HOLIDAY_LABEL_KEYWORDS, NO_MEMBER

_LOGGER = logging.getLogger(__name__)


def parse_member_and_title(event: dict[str, Any]) -> tuple[str, str]:
    """
    Return (member_name, display_title) for a raw TimeTree event dict.

    Strategy:
    1. "Name: rest" prefix in title → member=Name, display="Name · rest"
    2. Non-default label name → member=label_name, display="label_name · title"
    3. Fallback → member=NO_MEMBER, display=original title
    """
    raw_title = (event.get("title") or "").strip()

    if ":" in raw_title:
        colon_idx = raw_title.index(":")
        potential_name = raw_title[:colon_idx].strip()
        rest = raw_title[colon_idx + 1:].strip()

        if (
            potential_name
            and len(potential_name) <= 30
            and not potential_name[0].isdigit()
            and ":" not in potential_name
        ):
            display = (
                f"{potential_name}{DISPLAY_SEPARATOR}{rest}" if rest else potential_name
            )
            return potential_name, display

    label = event.get("label") or {}
    if isinstance(label, dict):
        label_name = (label.get("name") or "").strip()
    else:
        label_name = ""

    if label_name and label_name.lower() not in DEFAULT_LABEL_NAMES:
        display = (
            f"{label_name}{DISPLAY_SEPARATOR}{raw_title}" if raw_title else label_name
        )
        return label_name, display

    return NO_MEMBER, raw_title


def extract_unique_members(events: list[dict[str, Any]]) -> list[str]:
    """Return sorted list of unique member names found in events (NO_MEMBER excluded)."""
    members: set[str] = set()
    for event in events:
        member, _ = parse_member_and_title(event)
        if member != NO_MEMBER:
            members.add(member)
    return sorted(members)


def is_holiday(event: dict[str, Any]) -> bool:
    """Return True if the event looks like a public holiday based on its label."""
    label = event.get("label") or {}
    label_name = (label.get("name") or "").lower()
    return any(kw in label_name for kw in HOLIDAY_LABEL_KEYWORDS)


def event_to_calendar_event(
    event: dict[str, Any],
    display_title: str,
) -> CalendarEvent | None:
    """Convert a raw TimeTree event dict to a HA CalendarEvent."""
    try:
        uid = str(event.get("id", ""))
        all_day: bool = bool(event.get("all_day", False))

        start_raw = event.get("start_at") or event.get("start")
        end_raw = event.get("end_at") or event.get("end")

        if not start_raw or not end_raw:
            return None

        if all_day:
            start_dt = _parse_date(start_raw)
            end_dt = _parse_date(end_raw)
            if start_dt is None or end_dt is None:
                return None

            # Normalise: TimeTree may return datetime objects for all_day events
            if isinstance(start_dt, datetime):
                start_dt = start_dt.date()
            if isinstance(end_dt, datetime):
                end_dt = end_dt.date()

            # HA expects exclusive end; if equal add one day
            if end_dt <= start_dt:
                end_dt = start_dt + timedelta(days=1)

            return CalendarEvent(
                summary=display_title or "(Kein Titel)",
                start=start_dt,
                end=end_dt,
                description=event.get("description") or None,
                location=event.get("location") or None,
                uid=uid,
            )
        else:
            start_dt = _parse_datetime(start_raw)
            end_dt = _parse_datetime(end_raw)
            if start_dt is None or end_dt is None:
                return None
            if end_dt <= start_dt:
                end_dt = start_dt + timedelta(minutes=30)
            return CalendarEvent(
                summary=display_title or "(Kein Titel)",
                start=start_dt,
                end=end_dt,
                description=event.get("description") or None,
                location=event.get("location") or None,
                uid=uid,
            )

    except Exception:  # noqa: BLE001
        _LOGGER.exception("Failed to parse event %s", event.get("id"))
        return None


def _parse_datetime(raw: str) -> datetime | None:
    """Parse ISO-8601 datetime string → timezone-aware datetime (UTC)."""
    try:
        raw = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        _LOGGER.debug("Cannot parse datetime: %r", raw)
        return None


def _parse_date(raw: str) -> date | None:
    """Parse date-only string (YYYY-MM-DD) or full ISO datetime → date."""
    try:
        if "T" in raw or " " in raw:
            dt = _parse_datetime(raw)
            return dt.date() if dt else None
        return date.fromisoformat(raw[:10])
    except (ValueError, AttributeError):
        _LOGGER.debug("Cannot parse date: %r", raw)
        return None
