"""Async wrapper around the internal TimeTree web API (session based).

TimeTree's official developer API was shut down in 2023, so this integration
talks to the same endpoints the TimeTree web app uses. No developer token is
needed; the user signs in with e-mail and password.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import logging
import re
from typing import Any
import uuid

import aiohttp
from yarl import URL

from .events import color_to_str

_LOGGER = logging.getLogger(__name__)

WEB_URL = "https://timetreeapp.com"
BASE_URL = f"{WEB_URL}/api/v1"
SESSION_COOKIE = "_session_id"

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

# Headers that mimic the TimeTree web app — Origin is required by the server.
_SESSION_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "X-Timetreea": "web/2.1.0/en",
    "Origin": WEB_URL,
}

_CSRF_META_RE = re.compile(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', re.I)

# TimeTree error codes seen in practice
ERR_CSRF_REJECTED = -1
ERR_SESSION_REJECTED = -493
ERR_RATE_LIMITED = -495
ERR_INVALID_CREDENTIALS = -702

# Upper bound for sync pagination (each chunk holds a few hundred events).
MAX_SYNC_CHUNKS = 100


class TimeTreeError(Exception):
    """Base class for TimeTree errors."""


class TimeTreeAuthError(TimeTreeError):
    """Authentication failed or the session expired."""


class TimeTreeInvalidCredentials(TimeTreeAuthError):
    """The e-mail/password combination was rejected."""


class TimeTreeAPIError(TimeTreeError):
    """General API / network error."""


class TimeTreeRateLimited(TimeTreeAPIError):
    """TimeTree rate-limited the request."""


def _error_code(body: Any) -> int | None:
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            code = err.get("code")
            if isinstance(code, int):
                return code
        code = body.get("code")
        if isinstance(code, int):
            return code
    return None


class TimeTreeAPI:
    """Minimal async client for the internal TimeTree web API."""

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        # A dedicated session with an unsafe cookie jar is used so the
        # _session_id cookie is stored and sent automatically.
        self._owns_session = session is None
        self._session = session or aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(unsafe=True),
            headers=_SESSION_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        self._csrf_token: str | None = None

    async def close(self) -> None:
        """Close the underlying HTTP session (only if we created it)."""
        if self._owns_session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Session handling
    # ------------------------------------------------------------------

    @property
    def session_id(self) -> str | None:
        """Return the current session cookie value, if any."""
        cookie = self._session.cookie_jar.filter_cookies(URL(BASE_URL)).get(SESSION_COOKIE)
        return cookie.value if cookie else None

    @property
    def is_authenticated(self) -> bool:
        return self.session_id is not None

    def restore_session(self, session_id: str) -> None:
        """Re-use a session cookie persisted from a previous run."""
        self._session.cookie_jar.update_cookies(
            {SESSION_COOKIE: session_id}, response_url=URL(BASE_URL)
        )

    def invalidate_session(self) -> None:
        self._session.cookie_jar.clear()
        self._csrf_token = None

    async def login(self, email: str, password: str) -> None:
        """Sign in and obtain a session cookie."""
        self.invalidate_session()
        try:
            async with self._session.put(
                f"{BASE_URL}/auth/email/signin",
                headers={"Content-Type": "application/json", "Referer": f"{WEB_URL}/signin"},
                json={"uid": email, "password": password, "uuid": uuid.uuid4().hex},
            ) as resp:
                try:
                    data = await resp.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    data = {}
                code = _error_code(data)
                _LOGGER.debug("TimeTree Enhanced: login HTTP %s (error code %s)", resp.status, code)
                if resp.status in (401, 403) or code == ERR_INVALID_CREDENTIALS:
                    raise TimeTreeInvalidCredentials("Invalid e-mail or password")
                if resp.status == 429 or code == ERR_RATE_LIMITED:
                    raise TimeTreeRateLimited("Login rate-limited by TimeTree")
                if resp.status != 200:
                    raise TimeTreeAPIError(f"Login returned HTTP {resp.status} (code {code})")
        except (TimeoutError, aiohttp.ClientError) as err:
            raise TimeTreeAPIError(f"Network error during login: {err}") from err

        if not self.is_authenticated:
            raise TimeTreeAuthError("No session cookie in login response")
        _LOGGER.debug("TimeTree Enhanced: login successful")

    async def _fetch_csrf_token(self) -> str:
        """Fetch the CSRF token the web app embeds in its HTML (needed for writes)."""
        try:
            async with self._session.get(WEB_URL, headers={"Accept": "text/html"}) as resp:
                html = await resp.text()
        except (TimeoutError, aiohttp.ClientError) as err:
            raise TimeTreeAPIError(f"Network error fetching CSRF token: {err}") from err
        match = _CSRF_META_RE.search(html)
        if not match:
            raise TimeTreeAPIError("Could not find CSRF token on timetreeapp.com")
        self._csrf_token = match.group(1)
        return self._csrf_token

    # ------------------------------------------------------------------
    # Low-level request
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        csrf: bool = False,
        _retry_csrf: bool = True,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if json is not None:
            headers["Content-Type"] = "application/json"
        if csrf:
            headers["X-CSRF-Token"] = self._csrf_token or await self._fetch_csrf_token()

        try:
            async with self._session.request(
                method, f"{BASE_URL}/{path}", params=params, json=json, headers=headers
            ) as resp:
                if new_token := resp.headers.get("x-csrf-token"):
                    self._csrf_token = new_token
                if 200 <= resp.status < 300:
                    if resp.status == 204:
                        return {}
                    data = await resp.json(content_type=None)
                    return data if isinstance(data, dict) else {"data": data}

                try:
                    body: Any = await resp.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    body = await resp.text()
                code = _error_code(body)
                _LOGGER.debug(
                    "TimeTree Enhanced: %s /%s HTTP %s — %.300s",
                    method,
                    path,
                    resp.status,
                    body,
                )
        except (TimeoutError, aiohttp.ClientError) as err:
            raise TimeTreeAPIError(f"Network error on {method} /{path}: {err}") from err

        if resp.status == 401 or code == ERR_SESSION_REJECTED:
            raise TimeTreeAuthError(f"Session expired (HTTP {resp.status}, code {code})")
        if csrf and _retry_csrf and (resp.status in (403, 422) or code == ERR_CSRF_REJECTED):
            # Stale CSRF token → fetch a fresh one and retry once.
            self._csrf_token = None
            return await self._request(
                method, path, params=params, json=json, csrf=True, _retry_csrf=False
            )
        if resp.status == 429 or code == ERR_RATE_LIMITED:
            raise TimeTreeRateLimited(f"{method} /{path} rate-limited by TimeTree")
        raise TimeTreeAPIError(f"{method} /{path} returned HTTP {resp.status}: {str(body)[:200]}")

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    async def get_calendars(self) -> list[dict[str, str]]:
        """Return the calendars the user has access to as ``[{id, name}]``."""
        data = await self._request("GET", "calendars", params={"since": 0})
        raw = data.get("calendars") or data.get("data") or []
        calendars: list[dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            if item.get("deactivated_at"):
                continue
            attrs = item.get("attributes") or item
            cal_id = str(item.get("id") or "")
            if cal_id:
                calendars.append({"id": cal_id, "name": str(attrs.get("name") or cal_id)})
        _LOGGER.debug("TimeTree Enhanced: found %d calendars", len(calendars))
        return calendars

    async def get_calendar_labels(self, calendar_id: str) -> list[dict[str, Any]]:
        """Return the labels of a calendar as ``[{id, name, color}]``."""
        data = await self._request("GET", f"calendar/{calendar_id}/labels")
        result: list[dict[str, Any]] = []
        for lbl in data.get("calendar_labels") or []:
            if not isinstance(lbl, dict):
                continue
            attrs = lbl.get("attributes") or lbl
            name = str(attrs.get("name") or "").strip()
            if not name:
                continue
            result.append(
                {
                    "id": lbl.get("id", attrs.get("id")),
                    "name": name,
                    "color": color_to_str(attrs.get("color") or attrs.get("color_name")),
                }
            )
        return result

    async def get_all_events_sync(self, calendar_id: str) -> list[dict[str, Any]]:
        """Fetch all events (incl. recurring series) via the paginated sync endpoint."""
        events: list[dict[str, Any]] = []
        since: int = 0  # since=0 is mandatory on the first call
        for _ in range(MAX_SYNC_CHUNKS):
            data = await self._request(
                "GET", f"calendar/{calendar_id}/events/sync", params={"since": since}
            )
            events.extend(e for e in data.get("events") or [] if isinstance(e, dict))
            next_since = data.get("since")
            if not data.get("chunk") or not next_since or next_since == since:
                return events
            since = next_since
        _LOGGER.warning(
            "TimeTree Enhanced: calendar %s has more than %d sync chunks — "
            "some events may be missing",
            calendar_id,
            MAX_SYNC_CHUNKS,
        )
        return events

    async def create_event(
        self,
        calendar_id: str,
        title: str,
        start: datetime | date,
        end: datetime | date,
        *,
        all_day: bool = False,
        tz_name: str = "UTC",
        description: str = "",
        location: str = "",
        label_id: int | None = None,
    ) -> dict[str, Any]:
        """Create an event.

        ``start``/``end`` use Home Assistant semantics: for all-day events they
        are dates with an *exclusive* end; TimeTree expects midnight-UTC
        timestamps with an *inclusive* end, so the end is shifted back a day.
        """
        if all_day:
            start_d = start.date() if isinstance(start, datetime) else start
            end_d = end.date() if isinstance(end, datetime) else end
            last_day = max(start_d, end_d - timedelta(days=1))
            start_ms = _date_to_ms(start_d)
            end_ms = _date_to_ms(last_day)
            tz_name = "UTC"
        else:
            assert isinstance(start, datetime) and isinstance(end, datetime)
            start_ms = int(start.timestamp() * 1000)
            end_ms = int(end.timestamp() * 1000)

        payload: dict[str, Any] = {
            "title": title,
            "all_day": all_day,
            "start_at": start_ms,
            "start_timezone": tz_name,
            "end_at": end_ms,
            "end_timezone": tz_name,
            "category": 1,
            "note": description,
            "location": location,
            "attendees": [],
            "recurrences": [],
            "alerts": [],
            "file_uuids": [],
        }
        if label_id is not None:
            payload["label_id"] = label_id

        return await self._request("POST", f"calendar/{calendar_id}/event", json=payload, csrf=True)


def _date_to_ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)
