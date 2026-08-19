"""Diagnostics support for GreenFlux."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import GreenFluxConfigEntry
from .const import CONF_API_URL, CONF_AUTO_CREATE, CONF_CHARGER_ID


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GreenFluxConfigEntry
) -> dict[str, Any]:
    """Return non-secret diagnostics for a GreenFlux config entry."""
    del hass
    station_data = entry.runtime_data.station_coordinator.data or {}
    notification_data = entry.runtime_data.notification_coordinator.data
    return {
        "config": {
            "api_url": entry.data.get(CONF_API_URL),
            "auto_create": entry.data.get(CONF_AUTO_CREATE, False),
            "charger_ids": [
                subentry.data.get(CONF_CHARGER_ID)
                for subentry in entry.subentries.values()
                if CONF_CHARGER_ID in subentry.data
            ],
        },
        "station_count": len(station_data),
        "stations": {
            charger_id: {
                "manufacturer": station.manufacturer,
                "model": station.model,
                "serial_number": station.serial_number,
                "firmware_version": station.firmware_version,
                "status": station.status,
                "socket_count": len(station.sockets),
            }
            for charger_id, station in station_data.items()
        },
        "notification_state_count": (
            len(notification_data.states) if notification_data else 0
        ),
        "notification_last_query_end": (
            notification_data.last_query_end.isoformat()
            if notification_data and notification_data.last_query_end
            else None
        ),
    }
