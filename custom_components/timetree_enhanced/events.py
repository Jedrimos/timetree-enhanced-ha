"""Pure helpers for TimeTree event data: parsing, recurrence expansion, filtering.

This module deliberately has no Home Assistant imports so it can be unit-tested
in isolation.

TimeTree data model (internal web API, reverse engineered):

* ``start_at`` / ``end_at`` are Unix timestamps in milliseconds (UTC).
* Timed events carry ``start_timezone`` / ``end_timezone`` (IANA names).
* All-day events are stored at midnight UTC and their ``end_at`` is the *last*
  day of the event (inclusive), unlike iCal/Home Assistant which use an
  exclusive end date.
* Recurring series store RFC 5545 lines in ``recurrences``, e.g.
  ``["RRULE:FREQ=WEEKLY;BYDAY=MO,WE", "EXDATE:20261021T060000Z"]``.
  Deleting one occurrence adds an EXDATE; editing one occurrence adds an
  EXDATE and creates a separate event (``parent_id`` = series uuid), so the
  edited copy shows up as a normal event and needs no special handling.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, tzinfo
import logging
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr

_LOGGER = logging.getLogger(__name__)

# Hard cap so a pathological rule (e.g. FREQ=DAILY over a huge window) can
# never stall the event loop.
MAX_OCCURRENCES_PER_EVENT = 1000

_UNTIL_RE = re.compile(r"UNTIL=([0-9T]+Z?)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Basic parsing
# ---------------------------------------------------------------------------


def get_tz(name: str | None, fallback: str | None = None) -> tzinfo:
    """Return a ZoneInfo for ``name`` (or ``fallback``), defaulting to UTC."""
    for candidate in (name, fallback):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            continue
    return UTC


def parse_dt(value: Any) -> datetime | None:
    """Parse a Unix timestamp (ms or s) or ISO string into an aware UTC datetime."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            secs = value / 1000 if abs(value) > 1e10 else value
            return datetime.fromtimestamp(secs, tz=UTC)
        raw = str(value).strip()
        if raw.lstrip("-").isdigit():
            return parse_dt(int(raw))
        if len(raw) == 10 and "T" not in raw:
            return datetime.fromisoformat(raw).replace(tzinfo=UTC)
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, OverflowError, OSError):
        return None


def event_start_raw(ev: dict[str, Any]) -> Any:
    """Return the raw start value of an event (TimeTree uses start_at)."""
    for key in ("start_at", "dt_start", "start"):
        if ev.get(key) not in (None, ""):
            return ev[key]
    return None


def event_end_raw(ev: dict[str, Any]) -> Any:
    """Return the raw end value of an event, falling back to the start."""
    for key in ("end_at", "dt_end", "end"):
        if ev.get(key) not in (None, ""):
            return ev[key]
    return event_start_raw(ev)


def is_all_day(ev: dict[str, Any]) -> bool:
    """Return True for all-day events."""
    return bool(ev.get("all_day", False))


def event_bounds(ev: dict[str, Any]) -> tuple[date, date] | tuple[datetime, datetime] | None:
    """Return (start, end) of an event in Home Assistant semantics.

    * All-day events → ``(date, date)`` with an *exclusive* end date.
    * Timed events → aware UTC ``(datetime, datetime)``; end is forced to be
      after start (30 minutes minimum).
    """
    start = parse_dt(event_start_raw(ev))
    end = parse_dt(event_end_raw(ev))
    if start is None:
        return None
    if end is None:
        end = start

    if is_all_day(ev):
        # All-day events are stored at midnight UTC → use UTC dates.
        start_d = start.astimezone(UTC).date()
        end_d = end.astimezone(UTC).date()
        # TimeTree end is inclusive → +1 day for the exclusive HA end.
        end_d = max(end_d, start_d) + timedelta(days=1)
        return start_d, end_d

    if end <= start:
        end = start + timedelta(minutes=30)
    return start, end


def bounds_as_utc(bounds: tuple[Any, Any]) -> tuple[datetime, datetime]:
    """Convert event bounds (dates or datetimes) into aware UTC datetimes."""
    return as_utc_datetime(bounds[0]), as_utc_datetime(bounds[1])


