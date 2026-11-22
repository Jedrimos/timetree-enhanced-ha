"""Tests for the pure event helpers (parsing + recurrence expansion)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.timetree_enhanced.events import (
    active_labels,
    color_to_str,
    event_bounds,
    expand_event,
    filter_events_in_range,
    parse_dt,
)

UTC = UTC
BERLIN = ZoneInfo("Europe/Berlin")


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def starts(occurrences: list[dict]) -> list[datetime]:
    return [parse_dt(o["start_at"]) for o in occurrences]


def test_parse_dt_variants() -> None:
    ref = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    assert parse_dt(ms(ref)) == ref
    assert parse_dt(ref.timestamp()) == ref
    assert parse_dt("2026-03-01T12:00:00Z") == ref
    assert parse_dt("2026-03-01") == datetime(2026, 3, 1, tzinfo=UTC)
    assert parse_dt(str(ms(ref))) == ref
    assert parse_dt(None) is None
    assert parse_dt("garbage") is None


def test_all_day_end_is_made_exclusive() -> None:
    ev = {
        "all_day": True,
        "start_at": ms(datetime(2026, 7, 1, tzinfo=UTC)),
        "end_at": ms(datetime(2026, 7, 3, tzinfo=UTC)),
    }
    assert event_bounds(ev) == (date(2026, 7, 1), date(2026, 7, 4))

    single = {**ev, "end_at": ev["start_at"]}
    assert event_bounds(single) == (date(2026, 7, 1), date(2026, 7, 2))


def test_timed_event_without_duration_gets_30_minutes() -> None:
    start = datetime(2026, 7, 1, 8, tzinfo=UTC)
    ev = {"start_at": ms(start), "end_at": ms(start)}
    assert event_bounds(ev) == (start, start + timedelta(minutes=30))


def test_non_recurring_event_is_returned_unchanged() -> None:
    ev = {"id": "1", "start_at": 0, "end_at": 0}
    assert expand_event(ev, datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)) == [
        ev
    ]


def test_deleted_event_is_dropped() -> None:
    ev = {"id": "1", "start_at": 0, "deleted_at": 123, "recurrences": ["RRULE:FREQ=DAILY"]}
    assert (
        expand_event(ev, datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)) == []
    )


def test_weekly_keeps_local_time_across_dst() -> None:
    # Monday 2026-03-16 08:00 Berlin (CET, UTC+1); DST starts 2026-03-29.
    start = datetime(2026, 3, 16, 8, 0, tzinfo=BERLIN)
    ev = {
        "id": "w",
        "start_at": ms(start),
        "end_at": ms(start + timedelta(hours=1)),
        "start_timezone": "Europe/Berlin",
        "recurrences": ["RRULE:FREQ=WEEKLY"],
    }
    occ = expand_event(ev, datetime(2026, 3, 20, tzinfo=UTC), datetime(2026, 4, 10, tzinfo=UTC))
    local = [s.astimezone(BERLIN) for s in starts(occ)]
    assert [d.day for d in local] == [23, 30, 6]
    assert all(d.hour == 8 for d in local)
    assert occ[0]["recurrence_id"] == "20260323T070000Z"
    assert occ[1]["recurrence_id"] == "20260330T060000Z"


def test_byday_multiple_weekdays() -> None:
    start = datetime(2026, 6, 1, 18, 0, tzinfo=BERLIN)  # Monday
    ev = {
        "start_at": ms(start),
        "end_at": ms(start + timedelta(hours=1)),
        "start_timezone": "Europe/Berlin",
        "recurrences": ["RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR"],
    }
    occ = expand_event(ev, datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 8, tzinfo=UTC))
    assert [s.astimezone(BERLIN).day for s in starts(occ)] == [1, 3, 5]


def test_exdate_removes_occurrence() -> None:
    start = datetime(2026, 6, 1, 18, 0, tzinfo=BERLIN)
    ev = {
        "start_at": ms(start),
        "end_at": ms(start + timedelta(hours=1)),
        "start_timezone": "Europe/Berlin",
        "recurrences": ["RRULE:FREQ=DAILY", "EXDATE:20260602T160000Z,20260604T160000Z"],
    }
    occ = expand_event(ev, datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 5, 23, tzinfo=UTC))
    assert [s.astimezone(BERLIN).day for s in starts(occ)] == [1, 3, 5]


def test_until_and_count_end_series() -> None:
    start = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
    base = {"start_at": ms(start), "end_at": ms(start + timedelta(hours=1))}
    window = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 3, 1, tzinfo=UTC))

    counted = expand_event({**base, "recurrences": ["RRULE:FREQ=WEEKLY;COUNT=3"]}, *window)
    assert len(counted) == 3

    until = expand_event({**base, "recurrences": ["RRULE:FREQ=WEEKLY;UNTIL=20260119"]}, *window)
    assert len(until) == 3  # date-only UNTIL is inclusive

    finished = expand_event(
        {**base, "recurrences": ["RRULE:FREQ=WEEKLY;UNTIL=20251231T000000Z"]}, *window
    )
    assert finished == []


def test_yearly_birthday_all_day() -> None:
    ev = {
        "all_day": True,
        "start_at": ms(datetime(1990, 2, 14, tzinfo=UTC)),
        "end_at": ms(datetime(1990, 2, 14, tzinfo=UTC)),
        "recurrences": ["RRULE:FREQ=YEARLY"],
    }
    occ = expand_event(ev, datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC))
    assert len(occ) == 1
    assert event_bounds(occ[0]) == (date(2026, 2, 14), date(2026, 2, 15))
    assert occ[0]["recurrence_id"] == "20260214"


def test_monthly_on_31st_skips_short_months() -> None:
    # RFC 5545: BYMONTHDAY=31 does not occur in months without a 31st.
    ev = {
        "all_day": True,
        "start_at": ms(datetime(2026, 1, 31, tzinfo=UTC)),
        "end_at": ms(datetime(2026, 1, 31, tzinfo=UTC)),
        "recurrences": ["RRULE:FREQ=MONTHLY"],
    }
    occ = expand_event(ev, datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC))
    assert [event_bounds(o)[0] for o in occ] == [
        date(2026, 1, 31),
        date(2026, 3, 31),
        date(2026, 5, 31),
    ]


def test_running_multi_day_occurrence_is_included() -> None:
    # Weekly 3-day all-day event that started before the window begins.
    ev = {
        "all_day": True,
        "start_at": ms(datetime(2026, 5, 4, tzinfo=UTC)),
        "end_at": ms(datetime(2026, 5, 6, tzinfo=UTC)),
        "recurrences": ["RRULE:FREQ=WEEKLY"],
    }
    occ = expand_event(ev, datetime(2026, 5, 12, tzinfo=UTC), datetime(2026, 5, 14, tzinfo=UTC))
    assert [event_bounds(o)[0] for o in occ] == [date(2026, 5, 11)]


def test_unparseable_rule_falls_back_to_raw_event() -> None:
    ev = {"start_at": 0, "recurrences": ["RRULE:FREQ=NONSENSE"]}
    assert expand_event(ev, datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)) == [
        ev
    ]


def test_filter_events_in_range() -> None:
    window = (datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 30, tzinfo=UTC))
    inside = {"start_at": ms(datetime(2026, 6, 10, tzinfo=UTC))}
    before = {"start_at": ms(datetime(2026, 5, 10, tzinfo=UTC))}
    no_start = {"title": "x"}
    assert filter_events_in_range([inside, before, no_start], *window) == [inside]


def test_active_labels() -> None:
    now = datetime(2026, 6, 1, 12, tzinfo=UTC)
    events = [
        {"start_at": ms(now + timedelta(days=1)), "label": {"name": "Elias", "color": "#ff0000"}},
        {"start_at": ms(now - timedelta(days=3)), "label": {"name": "Past"}},
        {"start_at": ms(now + timedelta(days=2)), "label": {"name": "elias"}},
        {"start_at": ms(now + timedelta(days=2)), "label": {"name": "Domi"}},
    ]
    assert active_labels(events, now) == [
        {"name": "Domi", "color": ""},
        {"name": "Elias", "color": "#ff0000"},
    ]


def test_color_to_str() -> None:
    assert color_to_str(0xFF8800) == "#ff8800"
    assert color_to_str("#abcdef") == "#abcdef"
    assert color_to_str(None) == ""
