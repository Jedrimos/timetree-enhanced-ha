"""Config flow, setup, entities and event creation (TimeTree API mocked)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.timetree_enhanced.api import (
    TimeTreeAPIError,
    TimeTreeInvalidCredentials,
)
from custom_components.timetree_enhanced.const import (
    CONF_CALENDAR_ID,
    CONF_CALENDAR_NAME,
    CONF_EMAIL,
    CONF_FETCH_DAYS,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_TIMEZONE,
    DOMAIN,
)

API = "custom_components.timetree_enhanced.api.TimeTreeAPI"

ENTRY_DATA = {
    CONF_EMAIL: "user@example.com",
    CONF_PASSWORD: "secret",
    CONF_CALENDAR_ID: "123",
    CONF_CALENDAR_NAME: "Familie",
    CONF_TIMEZONE: "Europe/Berlin",
    CONF_FETCH_DAYS: 60,
    CONF_SCAN_INTERVAL: 60,
}


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _events() -> list[dict]:
    now = datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    return [
        {
            "id": "e1",
            "title": "Krippe",
            "start_at": _ms(tomorrow),
            "end_at": _ms(tomorrow + timedelta(hours=1)),
            "start_timezone": "Europe/Berlin",
            "label_id": 1,
        },
        {
            "id": "e2",
            "title": "Urlaub",
            "all_day": True,
            "start_at": _ms(tomorrow.replace(hour=0)),
            "end_at": _ms(tomorrow.replace(hour=0) + timedelta(days=2)),
            "label_id": 2,
        },
        {
            "id": "e3",
            "title": "Ohne Label",
            "start_at": _ms(tomorrow + timedelta(hours=3)),
            "end_at": _ms(tomorrow + timedelta(hours=4)),
            "label_id": 3,
        },
    ]


LABELS = [
    {"id": 1, "name": "Elias", "color": "#ff0000"},
    {"id": 2, "name": "Zusammen", "color": "#00ff00"},
    {"id": 3, "name": "Label 3", "color": "#0000ff"},
]


@pytest.fixture
def mock_api():
    with patch(API, autospec=True) as cls:
        api = cls.return_value
        state = {"session": None}

        async def _login(email, password):
            state["session"] = "sess"

        api.login.side_effect = _login
        type(api).is_authenticated = property(lambda self: state["session"] is not None)
        type(api).session_id = property(lambda self: state["session"])
        api.restore_session.side_effect = lambda sid: state.update(session=sid)
        api.get_calendars.return_value = [{"id": "123", "name": "Familie"}]
        api.get_all_events_sync.return_value = _events()
        api.get_calendar_labels.return_value = LABELS
        api.create_event.return_value = {}
        with (
            patch("custom_components.timetree_enhanced.TimeTreeAPI", cls),
            patch("custom_components.timetree_enhanced.config_flow.TimeTreeAPI", cls),
        ):
            yield api


async def _setup(hass: HomeAssistant, **kwargs) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, unique_id=f"{DOMAIN}_123", title="Familie", **kwargs
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_user_flow(hass: HomeAssistant, mock_api) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: " user@example.com ", CONF_PASSWORD: "secret"}
    )
    assert result["step_id"] == "calendar"
    with patch("custom_components.timetree_enhanced.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CALENDAR_ID: "123", CONF_TIMEZONE: "Europe/Berlin"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Familie"
    assert result["data"][CONF_EMAIL] == "user@example.com"
    assert result["data"][CONF_CALENDAR_ID] == "123"


async def test_user_flow_invalid_auth(hass: HomeAssistant, mock_api) -> None:
    mock_api.login.side_effect = TimeTreeInvalidCredentials("nope")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: "a@b.c", CONF_PASSWORD: "x"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_setup_creates_entities(hass: HomeAssistant, mock_api) -> None:
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED

    assert hass.states.get("calendar.familie_alle") or hass.states.get("calendar.familie_all")
    elias = hass.states.get("calendar.familie_elias")
    assert elias is not None
    assert elias.attributes["next_event_summary"] == "Elias: Krippe"
    assert elias.attributes["color_hint"] == "#ff0000"
    assert hass.states.get("calendar.familie_zusammen") is not None
    # default label names do not get their own calendar
    assert hass.states.get("calendar.familie_label_3") is None

    count = [s for s in hass.states.async_all("sensor") if s.attributes.get("member") == "Elias"]
    assert count and count[0].state == "1"

    assert await hass.config_entries.async_unload(entry.entry_id)
    mock_api.close.assert_awaited()


async def test_get_events_service(hass: HomeAssistant, mock_api) -> None:
    await _setup(hass)
    now = datetime.now(UTC)
    resp = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": "calendar.familie_zusammen",
            "start_date_time": now,
            "end_date_time": now + timedelta(days=7),
        },
        blocking=True,
        return_response=True,
    )
    events = resp["calendar.familie_zusammen"]["events"]
    assert len(events) == 1
    ev = events[0]
    assert ev["summary"] == "Zusammen: Urlaub"
    start = date.fromisoformat(ev["start"])
    # inclusive 3-day TimeTree event -> exclusive end 3 days later
    assert date.fromisoformat(ev["end"]) - start == timedelta(days=3)


async def test_create_event_sets_label(hass: HomeAssistant, mock_api) -> None:
    await _setup(hass)
    await hass.services.async_call(
        "calendar",
        "create_event",
        {
            "entity_id": "calendar.familie_elias",
            "summary": "Arzt",
            "start_date": "2026-12-01",
            "end_date": "2026-12-02",
        },
        blocking=True,
    )
    kwargs = mock_api.create_event.call_args.kwargs
    assert kwargs["label_id"] == 1
    assert kwargs["all_day"] is True
    assert kwargs["title"] == "Arzt"


async def test_session_expired_triggers_relogin(hass: HomeAssistant, mock_api) -> None:
    from custom_components.timetree_enhanced.api import TimeTreeAuthError

    mock_api.get_all_events_sync.side_effect = [TimeTreeAuthError("expired"), _events()]
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert mock_api.login.await_count == 2


async def test_invalid_credentials_start_reauth(hass: HomeAssistant, mock_api) -> None:
    mock_api.login.side_effect = TimeTreeInvalidCredentials("nope")
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == config_entries.SOURCE_REAUTH for f in flows)


async def test_api_error_retries_setup(hass: HomeAssistant, mock_api) -> None:
    mock_api.get_all_events_sync.side_effect = TimeTreeAPIError("down")
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    mock_api.close.assert_awaited()


async def test_reauth_flow(hass: HomeAssistant, mock_api) -> None:
    entry = await _setup(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: "user@example.com", CONF_PASSWORD: "new"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new"


async def test_options_flow(hass: HomeAssistant, mock_api) -> None:
    entry = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 30, CONF_FETCH_DAYS: 90}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options == {CONF_SCAN_INTERVAL: 30, CONF_FETCH_DAYS: 90}
    assert entry.runtime_data.coordinator.fetch_days == 90


async def test_session_cookie_is_restored(hass: HomeAssistant, mock_api, hass_storage) -> None:
    hass_storage[f"{DOMAIN}_mock_session"] = {
        "version": 1,
        "key": f"{DOMAIN}_mock_session",
        "data": {"session_id": "old"},
    }
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, unique_id=f"{DOMAIN}_123", entry_id="mock"
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    mock_api.restore_session.assert_called_once_with("old")
    mock_api.login.assert_not_awaited()


async def test_diagnostics_redacts(hass: HomeAssistant, mock_api) -> None:
    from custom_components.timetree_enhanced.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    entry = await _setup(hass)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"][CONF_PASSWORD] == "**REDACTED**"
    assert diag["entry"]["data"][CONF_EMAIL] == "**REDACTED**"
    assert diag["event_count"] == 3


async def test_entity_ids(hass: HomeAssistant, mock_api) -> None:
    await _setup(hass)
    ids = sorted(s.entity_id for s in hass.states.async_all())
    assert "calendar.familie_all" in ids
    assert "calendar.familie_elias" in ids
    assert "sensor.familie_last_synchronized" in ids
