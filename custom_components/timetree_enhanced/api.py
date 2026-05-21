"""Async TimeTree APP-API wrapper (session-based, no developer token needed)."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://timetreeapp.com/api/v1"

HEADERS_BASE = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "X-Timetreea": "web/2.1.0/en",
}


class TimeTreeAuthError(Exception):
    """Authentication failed."""


class TimeTreeAPIError(Exception):
    """General API error."""


class TimeTreeAPI:
    """Minimal async wrapper around the TimeTree internal APP API."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._session_id: str | None = None

    @property
    def is_authenticated(self) -> bool:
        return self._session_id is not None

    def invalidate_session(self) -> None:
        self._session_id = None

    async def login(self, email: str, password: str) -> None:
        """Obtain a session_id. Raises TimeTreeAuthError on failure."""
        device_uuid = uuid.uuid4().hex
        try:
            resp = await self._session.put(
                f"{BASE_URL}/auth/email/signin",
                json={"uid": email, "password": password, "uuid": device_uuid},
                headers=HEADERS_BASE,
                allow_redirects=True,
            )
            body = await resp.text()
        except aiohttp.ClientError as err:
            _LOGGER.error("TimeTree Enhanced: network error during login: %s", err)
            raise TimeTreeAPIError(f"Network error during login: {err}") from err

        _LOGGER.debug(
            "TimeTree Enhanced: login response HTTP %s – %.500s", resp.status, body
        )

        if resp.status in (401, 403):
            raise TimeTreeAuthError("Invalid email or password")
        if resp.status == 422:
            raise TimeTreeAuthError(f"Login rejected (422): {body[:200]}")
        if resp.status != 200:
            raise TimeTreeAPIError(
                f"Login returned HTTP {resp.status}: {body[:200]}"
            )

        # Session ID comes as a cookie
        self._session_id = resp.cookies.get("_session_id")
        if not self._session_id:
            # Fall back to JSON body for older server versions
            try:
                data = json.loads(body)
                self._session_id = (
                    data.get("session_id")
                    or (data.get("user") or {}).get("session_id")
                    or (data.get("data") or {}).get("session_id")
                )
            except Exception:
                pass

        if not self._session_id:
            _LOGGER.error(
                "TimeTree Enhanced: no session_id found in cookies or body – body: %.500s", body
            )
            raise TimeTreeAuthError("No session_id in login response")

        _LOGGER.debug("TimeTree Enhanced: login successful")

    async def get_calendars(self) -> list[dict[str, Any]]:
        """Return list of calendars the user has access to."""
        data = await self._get("calendars")
        _LOGGER.debug("TimeTree Enhanced: get_calendars response keys: %s", list(data.keys()))

        # Handle flat list, "calendars" key, or JSON:API "data" key
        if isinstance(data, list):
            raw = data
        else:
            raw = data.get("calendars") or data.get("data") or []

        calendars = []
        for item in raw:
            # JSON:API format: id at top level, name inside attributes
            if "attributes" in item:
                cal_id = str(item.get("id", ""))
                cal_name = item["attributes"].get("name", cal_id)
            else:
                cal_id = str(item.get("id", ""))
                cal_name = item.get("name", cal_id)
            if cal_id:
                calendars.append({"id": cal_id, "name": cal_name})

        _LOGGER.debug("TimeTree Enhanced: found %d calendars: %s", len(calendars), calendars)
        return calendars

    async def get_all_events_sync(
        self,
        calendar_id: str,
    ) -> list[dict[str, Any]]:
        """Fetch ALL events (incl. recurring) via the sync endpoint with chunking.

        Uses GET /calendar/{id}/events/sync (singular 'calendar') which returns
        events in pages identified by a 'since' cursor until chunk==False.
        """
        events: list[dict[str, Any]] = []
        since: str | None = None
        max_chunks = 20  # guard against infinite loops

        for _ in range(max_chunks):
            params: dict[str, str] = {}
            if since is not None:
                params["since"] = since

            data = await self._get(f"calendar/{calendar_id}/events/sync", params=params or None)
            chunk_events = data.get("events") or []
            events.extend(chunk_events)

            if not data.get("chunk"):
                break
            since = data.get("since")
            if not since:
                break

        return events

    async def get_calendar_members(self, calendar_id: str) -> list[dict[str, Any]]:
        """Return members of a shared calendar with their label info.

        Tries /calendars/{id}/members, then /calendars/{id} as fallback.
        Each returned dict has 'name' and 'label_name' keys.
        """
        for path in (f"calendars/{calendar_id}/members", f"calendars/{calendar_id}"):
            try:
                data = await self._get(path)
            except TimeTreeAPIError:
                continue

            cal = data.get("calendar") or data.get("data") or data
            members_raw = (
                data.get("members")
                or (cal.get("members") if isinstance(cal, dict) else None)
                or []
            )

            if not members_raw:
                continue

            result: list[dict[str, Any]] = []
            for m in members_raw:
                attrs = m.get("attributes") or m
                name = (attrs.get("name") or attrs.get("display_name") or "").strip()
                label = attrs.get("label") or {}
                label_name = (
                    (label.get("name") or "").strip()
                    if isinstance(label, dict)
                    else ""
                )
                if name:
                    result.append({"name": name, "label_name": label_name or name})

            if result:
                _LOGGER.debug(
                    "TimeTree Enhanced: found %d members via %s: %s", len(result), path, result
                )
                return result

        _LOGGER.debug("TimeTree Enhanced: no member list found for calendar %s", calendar_id)
        return []

    async def get_upcoming_events(
        self,
        calendar_id: str,
        *,
        days: int = 60,
        tz: str = "Europe/Berlin",
    ) -> list[dict[str, Any]]:
        """Return upcoming events (fallback – does not include recurring events)."""
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

        def _fmt_dt(dt: datetime) -> str:
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        def _fmt_date(dt: datetime) -> str:
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")

        # TimeTree expects plain date strings (YYYY-MM-DD) for all-day events
        fmt = _fmt_date if all_day else _fmt_dt

        payload: dict[str, Any] = {
            "title": title,
            "all_day": all_day,
            "start_at": fmt(start),
            "end_at": fmt(end),
            "description": description,
            "location": location,
        }
        if label_id is not None:
            payload["label_id"] = label_id

        return await self._post(f"calendars/{calendar_id}/events", payload)

    def _auth_headers(self) -> dict[str, str]:
        if not self._session_id:
            raise TimeTreeAuthError("Not logged in – call login() first")
        return {**HEADERS_BASE, "Cookie": f"_session_id={self._session_id}"}

    async def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        try:
            resp = await self._session.get(
                f"{BASE_URL}/{path}",
                headers=self._auth_headers(),
                params=params,
            )
        except aiohttp.ClientError as err:
            raise TimeTreeAPIError(f"Network error: {err}") from err

        if resp.status == 401:
            raise TimeTreeAuthError("Session expired")
        if resp.status != 200:
            body = await resp.text()
            _LOGGER.debug("TimeTree Enhanced: GET /%s HTTP %s – %.500s", path, resp.status, body)
            raise TimeTreeAPIError(f"GET /{path} returned HTTP {resp.status}: {body[:200]}")

        return await resp.json(content_type=None)

    async def _post(self, path: str, payload: dict) -> dict[str, Any]:
        try:
            resp = await self._session.post(
                f"{BASE_URL}/{path}",
                json=payload,
                headers=self._auth_headers(),
            )
        except aiohttp.ClientError as err:
            raise TimeTreeAPIError(f"Network error: {err}") from err

        if resp.status == 401:
            raise TimeTreeAuthError("Session expired")
        if resp.status not in (200, 201):
            body = await resp.text()
            _LOGGER.debug("TimeTree Enhanced: POST /%s HTTP %s – %.200s", path, resp.status, body)
            raise TimeTreeAPIError(
                f"POST /{path} returned HTTP {resp.status}: {body}"
            )

        return await resp.json(content_type=None)
