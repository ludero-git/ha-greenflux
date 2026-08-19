"""GreenFlux integration."""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from functools import partial
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.importlib import async_import_module

from .api import GreenFluxApiClient, GreenFluxError, raise_for_command_rejection
from .compat import charger_parent_kwargs
from .const import (
    CONF_API_TOKEN,
    CONF_API_URL,
    CONF_CHARGER_ID,
    CONF_PLATFORM_NUMBER,
    DOMAIN,
    PLATFORMS,
    SUBENTRY_TYPE_CHARGER,
)
from .coordinator import (
    GreenFluxNotificationCoordinator,
    GreenFluxStationCoordinator,
    NotificationData,
)
from .naming import charger_name, charger_prefix
from .setup_cache import pop_station_snapshot

_LOGGER = logging.getLogger(__name__)

SERVICE_START_SESSION = "start_session"
SERVICE_STOP_SESSION = "stop_session"
SERVICE_UNLOCK_CONNECTOR = "unlock_connector"
SERVICE_RESET = "reset"
SERVICE_COMMAND_STATUS = "command_status"

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_CHARGER_ID = "charger_id"
ATTR_LOCATION_ID = "location_id"
ATTR_EVSE_UID = "evse_uid"
ATTR_CONNECTOR_ID = "connector_id"
ATTR_TOKEN_UID = "token_uid"
ATTR_AUTH_ID = "auth_id"
ATTR_SESSION_ID = "session_id"
ATTR_NOTIFICATION_ID = "notification_id"
ATTR_RESET_TYPE = "type"
ATTR_SCHEDULED = "scheduled"


@dataclass(slots=True)
class GreenFluxRuntimeData:
    """Runtime data for one GreenFlux platform config entry."""

    api: GreenFluxApiClient
    station_coordinator: GreenFluxStationCoordinator
    notification_coordinator: GreenFluxNotificationCoordinator
    platform_device_id: str
    charger_device_ids: dict[str, str]


