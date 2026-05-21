"""Async TimeTree APP-API wrapper (session-based, no developer token needed)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://timetreeapp.com/api/v1"

HEADERS_BASE = {
    "User-Agent": "TimeTree/7.21.1 (iPhone; iOS 17.5; Scale/3.00)",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "X-Requested-With": "XMLHttpRequest",
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

    async def login(self, email: str, password: str) -> None:
        """Obtain a session_id. Raises TimeTreeAuthError on failure."""
        try:
            resp = await self._session.post(
                f"{BASE_URL}/auth/sign_in",
                json={"email": email, "password": password},
                headers=HEADERS_BASE,
                allow_redirects=True,
            )
        except aiohttp.ClientError as err:
            _LOGGER.error("TimeTree Enhanced: network error during login: %s", err)
            raise TimeTreeAPIError(f"Network error during login: {err}") from err

        body = await resp.text()
        _LOGGER.debug(
            "TimeTree Enhanced: login response HTTP %s – %.300s", resp.status, body
        )

        if resp.status in (401, 403):
            raise TimeTreeAuthError("Invalid email or password")
        if resp.status == 422:
            raise TimeTreeAuthError(f"Login rejected (422): {body[:200]}")
        if resp.status != 200:
            raise TimeTreeAPIError(
                f"Login returned HTTP {resp.status}: {body[:200]}"
            )

        try:
            data = await resp.json(content_type=None)
        except Exception as err:
            _LOGGER.error(
                "TimeTree Enhanced: could not parse login JSON – %s – body: %.300s",
                err, body,
            )
            raise TimeTreeAPIError(f"Login response not valid JSON: {err}") from err

        # session_id may be top-level or nested under "user" / "data"
        self._session_id = (
            data.get("session_id")
            or (data.get("user") or {}).get("session_id")
            or (data.get("data") or {}).get("session_id")
        )
        if not self._session_id:
            _LOGGER.error(
                "TimeTree Enhanced: no session_id in login response – keys: %s",
                list(data.keys()),
            )
            raise TimeTreeAuthError("No session_id in login response")

        _LOGGER.debug("TimeTree Enhanced: login successful")

    async def get_calendars(self) -> list[dict[str, Any]]:
        """Return list of calendars the user has access to."""
        data = await self._get("calendars")
        return data.get("calendars", [])

    async def get_events_in_range(
        self,
        calendar_id: str,
        start: datetime,
        end: datetime,
        tz: str = "Europe/Berlin",
    ) -> list[dict[str, Any]]:
        """Fetch ALL events (incl. recurring) within an explicit date range."""
        params = {
            "start_at": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "end_at": end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "timezone": tz,
        }
        data = await self._get(f"calendars/{calendar_id}/events", params=params)
        return data.get("events") or data.get("data") or []

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

        def _fmt(dt: datetime) -> str:
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        payload: dict[str, Any] = {
            "title": title,
            "all_day": all_day,
            "start_at": _fmt(start),
            "end_at": _fmt(end),
            "description": description,
            "location": location,
        }
        if label_id is not None:
            payload["label_id"] = label_id

        return await self._post(f"calendars/{calendar_id}/events", payload)

    def _auth_headers(self) -> dict[str, str]:
        if not self._session_id:
            raise TimeTreeAuthError("Not logged in – call login() first")
        return {**HEADERS_BASE, "Session-Id": self._session_id}

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
            _LOGGER.debug("TimeTree Enhanced: GET /%s HTTP %s – %.200s", path, resp.status, body)
            raise TimeTreeAPIError(f"GET /{path} returned HTTP {resp.status}")

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
