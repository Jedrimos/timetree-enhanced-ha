"""TimeTree Enhanced – Home Assistant integration setup."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TimeTreeAPI, TimeTreeAPIError, TimeTreeAuthError
from .const import (
    CONF_CALENDAR_ID,
    CONF_EMAIL,
    CONF_FETCH_DAYS,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_TIMEZONE,
    DEFAULT_FETCH_DAYS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEZONE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CALENDAR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up TimeTree Enhanced from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    session = async_get_clientsession(hass)
    api = TimeTreeAPI(session)

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

    # Persist session_id across HA restarts to avoid login rate-limiting (HTTP 429)
    store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}_session")
    stored = await store.async_load() or {}
    if stored.get("session_id"):
        api._session_id = stored["session_id"]
        _LOGGER.debug("TimeTree Enhanced: restored session_id from storage")

    # Mutable container so _fetch() can write the timestamp and sensors can read it
    last_sync: dict[str, datetime | None] = {"time": None}

    async def _get_events() -> list[dict]:
        """Fetch events via sync endpoint (incl. recurring), fall back to upcoming_events."""
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=14)
        end = now + timedelta(days=fetch_days)

        try:
            all_events = await api.get_all_events_sync(calendar_id)
            # Filter locally to the desired window
            events = _filter_events_in_range(all_events, start, end)
            _LOGGER.debug(
                "TimeTree Enhanced: %d/%d events via sync endpoint for %s",
                len(events),
                len(all_events),
                calendar_id,
            )
            last_sync["time"] = datetime.now(timezone.utc)
            return events
        except TimeTreeAPIError as sync_err:
            _LOGGER.warning(
                "TimeTree Enhanced: sync endpoint failed (%s), falling back to upcoming_events",
                sync_err,
            )

        events = await api.get_upcoming_events(calendar_id, days=fetch_days, tz=tz)
        _LOGGER.debug(
            "TimeTree Enhanced: %d events via upcoming_events for %s",
            len(events),
            calendar_id,
        )
        last_sync["time"] = datetime.now(timezone.utc)
        return events

    async def _login_and_save() -> None:
        """Login and persist the new session_id."""
        await api.login(entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
        await store.async_save({"session_id": api._session_id})

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

    # Fetch the actual persons who are members of this shared calendar.
    # These become the basis for per-person calendar entities (not event labels).
    members: list[dict] = []
    try:
        members = await api.get_calendar_members(calendar_id)
    except Exception:
        _LOGGER.debug("TimeTree Enhanced: member fetch raised an exception")

    # Fallback: if the API didn't return member info, derive members from
    # the unique labels found in the current events window.
    if not members and coordinator.data:
        members = _members_from_event_labels(coordinator.data)
        if members:
            _LOGGER.info(
                "TimeTree Enhanced: derived %d members from event labels "
                "(API had no member data): %s",
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
        hass.data[DOMAIN].pop(entry.entry_id)
        # Keep stored session_id so the next load can reuse it without re-login
    return unload_ok


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


_CATEGORY_LABEL_KEYWORDS = {
    "geburtstag", "birthday", "urlaub", "vacation", "feiertag", "holiday",
    "arbeit", "work", "job", "bank", "schule", "school", "arzt", "doctor",
    "sport", "einkauf", "shopping", "termin", "appointment",
    "ebay", "amazon", "müll", "garbage", "sonstiges", "sonstige", "misc",
}


def _members_from_event_labels(events: list[dict]) -> list[dict]:
    """Derive a member list from the unique label names seen in events.

    Skips labels that look like categories (known keywords) rather than
    person names. Each returned dict has 'name', 'label_name', 'color'.
    """
    seen: dict[str, dict] = {}
    for ev in events:
        label = ev.get("label") or {}
        if not isinstance(label, dict):
            continue
        name = (label.get("name") or "").strip()
        if not name:
            continue
        if name.lower() in _CATEGORY_LABEL_KEYWORDS:
            continue
        # Skip labels with spaces that look like sentences (category phrases)
        if len(name.split()) > 2:
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
        return datetime.fromtimestamp(value, tz=timezone.utc)
    value = str(value)
    if len(value) == 10 and "T" not in value:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    value = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