GreenFluxConfigEntry = ConfigEntry[GreenFluxRuntimeData]


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up global GreenFlux service actions."""
    del config

    common = {vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string}

    hass.services.async_register(
        DOMAIN,
        SERVICE_START_SESSION,
        partial(_async_service_start_session, hass),
        schema=vol.Schema(
            {
                **common,
                vol.Required(ATTR_CHARGER_ID): cv.string,
                vol.Required(ATTR_LOCATION_ID): cv.string,
                vol.Required(ATTR_EVSE_UID): cv.string,
                vol.Optional(ATTR_CONNECTOR_ID): cv.string,
                vol.Optional(ATTR_TOKEN_UID): cv.string,
                vol.Optional(ATTR_AUTH_ID): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_STOP_SESSION,
        partial(_async_service_stop_session, hass),
        schema=vol.Schema(
            {
                **common,
                vol.Required(ATTR_SESSION_ID): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UNLOCK_CONNECTOR,
        partial(_async_service_unlock_connector, hass),
        schema=vol.Schema(
            {
                **common,
                vol.Required(ATTR_LOCATION_ID): cv.string,
                vol.Required(ATTR_EVSE_UID): cv.string,
                vol.Required(ATTR_CONNECTOR_ID): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET,
        partial(_async_service_reset, hass),
        schema=vol.Schema(
            {
                **common,
                vol.Required(ATTR_CHARGER_ID): cv.string,
                vol.Optional(ATTR_EVSE_UID): cv.string,
                vol.Optional(ATTR_RESET_TYPE, default="Soft"): vol.In(
                    ["Hard", "Soft"]
                ),
                vol.Optional(ATTR_SCHEDULED, default="Immediate"): vol.In(
                    ["Immediate", "OnIdle"]
                ),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_COMMAND_STATUS,
        partial(_async_service_command_status, hass),
        schema=vol.Schema(
            {
                **common,
                vol.Required(ATTR_EVSE_UID): cv.string,
                vol.Required(ATTR_NOTIFICATION_ID): cv.string,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def _async_config_entry_updated(
    hass: HomeAssistant, entry: GreenFluxConfigEntry
) -> None:
    """Reload when credentials or charger subentries change."""
    hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: GreenFluxConfigEntry) -> bool:
    """Set up one GreenFlux platform."""
    entry.async_on_unload(entry.add_update_listener(_async_config_entry_updated))
    api = GreenFluxApiClient(
        async_get_clientsession(hass),
        entry.data[CONF_API_URL],
        entry.data[CONF_API_TOKEN],
    )
    charger_ids = {
        str(subentry.data[CONF_CHARGER_ID])
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_CHARGER
        and CONF_CHARGER_ID in subentry.data
    }
    station_coordinator = GreenFluxStationCoordinator(hass, entry, api, charger_ids)
    notification_coordinator = GreenFluxNotificationCoordinator(
        hass, entry, api, station_coordinator
    )

    station_snapshot = pop_station_snapshot(
        hass, entry.data[CONF_API_URL], entry.data[CONF_API_TOKEN]
    )
    if station_snapshot is None:
        await station_coordinator.async_config_entry_first_refresh()
    else:
        station_coordinator.async_set_updated_data(
            station_coordinator.normalize_snapshot(station_snapshot)
        )

    # Notifications are optional; initialize empty state and let the coordinator poll after setup.
    notification_coordinator.async_set_updated_data(NotificationData())

    platform_number = int(entry.data.get(CONF_PLATFORM_NUMBER, 1))
    device_registry = dr.async_get(hass)
    platform_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"platform:{entry.entry_id}")},
        name=f"GreenFlux {api.host}",
        manufacturer="GreenFlux",
        model="API platform",
        entry_type=dr.DeviceEntryType.SERVICE,
    )

    charger_device_ids: dict[str, str] = {}
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_CHARGER:
            continue
        charger_id = str(subentry.data[CONF_CHARGER_ID])
        station = (station_coordinator.data or {}).get(charger_id)
        charger_device = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            config_subentry_id=subentry_id,
            identifiers={(DOMAIN, charger_prefix(platform_number, charger_id))},
            name=charger_name(platform_number, charger_id),
            manufacturer=station.manufacturer if station else None,
            model=station.model if station else None,
            serial_number=station.serial_number if station else None,
            sw_version=station.firmware_version if station else None,
            **charger_parent_kwargs(
                platform_device_id=platform_device.id,
                platform_identifier=(DOMAIN, f"platform:{entry.entry_id}"),
            ),
        )
        charger_device_ids[charger_id] = charger_device.id

    entry.runtime_data = GreenFluxRuntimeData(
        api=api,
        station_coordinator=station_coordinator,
        notification_coordinator=notification_coordinator,
        platform_device_id=platform_device.id,
        charger_device_ids=charger_device_ids,
    )

    # Pre-import platforms to avoid blocking I/O during async platform setup.
    for platform in PLATFORMS:
        await async_import_module(hass, f"{__package__}.{platform}")

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GreenFluxConfigEntry) -> bool:
    """Unload a GreenFlux config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _get_loaded_entry(hass: HomeAssistant, entry_id: str) -> GreenFluxConfigEntry:
    """Return a loaded GreenFlux config entry selected by a service call."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError("The selected GreenFlux config entry does not exist")
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError("The selected GreenFlux config entry is not loaded")
    return entry  # type: ignore[return-value]


async def _async_api_call(
    awaitable: Awaitable[dict[str, Any]], *, command: bool = False
) -> dict[str, Any]:
    """Convert client errors into Home Assistant service errors."""
    try:
        response = await awaitable
        if command:
            raise_for_command_rejection(response)
        return response
    except GreenFluxError as err:
        raise HomeAssistantError(str(err)) from err


async def _async_service_start_session(
    hass: HomeAssistant, call: ServiceCall
) -> dict[str, Any] | None:
    entry = _get_loaded_entry(hass, call.data[ATTR_CONFIG_ENTRY_ID])
    token_uid = call.data.get(ATTR_TOKEN_UID)
    auth_id = call.data.get(ATTR_AUTH_ID)
    if not token_uid and not auth_id:
        raise ServiceValidationError("Provide token_uid or auth_id")
    response = await _async_api_call(
        entry.runtime_data.api.async_start_session(
            charger_id=call.data[ATTR_CHARGER_ID],
            location_id=call.data[ATTR_LOCATION_ID],
            evse_uid=call.data[ATTR_EVSE_UID],
            connector_id=call.data.get(ATTR_CONNECTOR_ID),
            token_uid=token_uid,
            auth_id=auth_id,
        ),
        command=True,
    )
    return response if call.return_response else None


async def _async_service_stop_session(
    hass: HomeAssistant, call: ServiceCall
) -> dict[str, Any] | None:
    entry = _get_loaded_entry(hass, call.data[ATTR_CONFIG_ENTRY_ID])
    response = await _async_api_call(
        entry.runtime_data.api.async_stop_session(call.data[ATTR_SESSION_ID]),
        command=True,
    )
    return response if call.return_response else None


async def _async_service_unlock_connector(
    hass: HomeAssistant, call: ServiceCall
) -> dict[str, Any] | None:
    entry = _get_loaded_entry(hass, call.data[ATTR_CONFIG_ENTRY_ID])
    response = await _async_api_call(
        entry.runtime_data.api.async_unlock_connector(
            location_id=call.data[ATTR_LOCATION_ID],
            evse_uid=call.data[ATTR_EVSE_UID],
            connector_id=call.data[ATTR_CONNECTOR_ID],
        ),
        command=True,
    )
    return response if call.return_response else None


async def _async_service_reset(
    hass: HomeAssistant, call: ServiceCall
) -> dict[str, Any] | None:
    entry = _get_loaded_entry(hass, call.data[ATTR_CONFIG_ENTRY_ID])
    response = await _async_api_call(
        entry.runtime_data.api.async_reset(
            charger_id=call.data[ATTR_CHARGER_ID],
            evse_uid=call.data.get(ATTR_EVSE_UID),
            reset_type=call.data[ATTR_RESET_TYPE],
            scheduled=call.data[ATTR_SCHEDULED],
        ),
        command=True,
    )
    return response if call.return_response else None


async def _async_service_command_status(
    hass: HomeAssistant, call: ServiceCall
) -> dict[str, Any]:
    entry = _get_loaded_entry(hass, call.data[ATTR_CONFIG_ENTRY_ID])
    return await _async_api_call(
        entry.runtime_data.api.async_get_command_notification(
            call.data[ATTR_EVSE_UID], call.data[ATTR_NOTIFICATION_ID]
        )
    )
