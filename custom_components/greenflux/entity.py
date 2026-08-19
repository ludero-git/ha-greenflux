"""Shared GreenFlux entity classes."""

from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import GreenFluxConfigEntry
from .compat import socket_device_info
from .const import CONF_PLATFORM_NUMBER, DOMAIN
from .coordinator import GreenFluxStationCoordinator
from .models import GreenFluxSocket, SocketRuntimeState
from .naming import charger_prefix, socket_name, socket_prefix


class GreenFluxSocketEntity(CoordinatorEntity[GreenFluxStationCoordinator]):
    """Base entity for a GreenFlux socket."""

    _attr_has_entity_name = True

    def __init__(
        self,
        entry: GreenFluxConfigEntry,
        charger_id: str,
        socket: GreenFluxSocket,
        parent_device_id: str,
    ) -> None:
        runtime = entry.runtime_data
        super().__init__(runtime.station_coordinator)
        self.entry = entry
        self.charger_id = charger_id
        self.socket_key = socket.key
        self.socket_ordinal = socket.ordinal
        self.platform_number = int(entry.data.get(CONF_PLATFORM_NUMBER, 1))
        self.object_id_prefix = socket_prefix(
            self.platform_number, charger_id, socket.ordinal
        )
        self._attr_device_info = socket_device_info(
            identifiers={(DOMAIN, self.object_id_prefix)},
            name=socket_name(self.platform_number, charger_id, socket.ordinal),
            parent_device_id=parent_device_id,
            parent_identifier=(DOMAIN, charger_prefix(self.platform_number, charger_id)),
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to both slow station and fast notification data."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.entry.runtime_data.notification_coordinator.async_add_listener(
                self.async_write_ha_state
            )
        )

    @property
    def available(self) -> bool:
        """Return whether the socket still exists in the latest station data."""
        return super().available and self.socket is not None

    @property
    def socket(self) -> GreenFluxSocket | None:
        """Return the latest normalized socket."""
        station = (self.coordinator.data or {}).get(self.charger_id)
        if station is None:
            return None
        return next((item for item in station.sockets if item.key == self.socket_key), None)

    @property
    def runtime_state(self) -> SocketRuntimeState:
        """Return notification-derived state for this socket."""
        data = self.entry.runtime_data.notification_coordinator.data
        if data is None:
            return SocketRuntimeState()
        return data.states.get((self.charger_id, self.socket_key), SocketRuntimeState())
