"""Config flow for TimeTree Enhanced."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import (
    TimeTreeAPI,
    TimeTreeAPIError,
    TimeTreeAuthError,
    TimeTreeRateLimited,
)
from .const import (
    CONF_CALENDAR_ID,
    CONF_CALENDAR_NAME,
    CONF_EMAIL,
    CONF_FETCH_DAYS,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_TIMEZONE,
    DEFAULT_FETCH_DAYS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import session_store

_LOGGER = logging.getLogger(__name__)

TIMEZONES = [
    "Europe/Berlin",
    "Europe/Vienna",
    "Europe/Zurich",
    "Europe/London",
    "America/New_York",
    "America/Chicago",
    "America/Los_Angeles",
    "Asia/Tokyo",
    "Asia/Seoul",
    "Asia/Taipei",
    "Asia/Shanghai",
    "Australia/Sydney",
    "UTC",
]

FETCH_DAYS_SELECTOR = NumberSelector(
    NumberSelectorConfig(min=7, max=365, step=1, mode=NumberSelectorMode.BOX)
)
SCAN_INTERVAL_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=5, max=240, step=5, mode=NumberSelectorMode.SLIDER, unit_of_measurement="min"
    )
)
CREDENTIALS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


async def _validate_credentials(
    email: str, password: str
) -> tuple[list[dict[str, str]], str | None]:
    """Log in and return (calendars, error_key)."""
    api = TimeTreeAPI()
    try:
        await api.login(email, password)
        calendars = await api.get_calendars()
    except TimeTreeAuthError as err:
        _LOGGER.debug("TimeTree Enhanced: auth error – %s", err)
        return [], "invalid_auth"
    except TimeTreeRateLimited as err:
        _LOGGER.warning("TimeTree Enhanced: rate limited – %s", err)
        return [], "rate_limited"
    except TimeTreeAPIError as err:
        _LOGGER.warning("TimeTree Enhanced: API error – %s", err)
        return [], "cannot_connect"
    except Exception:  # noqa: BLE001
        _LOGGER.exception("TimeTree Enhanced: unexpected error during login")
        return [], "unknown"
    finally:
        await api.close()
    if not calendars:
        return [], "no_calendars"
    return calendars, None


class TimeTreeEnhancedConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the setup flow for TimeTree Enhanced."""

    VERSION = 1

    def __init__(self) -> None:
        self._email = ""
        self._password = ""
        self._calendars: list[dict[str, str]] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            password = user_input[CONF_PASSWORD]
            calendars, error = await _validate_credentials(email, password)
            if error:
                errors["base"] = error
            else:
                self._email, self._password, self._calendars = email, password, calendars
                return await self.async_step_calendar()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(CREDENTIALS_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_calendar(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        calendar_options = {c["id"]: c["name"] for c in self._calendars}

        if user_input is not None and CONF_CALENDAR_ID in user_input:
            cal_id: str = user_input[CONF_CALENDAR_ID]
            cal_name = calendar_options.get(cal_id, cal_id)

            await self.async_set_unique_id(f"{DOMAIN}_{cal_id}")
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=cal_name,
                data={
                    CONF_EMAIL: self._email,
                    CONF_PASSWORD: self._password,
                    CONF_CALENDAR_ID: cal_id,
                    CONF_CALENDAR_NAME: cal_name,
                    CONF_TIMEZONE: user_input.get(CONF_TIMEZONE) or self.hass.config.time_zone,
                    CONF_FETCH_DAYS: int(user_input.get(CONF_FETCH_DAYS, DEFAULT_FETCH_DAYS)),
                    CONF_SCAN_INTERVAL: int(
                        user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                    ),
                },
            )

        timezones = list(TIMEZONES)
        ha_tz = self.hass.config.time_zone
        if ha_tz and ha_tz not in timezones:
            timezones.insert(0, ha_tz)

        return self.async_show_form(
            step_id="calendar",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CALENDAR_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=[{"value": k, "label": v} for k, v in calendar_options.items()],
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                    vol.Optional(CONF_TIMEZONE, default=ha_tz or "UTC"): SelectSelector(
                        SelectSelectorConfig(
                            options=timezones,
                            mode=SelectSelectorMode.DROPDOWN,
                            custom_value=True,
                        )
                    ),
                    vol.Optional(CONF_FETCH_DAYS, default=DEFAULT_FETCH_DAYS): FETCH_DAYS_SELECTOR,
                    vol.Optional(
                        CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                    ): SCAN_INTERVAL_SELECTOR,
                }
            ),
            description_placeholders={"calendar_count": str(len(self._calendars))},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start re-authentication after TimeTree rejected the stored password."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            password = user_input[CONF_PASSWORD]
            calendars, error = await _validate_credentials(email, password)
            if error:
                errors["base"] = error
            elif entry.data[CONF_CALENDAR_ID] not in {c["id"] for c in calendars}:
                errors["base"] = "calendar_not_found"
            else:
                await session_store(self.hass, entry.entry_id).async_remove()
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_EMAIL: email, CONF_PASSWORD: password}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                CREDENTIALS_SCHEMA, {CONF_EMAIL: entry.data.get(CONF_EMAIL)}
            ),
            description_placeholders={"calendar": entry.title},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TimeTreeEnhancedOptionsFlow()


class TimeTreeEnhancedOptionsFlow(OptionsFlow):
    """Change sync interval and fetch window without re-adding the integration."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_FETCH_DAYS: int(user_input[CONF_FETCH_DAYS]),
                }
            )

        entry = self.config_entry
        current_interval = entry.options.get(
            CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        current_days = entry.options.get(
            CONF_FETCH_DAYS, entry.data.get(CONF_FETCH_DAYS, DEFAULT_FETCH_DAYS)
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL, default=current_interval
                    ): SCAN_INTERVAL_SELECTOR,
                    vol.Required(CONF_FETCH_DAYS, default=current_days): FETCH_DAYS_SELECTOR,
                }
            ),
        )
