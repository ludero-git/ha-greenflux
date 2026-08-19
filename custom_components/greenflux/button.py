"""Remote command buttons for GreenFlux."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import GreenFluxConfigEntry
from .api import (
    GreenFluxError,
    command_notification_id,
    raise_for_command_rejection,
)
from .const import (
    CONF_CHARGER_ID,
    CONF_PLATFORM_NUMBER,
    DOMAIN,
    SUBENTRY_TYPE_CHARGER,
)
from .coordinator import GreenFluxStationCoordinator
from .entity import GreenFluxSocketEntity
from .models import GreenFluxSocket
from .naming import charger_prefix

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GreenFluxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GreenFlux remote command buttons."""
    del hass
    known_sockets: set[tuple[str, str]] = set()
    known_resets: set[str] = set()

    @callback
    def async_add_new_entities() -> None:
        for subentry_id, subentry in entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_CHARGER:
                continue
            charger_id = str(subentry.data[CONF_CHARGER_ID])
            station = (entry.runtime_data.station_coordinator.data or {}).get(charger_id)
            parent_device_id = entry.runtime_data.charger_device_ids.get(charger_id)
            if parent_device_id is None:
                continue

            entities: list[ButtonEntity] = []
            if charger_id not in known_resets:
                known_resets.add(charger_id)
                entities.append(GreenFluxResetButton(entry, charger_id))

            if station is not None:
                for socket in station.sockets:
                    identity = (charger_id, socket.key)
                    if identity in known_sockets:
                        continue
                    known_sockets.add(identity)
                    entities.extend(
                        (
                            GreenFluxUnlockButton(
                                entry, charger_id, socket, parent_device_id
                            ),
                            GreenFluxStopButton(
                                entry, charger_id, socket, parent_device_id
                            ),
                        )
                    )
            if entities:
                async_add_entities(entities, config_subentry_id=subentry_id)

    async_add_new_entities()
    entry.async_on_unload(
        entry.runtime_data.station_coordinator.async_add_listener(
            async_add_new_entities
        )
    )


def _ensure_command_accepted(action: str, response: dict[str, Any]) -> None:
    """Raise when GreenFlux immediately rejects a remote command."""
    try:
        raise_for_command_rejection(response)
    except GreenFluxError as err:
        raise HomeAssistantError(f"{action}: {err}") from err

    notification_id = command_notification_id(response)
    if notification_id:
        _LOGGER.info(
            "GreenFlux accepted %s. Charge station notification ID: %s",
            action,
            notification_id,
        )
    else:
        _LOGGER.info("GreenFlux accepted %s", action)


class GreenFluxSocketButton(GreenFluxSocketEntity, ButtonEntity):
    """Base remote command button tied to a socket child device."""

    def __init__(
        self,
        entry: GreenFluxConfigEntry,
        charger_id: str,
        socket: GreenFluxSocket,
        parent_device_id: str,
        key: str,
    ) -> None:
        super().__init__(entry, charger_id, socket, parent_device_id)
        object_id = f"{self.object_id_prefix}_{key}"
        self._attr_unique_id = object_id
        self.entity_id = f"button.{object_id}"

    async def _run(self, action: str, awaitable: Any) -> None:
        try:
            response = await awaitable
        except GreenFluxError as err:
            raise HomeAssistantError(str(err)) from err
        _ensure_command_accepted(action, response)


class GreenFluxUnlockButton(GreenFluxSocketButton):
    """Unlock a GreenFlux connector."""

    _attr_translation_key = "unlock_connector"

    def __init__(self, *args: Any) -> None:
        super().__init__(*args, key="unlock_connector")

    @property
    def available(self) -> bool:
        station = (self.coordinator.data or {}).get(self.charger_id)
        return (
            super().available
            and station is not None
            and station.location_id is not None
            and self.socket is not None
        )

    async def async_press(self) -> None:
        station = (self.coordinator.data or {}).get(self.charger_id)
        socket = self.socket
        if station is None or station.location_id is None or socket is None:
            raise HomeAssistantError("GreenFlux connector metadata is unavailable")
        await self._run(
            "unlock connector",
            self.entry.runtime_data.api.async_unlock_connector(
                location_id=station.location_id,
                evse_uid=socket.evse_uid,
                connector_id=socket.connector_id,
            ),
        )


class GreenFluxStopButton(GreenFluxSocketButton):
    """Stop the active session on a socket."""

    _attr_translation_key = "stop_session"

    def __init__(self, *args: Any) -> None:
        super().__init__(*args, key="stop_session")

    @property
    def available(self) -> bool:
        socket = self.socket
        if not super().available or socket is None:
            return False
        status = self.runtime_state.status or socket.status
        return status == "CHARGING"

    async def async_press(self) -> None:
        socket = self.socket
        if socket is None:
            raise HomeAssistantError("GreenFlux connector metadata is unavailable")

        state = self.runtime_state
        session_id = state.session_id
        if not session_id:
            try:
                session_id = await self.entry.runtime_data.api.async_find_recent_session_id(
                    charger_id=self.charger_id,
                    evse_uid=socket.evse_uid,
                    connector_id=socket.connector_id,
                    not_before=state.started_at,
                )
            except GreenFluxError as err:
                raise HomeAssistantError(str(err)) from err
            if session_id:
                state.session_id = session_id

        if not session_id:
            raise HomeAssistantError(
                "No recent GreenFlux session ID was found for this socket. "
                "Use the stop_session action with an explicit GreenFlux session ID."
            )

        await self._run(
            "stop session",
            self.entry.runtime_data.api.async_stop_session(session_id),
        )


class GreenFluxResetButton(
    CoordinatorEntity[GreenFluxStationCoordinator], ButtonEntity
):
    """Soft-reset a GreenFlux charge station immediately."""

    _attr_has_entity_name = True
    _attr_translation_key = "reset"

    def __init__(self, entry: GreenFluxConfigEntry, charger_id: str) -> None:
        super().__init__(entry.runtime_data.station_coordinator)
        self.entry = entry
        self.charger_id = charger_id
        self.platform_number = int(entry.data.get(CONF_PLATFORM_NUMBER, 1))
        self.object_id_prefix = charger_prefix(self.platform_number, charger_id)
        object_id = f"{self.object_id_prefix}_reset"
        self._attr_unique_id = object_id
        self.entity_id = f"button.{object_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.object_id_prefix)}
        )


    @property
    def available(self) -> bool:
        return (
            super().available
            and self.charger_id in (self.coordinator.data or {})
        )

    async def async_press(self) -> None:
        try:
            response = await self.entry.runtime_data.api.async_reset(
                charger_id=self.charger_id,
                reset_type="Soft",
                scheduled="Immediate",
            )
        except GreenFluxError as err:
            raise HomeAssistantError(str(err)) from err
        _ensure_command_accepted("reset", response)
