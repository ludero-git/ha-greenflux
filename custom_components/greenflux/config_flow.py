"""Config flow for GreenFlux."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlowResult,
    ConfigSubentryData,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    GreenFluxApiClient,
    GreenFluxAuthError,
    GreenFluxConnectionError,
    GreenFluxError,
    GreenFluxNotFoundError,
    GreenFluxRateLimitError,
    normalize_api_url,
)
from .const import (
    CONF_API_TOKEN,
    CONF_API_URL,
    CONF_AUTO_CREATE,
    CONF_CHARGER_ID,
    CONF_PLATFORM_NUMBER,
    DOMAIN,
    SUBENTRY_TYPE_CHARGER,
)
from .models import normalize_station
from .naming import charger_name, charger_prefix
from .setup_cache import store_station_snapshot


def _next_platform_number(entries: list[ConfigEntry]) -> int:
    """Return the next stable platform number for a new GreenFlux hub."""
    numbers: list[int] = []
    for entry in entries:
        stored = str(entry.data.get(CONF_PLATFORM_NUMBER, ""))
        if stored.isdigit():
            numbers.append(int(stored))
            continue
        if entry.title.startswith("Platform "):
            suffix = entry.title.removeprefix("Platform ")
            if suffix.isdigit():
                numbers.append(int(suffix))
    return max(numbers, default=0) + 1



class GreenFluxConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle GreenFlux platform configuration."""

    VERSION = 1
    MINOR_VERSION = 1

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return supported GreenFlux subentry types."""
        del config_entry
        return {SUBENTRY_TYPE_CHARGER: GreenFluxChargerSubentryFlow}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure a GreenFlux API platform."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                api_url = normalize_api_url(user_input[CONF_API_URL])
                client = GreenFluxApiClient(
                    async_get_clientsession(self.hass),
                    api_url,
                    user_input[CONF_API_TOKEN],
                )
                if user_input[CONF_AUTO_CREATE]:
                    raw_stations = await client.async_get_charge_stations()
                else:
                    await client.async_validate()
                    raw_stations = []
            except ValueError:
                errors["base"] = "invalid_url"
            except GreenFluxAuthError:
                errors["base"] = "invalid_auth"
            except GreenFluxRateLimitError:
                errors["base"] = "rate_limited"
            except (GreenFluxConnectionError, GreenFluxError):
                errors["base"] = "cannot_connect"
            else:
                platform_number = _next_platform_number(
                    self.hass.config_entries.async_entries(DOMAIN)
                )
                subentries: list[ConfigSubentryData] = []
                if user_input[CONF_AUTO_CREATE]:
                    seen: set[str] = set()
                    for raw in raw_stations:
                        try:
                            station = normalize_station(raw)
                        except ValueError:
                            continue
                        if station.charger_id in seen:
                            continue
                        seen.add(station.charger_id)
                        subentries.append(
                            ConfigSubentryData(
                                data={CONF_CHARGER_ID: station.charger_id},
                                subentry_type=SUBENTRY_TYPE_CHARGER,
                                title=charger_name(platform_number, station.charger_id),
                                unique_id=charger_prefix(platform_number, station.charger_id),
                            )
                        )
                token = user_input[CONF_API_TOKEN].strip()
                if user_input[CONF_AUTO_CREATE]:
                    store_station_snapshot(self.hass, api_url, token, raw_stations)
                return self.async_create_entry(
                    title=f"Platform {platform_number}",
                    data={
                        CONF_API_URL: api_url,
                        CONF_API_TOKEN: token,
                        CONF_AUTO_CREATE: user_input[CONF_AUTO_CREATE],
                        CONF_PLATFORM_NUMBER: platform_number,
                    },
                    subentries=subentries,
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_API_URL, default="platform.greenflux.com"
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
                vol.Required(CONF_API_TOKEN): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Required(CONF_AUTO_CREATE, default=False): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication."""
        del entry_data
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update a rejected GreenFlux token."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            try:
                client = GreenFluxApiClient(
                    async_get_clientsession(self.hass),
                    entry.data[CONF_API_URL],
                    user_input[CONF_API_TOKEN],
                )
                await client.async_validate()
            except GreenFluxAuthError:
                errors["base"] = "invalid_auth"
            except GreenFluxRateLimitError:
                errors["base"] = "rate_limited"
            except GreenFluxError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_and_abort(
                    entry,
                    data_updates={CONF_API_TOKEN: user_input[CONF_API_TOKEN].strip()},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
        )


class GreenFluxChargerSubentryFlow(ConfigSubentryFlow):
    """Add a charge station under an existing GreenFlux platform."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add one GreenFlux charger."""
        errors: dict[str, str] = {}
        if user_input is not None:
            charger_id = user_input[CONF_CHARGER_ID].strip()
            config_entry = self._get_entry()
            existing = {
                str(subentry.data.get(CONF_CHARGER_ID, ""))
                for subentry in config_entry.subentries.values()
                if subentry.subentry_type == SUBENTRY_TYPE_CHARGER
            }
            if charger_id in existing:
                errors["base"] = "already_configured"
            else:
                client = GreenFluxApiClient(
                    async_get_clientsession(self.hass),
                    config_entry.data[CONF_API_URL],
                    config_entry.data[CONF_API_TOKEN],
                )
                try:
                    raw_station = await client.async_get_charge_station(charger_id)
                    station = normalize_station(raw_station)
                except GreenFluxNotFoundError:
                    errors["base"] = "charger_not_found"
                except GreenFluxAuthError:
                    errors["base"] = "invalid_auth"
                except GreenFluxRateLimitError:
                    errors["base"] = "rate_limited"
                except GreenFluxError:
                    errors["base"] = "cannot_connect"
                except ValueError:
                    errors["base"] = "charger_not_found"
                else:
                    platform_number = int(
                        config_entry.data.get(CONF_PLATFORM_NUMBER, 1)
                    )
                    return self.async_create_entry(
                        title=charger_name(platform_number, station.charger_id),
                        data={CONF_CHARGER_ID: station.charger_id},
                        unique_id=charger_prefix(platform_number, station.charger_id),
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CHARGER_ID): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.TEXT)
                    )
                }
            ),
            errors=errors,
        )
