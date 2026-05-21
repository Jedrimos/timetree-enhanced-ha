"""Config flow for TimeTree Enhanced."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
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

from .api import TimeTreeAPI, TimeTreeAPIError, TimeTreeAuthError
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
    DEFAULT_TIMEZONE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

TIMEZONES = [
    "Europe/Berlin",
    "Europe/Vienna",
    "Europe/Zurich",
    "Europe/London",
    "America/New_York",
    "America/Los_Angeles",
    "Asia/Tokyo",
    "Asia/Shanghai",
    "Australia/Sydney",
    "UTC",
]


class TimeTreeEnhancedConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup flow for TimeTree Enhanced."""

    VERSION = 1

    def __init__(self) -> None:
        self._email: str = ""
        self._password: str = ""
        self._calendars: list[dict] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict:
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            password = user_input[CONF_PASSWORD]

            api = TimeTreeAPI()
            try:
                await api.login(email, password)
                calendars = await api.get_calendars()
                if not calendars:
                    errors["base"] = "no_calendars"
                else:
                    self._email = email
                    self._password = password
                    self._calendars = calendars
                    return await self.async_step_calendar()
            except TimeTreeAuthError as err:
                _LOGGER.warning("TimeTree Enhanced: auth error – %s", err)
                errors["base"] = "invalid_auth"
            except TimeTreeAPIError as err:
                _LOGGER.warning("TimeTree Enhanced: API error – %s", err)
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("TimeTree Enhanced: unexpected error during setup")
                errors["base"] = "unknown"
            finally:
                await api.close()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.EMAIL)
                    ),
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_calendar(
        self, user_input: dict[str, Any] | None = None
    ) -> dict:
        errors: dict[str, str] = {}

        calendar_options = {c["id"]: c.get("name", c["id"]) for c in self._calendars}

        if user_input is not None:
            cal_id: str = user_input[CONF_CALENDAR_ID]
            cal_name: str = calendar_options.get(cal_id, cal_id)

            await self.async_set_unique_id(f"{DOMAIN}_{cal_id}")
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=cal_name,
                data={
                    CONF_EMAIL: self._email,
                    CONF_PASSWORD: self._password,
                    CONF_CALENDAR_ID: cal_id,
                    CONF_CALENDAR_NAME: cal_name,
                    CONF_TIMEZONE: user_input.get(CONF_TIMEZONE, DEFAULT_TIMEZONE),
                    CONF_FETCH_DAYS: int(
                        user_input.get(CONF_FETCH_DAYS, DEFAULT_FETCH_DAYS)
                    ),
                    CONF_SCAN_INTERVAL: int(
                        user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                    ),
                },
            )

        return self.async_show_form(
            step_id="calendar",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CALENDAR_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": k, "label": v}
                                for k, v in calendar_options.items()
                            ],
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                    vol.Optional(CONF_TIMEZONE, default=DEFAULT_TIMEZONE): SelectSelector(
                        SelectSelectorConfig(
                            options=TIMEZONES,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional(
                        CONF_FETCH_DAYS, default=DEFAULT_FETCH_DAYS
                    ): NumberSelector(
                        NumberSelectorConfig(min=7, max=180, step=1, mode=NumberSelectorMode.SLIDER)
                    ),
                    vol.Optional(
                        CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                    ): NumberSelector(
                        NumberSelectorConfig(min=5, max=120, step=5, mode=NumberSelectorMode.SLIDER)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={"calendar_count": str(len(self._calendars))},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TimeTreeEnhancedOptionsFlow(config_entry)


class TimeTreeEnhancedOptionsFlow(OptionsFlow):
    """Allow changing scan interval and fetch window without reinstalling."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> dict:
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_FETCH_DAYS: int(user_input[CONF_FETCH_DAYS]),
                },
            )

        current_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL,
            self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        current_days = self.config_entry.options.get(
            CONF_FETCH_DAYS,
            self.config_entry.data.get(CONF_FETCH_DAYS, DEFAULT_FETCH_DAYS),
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL, default=current_interval
                    ): NumberSelector(
                        NumberSelectorConfig(min=5, max=120, step=5, mode=NumberSelectorMode.SLIDER)
                    ),
                    vol.Optional(
                        CONF_FETCH_DAYS, default=current_days
                    ): NumberSelector(
                        NumberSelectorConfig(min=7, max=180, step=1, mode=NumberSelectorMode.SLIDER)
                    ),
                }
            ),
        )
