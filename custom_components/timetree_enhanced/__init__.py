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
        """Fetch upcoming events from TimeTree."""
        events = await api.get_upcoming_events(calendar_id, days=fetch_days, tz=tz)
        _LOGGER.debug(
            "TimeTree Enhanced: %d events via upcoming_events for %s",
            len(events),
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