def as_utc_datetime(value: date | datetime) -> datetime:
    """Return an aware datetime for a date (midnight UTC) or datetime."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.combine(value, time.min, tzinfo=UTC)


def label_name(ev: dict[str, Any]) -> str:
    """Return the label name of a raw TimeTree event, or an empty string."""
    label = ev.get("label")
    if isinstance(label, dict):
        return str(label.get("name") or "").strip()
    return ""


# ---------------------------------------------------------------------------
# Recurrence expansion
# ---------------------------------------------------------------------------


def _recurrence_lines(ev: dict[str, Any]) -> list[str]:
    raw = ev.get("recurrences") or ev.get("recurrence") or ev.get("rrule") or []
    if isinstance(raw, str):
        raw = raw.splitlines() or [raw]
    lines: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        item = item.strip()
        if not item:
            continue
        # A bare rule without the "RRULE:" prefix is accepted too.
        if ":" not in item.split(";")[0] and item.upper().startswith("FREQ="):
            item = f"RRULE:{item}"
        lines.append(item)
    return lines


def _localize(naive: datetime, tz: tzinfo) -> datetime:
    """Attach ``tz`` to a naive local wall-clock time."""
    return naive.replace(tzinfo=tz)


def _to_local_naive(dt: datetime, tz: tzinfo) -> datetime:
    return dt.astimezone(tz).replace(tzinfo=None)


def _normalise_rrule(value: str, tz: tzinfo) -> str:
    """Rewrite UNTIL into a naive local value so it matches a naive DTSTART."""

    def _repl(match: re.Match[str]) -> str:
        until = match.group(1).upper()
        try:
            if "T" not in until:
                # Date-only UNTIL = last local day of the series (inclusive).
                day = datetime.strptime(until[:8], "%Y%m%d")
                local = day.replace(hour=23, minute=59, second=59)
            elif until.endswith("Z"):
                utc = datetime.strptime(until[:15], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
                local = _to_local_naive(utc, tz)
            else:
                local = datetime.strptime(until[:15], "%Y%m%dT%H%M%S")
        except ValueError:
            return match.group(0)
        return f"UNTIL={local.strftime('%Y%m%dT%H%M%S')}"

    return _UNTIL_RE.sub(_repl, value)


def _parse_exdates(line: str, tz: tzinfo) -> tuple[set[datetime], set[date]]:
    """Parse one EXDATE line into excluded UTC instants and excluded local dates."""
    instants: set[datetime] = set()
    dates: set[date] = set()
    head, _, values = line.partition(":")
    params = {}
    for part in head.split(";")[1:]:
        key, _, val = part.partition("=")
        params[key.upper()] = val
    line_tz = get_tz(params.get("TZID")) if params.get("TZID") else tz
    date_only = params.get("VALUE", "").upper() == "DATE"

    for item in values.split(","):
        item = item.strip().upper()
        if not item:
            continue
        try:
            if date_only or "T" not in item:
                dates.add(datetime.strptime(item[:8], "%Y%m%d").date())
            elif item.endswith("Z"):
                instants.add(datetime.strptime(item[:15], "%Y%m%dT%H%M%S").replace(tzinfo=UTC))
            else:
                local = datetime.strptime(item[:15], "%Y%m%dT%H%M%S")
                instants.add(_localize(local, line_tz).astimezone(UTC))
        except ValueError:
            _LOGGER.debug("TimeTree Enhanced: cannot parse EXDATE value %r", item)
    return instants, dates


def _with_bounds(ev: dict[str, Any], start: datetime, end: datetime) -> dict[str, Any]:
    occ = {
        **ev,
        "start_at": int(start.timestamp() * 1000),
        "end_at": int(end.timestamp() * 1000),
    }
    return occ


def expand_event(
    ev: dict[str, Any],
    window_start: datetime,
    window_end: datetime,
    default_tz: str | None = None,
) -> list[dict[str, Any]]:
    """Return the occurrences of ``ev`` that overlap ``[window_start, window_end]``.

    Non-recurring events are returned unchanged (range filtering happens in
    :func:`filter_events_in_range`). Recurring events are expanded with
    ``dateutil.rrule`` in the event's own time zone so that a weekly 08:00
    appointment stays at 08:00 local time across DST changes. Every expanded
    occurrence gets ``recurrence_id`` set to its original start.
    """
    if ev.get("deleted_at") or ev.get("cancelled"):
        return []

    lines = _recurrence_lines(ev)
    rrule_lines = [ln for ln in lines if ln.upper().startswith("RRULE")]
    if not rrule_lines:
        return [ev]

    base_start = parse_dt(event_start_raw(ev))
    base_end = parse_dt(event_end_raw(ev))
    if base_start is None:
        return [ev]
    if base_end is None or base_end < base_start:
        base_end = base_start
    duration = base_end - base_start

    all_day = is_all_day(ev)
    # All-day events live at midnight UTC; timed events in their own zone.
    tz = UTC if all_day else get_tz(ev.get("start_timezone"), default_tz)

    rule_text = "\n".join(
        _normalise_rrule(ln if ":" in ln else f"RRULE:{ln}", tz) for ln in rrule_lines
    )
    excluded_instants: set[datetime] = set()
    excluded_dates: set[date] = set()
    for ln in lines:
        if ln.upper().startswith("EXDATE"):
            inst, dts = _parse_exdates(ln, tz)
            excluded_instants |= inst
            excluded_dates |= dts

    dtstart_local = _to_local_naive(base_start, tz)
    try:
        rule = rrulestr(rule_text, dtstart=dtstart_local, forceset=True)
        # For all-day events, an all-day occurrence that starts before the
        # window but is still running must be included → subtract duration.
        search_from = _to_local_naive(window_start - duration, tz) - timedelta(seconds=1)
        search_to = _to_local_naive(window_end, tz)
        starts = rule.between(search_from, search_to, inc=True)
    except (ValueError, TypeError, OverflowError) as err:
        _LOGGER.debug(
            "TimeTree Enhanced: cannot expand recurrence %r of event %s: %s",
            lines,
            ev.get("id") or ev.get("uuid"),
            err,
        )
        return [ev]

    result: list[dict[str, Any]] = []
    for local_start in starts[:MAX_OCCURRENCES_PER_EVENT]:
        if local_start.date() in excluded_dates:
            continue
        occ_start = _localize(local_start, tz).astimezone(UTC)
        if occ_start.replace(microsecond=0) in excluded_instants:
            continue
        if all_day:
            occ_end = occ_start + duration
        else:
            # Keep the wall-clock duration stable across DST changes.
            occ_end = _localize(local_start + duration, tz).astimezone(UTC)
        if occ_end < window_start or occ_start > window_end:
            continue
        occ = _with_bounds(ev, occ_start, occ_end)
        occ["recurrence_id"] = (
            local_start.strftime("%Y%m%d") if all_day else occ_start.strftime("%Y%m%dT%H%M%SZ")
        )
        result.append(occ)

    if len(starts) > MAX_OCCURRENCES_PER_EVENT:
        _LOGGER.debug(
            "TimeTree Enhanced: truncated recurrence of event %s to %d occurrences",
            ev.get("id") or ev.get("uuid"),
            MAX_OCCURRENCES_PER_EVENT,
        )
    return result


def filter_events_in_range(
    events: list[dict[str, Any]], start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Return only events whose bounds overlap ``[start, end]``."""
    result = []
    for ev in events:
        bounds = event_bounds(ev)
        if bounds is None:
            continue
        ev_start, ev_end = bounds_as_utc(bounds)
        if ev_end > start and ev_start <= end:
            result.append(ev)
    return result


def active_labels(events: list[dict[str, Any]], now: datetime) -> list[dict[str, str]]:
    """Return the labels used by at least one event that has not ended yet.

    Each entry has ``name`` and ``color``; the list is sorted by name.
    """
    seen: dict[str, dict[str, str]] = {}
    for ev in events:
        name = label_name(ev)
        if not name or name.lower() in seen:
            continue
        bounds = event_bounds(ev)
        if bounds is None:
            continue
        _, ev_end = bounds_as_utc(bounds)
        if ev_end <= now:
            continue
        label = ev.get("label") or {}
        color = str(label.get("color") or label.get("color_name") or "").strip()
        seen[name.lower()] = {"name": name, "color": color}
    return sorted(seen.values(), key=lambda m: m["name"].lower())


def color_to_str(value: Any) -> str:
    """Normalise a TimeTree color (int or string) into a string like '#rrggbb'."""
    if isinstance(value, bool) or value in (None, ""):
        return ""
    if isinstance(value, int):
        return f"#{value & 0xFFFFFF:06x}"
    return str(value).strip()
