"""TimeTree Enhanced – Home Assistant integration setup."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from yarl import URL

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BASE_URL, TimeTreeAPI, TimeTreeAPIError, TimeTreeAuthError
from .const import (
    CONF_CALENDAR_ID,
    CONF_EMAIL,
    CONF_FETCH_DAYS,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_TIMEZONE,
    DEFAULT_FETCH_DAYS,
    DEFAULT_LABEL_NAMES,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEZONE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CALENDAR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up TimeTree Enhanced from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    api = TimeTreeAPI()

    scan_interval: int = entry.options.get(
        CONF_SCAN_INTERVAL,
        entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )
    tz: str = entry.data.get(CONF_TIMEZONE, DEFAULT_TIMEZONE)
    fetch_days: int = entry.options.get(
        CONF_FETCH_DAYS,
        entry.data.get(CONF_FETCH_DAYS, DEFAULT_FETCH_DAYS),
    )
    calendar_id: str = entry.data[CONF_CALENDAR_ID]

    # Persist session cookie across HA restarts to avoid login rate-limiting (HTTP 429)
    store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}_session")
    stored = await store.async_load() or {}
    if stored.get("session_id"):
        api._session.cookie_jar.update_cookies(
            {"_session_id": stored["session_id"]},
            response_url=URL(BASE_URL),
        )
        _LOGGER.debug("TimeTree Enhanced: restored session cookie from storage")

    # Mutable container so _fetch() can write the timestamp and sensors can read it
    last_sync: dict[str, datetime | None] = {"time": None}

    async def _save_session() -> None:
        """Persist the current session cookie to storage."""
        cookies = api._session.cookie_jar.filter_cookies(URL(BASE_URL))
        cookie = cookies.get("_session_id")
        if cookie:
            await store.async_save({"session_id": cookie.value})

    async def _get_events() -> list[dict]:
        """Fetch events via the sync endpoint, enrich with label info, expand recurring."""
        all_events = await api.get_all_events_sync(calendar_id)

        # Build label_id → label dict mapping so events with label_id get a label object
        label_map: dict[int | str, dict] = {}
        try:
            labels = await api.get_calendar_labels(calendar_id)
            for lbl in labels:
                if lbl.get("id") is not None:
                    label_map[lbl["id"]] = {"name": lbl["name"], "color": lbl["color"]}
        except Exception:
            _LOGGER.debug("TimeTree Enhanced: could not fetch labels for event enrichment")

        now = datetime.now(timezone.utc)
        # Use start of today so multi-day events that began earlier today are included
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = today + timedelta(days=fetch_days)

        enriched: list[dict] = []
        for ev in all_events:
            # Inject label object when only label_id is present
            if not ev.get("label") and ev.get("label_id") is not None:
                lbl = label_map.get(ev["label_id"])
                if lbl:
                    ev = {**ev, "label": lbl}

            # Expand recurring events into individual occurrences within the window
            for occurrence in _expand_event(ev, today, end):
                enriched.append(occurrence)

        events = _filter_events_in_range(enriched, today, end)
        _LOGGER.debug(
            "TimeTree Enhanced: %d events in window (%d raw, %d after expansion) for %s",
            len(events),
            len(all_events),
            len(enriched),
            calendar_id,
        )
        last_sync["time"] = datetime.now(timezone.utc)
        return events

    async def _login_and_save() -> None:
        """Login and persist the new session cookie."""
        await api.login(entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
        await _save_session()

    async def _fetch() -> list[dict]:
        """Fetch events, logging in only when the session is missing or expired."""
        try:
            if not api.is_authenticated:
                await _login_and_save()

            try:
                return await _get_events()
            except TimeTreeAuthError:
                # Session expired – re-login once and retry
                _LOGGER.debug("TimeTree Enhanced: session expired, re-logging in")
                api.invalidate_session()
                await _login_and_save()
                return await _get_events()

        except TimeTreeAuthError as err:
            raise UpdateFailed(f"Auth-Fehler: {err}") from err
        except TimeTreeAPIError as err:
            raise UpdateFailed(f"API-Fehler: {err}") from err
        except Exception as err:
            _LOGGER.exception("TimeTree Enhanced: unexpected error during fetch")
            raise UpdateFailed(str(err)) from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_{calendar_id}",
        update_method=_fetch,
        update_interval=timedelta(minutes=scan_interval),
    )

    await coordinator.async_config_entry_first_refresh()

    # Member discovery: try three sources in order of reliability.
    members: list[dict] = []

    # 1. Calendar members endpoint (email-invited people)
    try:
        members = await api.get_calendar_members(calendar_id)
    except Exception:
        _LOGGER.debug("TimeTree Enhanced: member fetch raised an exception")

    # 2. Calendar labels endpoint — returns all defined labels with name & color
    if not members:
        try:
            labels = await api.get_calendar_labels(calendar_id)
            members = [
                {"name": lbl["name"], "label_name": lbl["name"], "color": lbl["color"]}
                for lbl in labels
                if lbl["name"].lower() not in DEFAULT_LABEL_NAMES
            ]
            if members:
                _LOGGER.info(
                    "TimeTree Enhanced: %d members from labels endpoint: %s",
                    len(members),
                    [m["name"] for m in members],
                )
        except Exception:
            _LOGGER.debug("TimeTree Enhanced: labels fetch raised an exception")

    # 3. Last resort: derive from unique labels on future events
    if not members and coordinator.data:
        members = _members_from_event_labels(coordinator.data)
        if members:
            _LOGGER.info(
                "TimeTree Enhanced: derived %d members from event labels "
                "(API had no label data): %s",
                len(members),
                [m["name"] for m in members],
            )

    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "api": api,
        "calendar_id": calendar_id,
        "last_sync": last_sync,
        "members": members,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await data["api"].close()
        # Keep stored session cookie so the next load can reuse it without re-login
    return unload_ok


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


def _expand_event(ev: dict, window_start: datetime, window_end: datetime) -> list[dict]:
    """Return occurrences of ev that fall inside [window_start, window_end].

    Non-recurring events return [ev].  Recurring events are expanded using a
    minimal RRULE parser that handles FREQ, INTERVAL, UNTIL, and COUNT so that
    finished series (with UNTIL= or COUNT=) are not resurrected in the future.
    Unknown FREQ values fall back to returning the raw event unchanged.
    """
    import re as _re

    # Skip deleted/cancelled events
    if ev.get("deleted_at") or ev.get("cancelled"):
        return []

    _rrule_val = ev.get("recurrences") or ev.get("recurrence") or ev.get("rrule") or ""
    if isinstance(_rrule_val, list):
        rrule_raw = " ".join(str(r) for r in _rrule_val).strip()
    else:
        rrule_raw = str(_rrule_val).strip()
    if not rrule_raw:
        return [ev]

    start_raw = ev.get("start_at") or ev.get("dt_start")
    end_raw = ev.get("end_at") or ev.get("dt_end") or start_raw
    if start_raw is None:
        return [ev]

    try:
        base_start = _parse_dt(start_raw)
        base_end = _parse_dt(end_raw)
        duration = base_end - base_start
    except Exception:
        return [ev]

    freq_match = _re.search(r"FREQ=(\w+)", rrule_raw, _re.IGNORECASE)
    if not freq_match:
        return [ev]
    freq = freq_match.group(1).upper()

    interval_match = _re.search(r"INTERVAL=(\d+)", rrule_raw, _re.IGNORECASE)
    interval = int(interval_match.group(1)) if interval_match else 1

    # UNTIL= → hard end date for the series
    until_dt: datetime | None = None
    until_match = _re.search(r"UNTIL=(\d{8}T\d{6}Z?|\d{8})", rrule_raw, _re.IGNORECASE)
    if until_match:
        us = until_match.group(1)
        try:
            if "T" in us:
                until_dt = datetime.strptime(us.rstrip("Z"), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
            else:
                until_dt = datetime.strptime(us, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    # Early exit: series ended before the window starts
    if until_dt and until_dt < window_start:
        return []

    # COUNT= → series has a fixed number of occurrences total
    count_match = _re.search(r"COUNT=(\d+)", rrule_raw, _re.IGNORECASE)
    max_count = int(count_match.group(1)) if count_match else None

    def _advance(cur: datetime, n: int = 1) -> datetime:
        """Advance cursor by n steps according to FREQ/INTERVAL."""
        steps = n * interval
        if freq == "YEARLY":
            return cur.replace(year=cur.year + steps)
        if freq == "MONTHLY":
            m = cur.month - 1 + steps
            return cur.replace(year=cur.year + m // 12, month=m % 12 + 1)
        if freq == "WEEKLY":
            return cur + timedelta(weeks=steps)
        if freq == "DAILY":
            return cur + timedelta(days=steps)
        raise ValueError(f"Unsupported FREQ: {freq}")

    if freq not in ("YEARLY", "MONTHLY", "WEEKLY", "DAILY"):
        return [ev]

    # --- Skip-ahead optimisation ---
    # Compute skip in STEPS (not raw time units) so _advance(cursor, skip)
    # doesn't double-multiply by interval (which caused the cursor to overshoot
    # the window for events with INTERVAL > 1, producing zero results).
    cursor = base_start
    occurrence_count = 0

    try:
        if freq == "YEARLY":
            skip = max(0, window_start.year - base_start.year - 1) // interval
        elif freq == "MONTHLY":
            delta_m = (window_start.year - base_start.year) * 12 + window_start.month - base_start.month
            skip = max(0, delta_m - 1) // interval
        elif freq == "WEEKLY":
            delta_w = max(0, (window_start - base_start).days // 7 - 1)
            skip = delta_w // interval
        else:  # DAILY
            delta_d = max(0, (window_start - base_start).days - 1)
            skip = delta_d // interval

        if skip > 0:
            cursor = _advance(cursor, skip)
            occurrence_count = skip

    except (ValueError, OverflowError):
        pass

    # --- Generate occurrences in/near window ---
    result: list[dict] = []
    max_iter = 500  # safety cap

    for _ in range(max_iter):
        if cursor > window_end:
            break
        if until_dt and cursor > until_dt:
            break
        occurrence_count += 1
        if max_count is not None and occurrence_count > max_count:
            break

        occ_end = cursor + duration
        if occ_end >= window_start:  # overlaps the window
            if isinstance(start_raw, (int, float)):
                new_start: int | str = int(cursor.timestamp() * 1000)
                new_end: int | str = int(occ_end.timestamp() * 1000)
            else:
                new_start = cursor.isoformat()
                new_end = occ_end.isoformat()
            result.append({**ev, "start_at": new_start, "end_at": new_end})

        try:
            cursor = _advance(cursor)
        except (ValueError, OverflowError):
            break

    # If the series produced no occurrences in the window, return nothing
    # (don't fall back to the base event which is likely years in the past)
    return result



def _members_from_event_labels(events: list[dict]) -> list[dict]:
    """Derive a member list from labels on UPCOMING events only.

    Only labels that appear on at least one future event are included.
    This ensures we don't create empty calendars for labels that only
    had past events.  Each returned dict has 'name', 'label_name', 'color'.
    """
    now_ts = datetime.now(timezone.utc).timestamp()
    seen: dict[str, dict] = {}

    for ev in events:
        # Only consider events that start in the future
        start_raw = ev.get("start_at") or ev.get("dt_start")
        if start_raw is None:
            continue
        try:
            if isinstance(start_raw, (int, float)):
                ev_start_ts = float(start_raw)
            else:
                val = str(start_raw).replace("Z", "+00:00")
                ev_start_ts = datetime.fromisoformat(val).timestamp()
            if ev_start_ts < now_ts:
                continue
        except Exception:
            continue

        label = ev.get("label") or {}
        if not isinstance(label, dict):
            continue
        name = (label.get("name") or "").strip()
        if not name:
            continue
        if name not in seen:
            color = (label.get("color") or label.get("color_name") or "").strip()
            seen[name] = {"name": name, "label_name": name, "color": color}

    return sorted(seen.values(), key=lambda m: m["name"])


def _filter_events_in_range(
    events: list[dict],
    start: datetime,
    end: datetime,
) -> list[dict]:
    """Return only events that overlap the [start, end] window."""
    result = []
    for ev in events:
        ev_start_raw = ev.get("start_at") or ev.get("dt_start")
        ev_end_raw = ev.get("end_at") or ev.get("dt_end") or ev_start_raw
        if ev_start_raw is None:
            continue  # skip events with no timestamp
        try:
            ev_start = _parse_dt(ev_start_raw)
            ev_end = _parse_dt(ev_end_raw)
            if ev_end >= start and ev_start <= end:
                result.append(ev)
        except Exception:
            continue  # skip unparseable events rather than keeping them
    return result


def _parse_dt(value: str | int | float) -> datetime:
    """Parse ISO datetime string or Unix timestamp to a UTC-aware datetime."""
    if isinstance(value, (int, float)):
        secs = value / 1000 if value > 1e10 else value
        return datetime.fromtimestamp(secs, tz=timezone.utc)
    value = str(value)
    if len(value) == 10 and "T" not in value:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    value = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
