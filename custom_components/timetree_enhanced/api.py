"""Async TimeTree APP-API wrapper (session-based, no developer token needed)."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://timetreeapp.com/api/v1"

# Headers that mimic the TimeTree web app — Origin is required by the server
_SESSION_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "X-Timetreea": "web/2.1.0/en",
    "Origin": "https://timetreeapp.com",
}

_LOGIN_HEADERS = {
    **_SESSION_HEADERS,
    "Content-Type": "application/json",
    "Referer": "https://timetreeapp.com/signin",
}


class TimeTreeAuthError(Exception):
    """Authentication failed."""


class TimeTreeAPIError(Exception):
    """General API error."""


def _ts_to_dt(ms: int | float, tz_name: str = "UTC") -> datetime:
    """Convert a Unix millisecond timestamp to a timezone-aware datetime."""
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError):
        tz = timezone.utc
    # Values > 1e10 are in milliseconds, smaller ones are seconds
    secs = ms / 1000 if ms > 1e10 else ms
    return datetime.fromtimestamp(secs, tz=tz)


class TimeTreeAPI:
    """Minimal async wrapper around the TimeTree internal APP API."""

    def __init__(self) -> None:
        # Dedicated session with unsafe cookie jar so _session_id is stored
        # and sent automatically — no manual Cookie header management needed.
        self._session = aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(unsafe=True),
            headers=_SESSION_HEADERS,
        )

    async def close(self) -> None:
        await self._session.close()

    @property
    def is_authenticated(self) -> bool:
        cookies = self._session.cookie_jar.filter_cookies(BASE_URL)
        return "_session_id" in cookies

    def invalidate_session(self) -> None:
        self._session.cookie_jar.clear()

    async def login(self, email: str, password: str) -> None:
        """Obtain a session_id. Raises TimeTreeAuthError on failure."""
        device_uuid = uuid.uuid4().hex
        try:
            async with self._session.put(
                f"{BASE_URL}/auth/email/signin",
                headers=_LOGIN_HEADERS,
                json={"uid": email, "password": password, "uuid": device_uuid},
            ) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = {}
                body_preview = str(data)[:300]

                _LOGGER.debug(
                    "TimeTree Enhanced: login HTTP %s — %s", resp.status, body_preview
                )

                if resp.status != 200:
                    code = (data.get("error") or {}).get("code")
                    if code == -702:
                        raise TimeTreeAuthError("Invalid email or password")
                    if code == -495:
                        raise TimeTreeAPIError(
                            f"Login returned HTTP {resp.status} (rate limited): {body_preview}"
                        )
                    raise TimeTreeAPIError(
                        f"Login returned HTTP {resp.status}: {body_preview}"
                    )

        except aiohttp.ClientError as err:
            raise TimeTreeAPIError(f"Network error during login: {err}") from err

        if not self.is_authenticated:
            raise TimeTreeAuthError("No session_id in login response")

        _LOGGER.debug("TimeTree Enhanced: login successful")

    async def get_calendars(self) -> list[dict[str, Any]]:
        """Return list of calendars the user has access to."""
        data = await self._get("calendars", params={"since": 0})
        _LOGGER.debug(
            "TimeTree Enhanced: get_calendars keys: %s  snippet: %.300s",
            list(data.keys()) if isinstance(data, dict) else type(data).__name__,
            str(data)[:300],
        )

        raw = data.get("calendars") or data.get("data") or (data if isinstance(data, list) else [])
        calendars = []
        for item in raw:
            attrs = item.get("attributes") or item
            cal_id = str(item.get("id", ""))
            cal_name = attrs.get("name", cal_id)
            if cal_id:
                calendars.append({"id": cal_id, "name": cal_name})

        _LOGGER.debug("TimeTree Enhanced: found %d calendars: %s", len(calendars), calendars)
        return calendars

    async def get_calendar_members(self, calendar_id: str) -> list[dict[str, Any]]:
        """Return members of a shared calendar with their label info."""
        paths = [
            f"calendars/{calendar_id}/members",
            f"calendars/{calendar_id}",
        ]
        for path in paths:
            try:
                data = await self._get(path, params={"since": 0})
            except TimeTreeAPIError as err:
                _LOGGER.debug("TimeTree Enhanced: members path /%s failed: %s", path, err)
                continue

            _LOGGER.debug(
                "TimeTree Enhanced: members /%s → keys=%s  snippet=%.400s",
                path,
                list(data.keys()) if isinstance(data, dict) else type(data).__name__,
                str(data)[:400],
            )

            cal = data.get("calendar") or data.get("data") or data
            members_raw = (
                data.get("members")
                or data.get("users")
                or (cal.get("members") if isinstance(cal, dict) else None)
                or (cal.get("users") if isinstance(cal, dict) else None)
                or []
            )

            if not members_raw:
                continue

            result: list[dict[str, Any]] = []
            for m in members_raw:
                attrs = m.get("attributes") or m
                name = (
                    attrs.get("name")
                    or attrs.get("display_name")
                    or attrs.get("username")
                    or ""
                ).strip()
                label = attrs.get("label") or {}
                if isinstance(label, dict):
                    label_name = (label.get("name") or "").strip()
                    color = (label.get("color") or label.get("color_name") or "").strip()
                else:
                    label_name = ""
                    color = ""
                if name:
                    result.append({
                        "name": name,
                        "label_name": label_name or name,
                        "color": color,
                    })

            if result:
                _LOGGER.info(
                    "TimeTree Enhanced: found %d members via /%s: %s",
                    len(result), path, [m["name"] for m in result],
                )
                return result

        _LOGGER.debug("TimeTree Enhanced: no member data found via API")
        return []

    async def get_calendar_labels(self, calendar_id: str) -> list[dict[str, Any]]:
        """Return labels defined for a calendar with name, id and hex color."""
        data = await self._get(f"calendar/{calendar_id}/labels")
        raw = data.get("calendar_labels") or []
        result: list[dict[str, Any]] = []
        for lbl in raw:
            attrs = lbl.get("attributes") or lbl
            name = (attrs.get("name") or "").strip()
            color_val = attrs.get("color") or attrs.get("color_name") or ""
            if isinstance(color_val, int):
                color = f"#{color_val:06x}"
            else:
                color = str(color_val).strip()
            label_id = lbl.get("id") or attrs.get("id")
            if name:
                result.append({"id": label_id, "name": name, "color": color})
        _LOGGER.debug(
            "TimeTree Enhanced: get_calendar_labels → %d labels: %s",
            len(result),
            [l["name"] for l in result],
        )
        return result

    async def get_all_events_sync(self, calendar_id: str) -> list[dict[str, Any]]:
        """Fetch ALL events (incl. recurring) via the sync endpoint with chunking."""
        events: list[dict[str, Any]] = []
        since: int | None = None
        max_chunks = 20

        for _ in range(max_chunks):
            params: dict = {}
            if since is not None:
                params["since"] = since

            data = await self._get(
                f"calendar/{calendar_id}/events/sync",
                params=params or None,
            )
            events.extend(data.get("events") or [])

            if not data.get("chunk"):
                break
            since = data.get("since")
            if not since:
                break

        return events

    async def get_upcoming_events(
        self,
        calendar_id: str,
        *,
        days: int = 60,
        tz: str = "Europe/Berlin",
    ) -> list[dict[str, Any]]:
        """Return upcoming events via the internal API."""
        data = await self._get(
            f"calendars/{calendar_id}/upcoming_events",
            params={"timezone": tz, "days": days},
        )
        return data.get("events", [])

    async def create_event(
        self,
        calendar_id: str,
        title: str,
        start: datetime,
        end: datetime,
        *,
        all_day: bool = False,
        description: str = "",
        location: str = "",
        label_id: int | None = None,
    ) -> dict[str, Any]:
        """Create an event in TimeTree. Returns the created event dict."""

        def _fmt_dt(dt: datetime) -> int:
            """Return Unix milliseconds."""
            return int(dt.astimezone(timezone.utc).timestamp() * 1000)

        def _fmt_date(dt: datetime) -> str:
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")

        payload: dict[str, Any] = {
            "title": title,
            "all_day": all_day,
            "start_at": _fmt_date(start) if all_day else _fmt_dt(start),
            "end_at": _fmt_date(end) if all_day else _fmt_dt(end),
            "note": description,
            "location": location,
            "uuid": uuid.uuid4().hex,
        }
        if label_id is not None:
            payload["label_id"] = label_id

        return await self._post(f"calendars/{calendar_id}/events", payload)

    async def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        try:
            async with self._session.get(
                f"{BASE_URL}/{path}",
                params=params,
            ) as resp:
                if resp.status == 401:
                    raise TimeTreeAuthError("Session expired")
                if resp.status != 200:
                    try:
                        body = await resp.json(content_type=None)
                    except Exception:
                        body = await resp.text()
                    _LOGGER.debug(
                        "TimeTree Enhanced: GET /%s HTTP %s — %.500s", path, resp.status, body
                    )
                    raise TimeTreeAPIError(
                        f"GET /{path} returned HTTP {resp.status}: {str(body)[:200]}"
                    )
                return await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise TimeTreeAPIError(f"Network error: {err}") from err

    async def _post(self, path: str, payload: dict) -> dict[str, Any]:
        try:
            async with self._session.post(
                f"{BASE_URL}/{path}",
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as resp:
                if resp.status == 401:
                    raise TimeTreeAuthError("Session expired")
                if resp.status not in (200, 201):
                    try:
                        body = await resp.json(content_type=None)
                    except Exception:
                        body = await resp.text()
                    raise TimeTreeAPIError(
                        f"POST /{path} returned HTTP {resp.status}: {str(body)[:200]}"
                    )
                return await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise TimeTreeAPIError(f"Network error: {err}") from err
