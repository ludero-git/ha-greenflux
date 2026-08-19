"""Sensors for GreenFlux chargers and sockets."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import GreenFluxConfigEntry
from .const import (
    CONF_CHARGER_ID,
    CONF_PLATFORM_NUMBER,
    DOMAIN,
    STATUS_OPTIONS,
    SUBENTRY_TYPE_CHARGER,
)
from .entity import GreenFluxSocketEntity
from .models import GreenFluxSocket, GreenFluxStation
from .naming import charger_prefix


@dataclass(frozen=True, kw_only=True)
class GreenFluxSensorDescription(SensorEntityDescription):
    """Describe a GreenFlux socket sensor."""

    value_fn: Callable[[GreenFluxStation, GreenFluxSocket, GreenFluxSocketEntity], Any]


def _station_value(
    field: str,
) -> Callable[[GreenFluxStation, GreenFluxSocket, GreenFluxSocketEntity], Any]:
    return lambda station, socket, entity: getattr(station, field)


def _socket_value(
    field: str,
) -> Callable[[GreenFluxStation, GreenFluxSocket, GreenFluxSocketEntity], Any]:
    return lambda station, socket, entity: getattr(socket, field)


SENSORS: tuple[GreenFluxSensorDescription, ...] = (
    GreenFluxSensorDescription(
        key="tag_id",
        translation_key="tag_id",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda station, socket, entity: entity.runtime_state.tag_id,
    ),
    GreenFluxSensorDescription(
        key="current_power",
        translation_key="current_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda station, socket, entity: entity.runtime_state.current_power_w,
    ),
    GreenFluxSensorDescription(
        key="energy",
        translation_key="energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
        value_fn=lambda station, socket, entity: entity.runtime_state.energy_kwh,
    ),
    GreenFluxSensorDescription(
        key="manufacturer",
        translation_key="manufacturer",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_station_value("manufacturer"),
    ),
    GreenFluxSensorDescription(
        key="model",
        translation_key="model",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_station_value("model"),
    ),
    GreenFluxSensorDescription(
        key="serial_number",
        translation_key="serial_number",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_station_value("serial_number"),
    ),
    GreenFluxSensorDescription(
        key="firmware_version",
        translation_key="firmware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_station_value("firmware_version"),
    ),
    GreenFluxSensorDescription(
        key="ip_address",
        translation_key="ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_station_value("ip_address"),
    ),
    GreenFluxSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=STATUS_OPTIONS,
        value_fn=lambda station, socket, entity: (
            entity.runtime_state.status or socket.status
        ),
    ),
    GreenFluxSensorDescription(
        key="power_type",
        translation_key="power_type",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_socket_value("power_type"),
    ),
    GreenFluxSensorDescription(
        key="voltage",
        translation_key="voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        value_fn=_socket_value("voltage"),
    ),
    GreenFluxSensorDescription(
        key="amperage",
        translation_key="amperage",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        value_fn=_socket_value("amperage"),
    ),
    GreenFluxSensorDescription(
        key="max_power",
        translation_key="max_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=_socket_value("max_power"),
    ),
    GreenFluxSensorDescription(
        key="next_scheduled_status",
        name="Next scheduled status",
        translation_key="next_scheduled_status",
        device_class=SensorDeviceClass.ENUM,
        options=STATUS_OPTIONS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_socket_value("next_scheduled_status"),
    ),
    GreenFluxSensorDescription(
        key="next_scheduled_start",
        name="Next scheduled start",
        translation_key="next_scheduled_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_socket_value("next_scheduled_start"),
    ),
    GreenFluxSensorDescription(
        key="next_scheduled_end",
        name="Next scheduled end",
        translation_key="next_scheduled_end",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_socket_value("next_scheduled_end"),
    ),
    GreenFluxSensorDescription(
        key="next_scheduled_message",
        name="Next scheduled message",
        translation_key="next_scheduled_message",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_socket_value("next_scheduled_message"),
    ),
    GreenFluxSensorDescription(
        key="session_id",
        translation_key="session_id",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda station, socket, entity: entity.runtime_state.session_id,
    ),
    GreenFluxSensorDescription(
        key="transaction_id",
        translation_key="transaction_id",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda station, socket, entity: entity.runtime_state.transaction_id,
    ),
    GreenFluxSensorDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda station, socket, entity: entity.runtime_state.state_of_charge,
    ),
)


@dataclass(frozen=True, kw_only=True)
class GreenFluxChargerSensorDescription(SensorEntityDescription):
    """Describe a charger-level GreenFlux sensor."""

    value_fn: Callable[[GreenFluxStation], Any]


CHARGER_SENSORS: tuple[GreenFluxChargerSensorDescription, ...] = (
    GreenFluxChargerSensorDescription(
        key="maintenance_info",
        name="Maintenance info",
        translation_key="maintenance_info",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda station: station.maintenance_info,
    ),
    GreenFluxChargerSensorDescription(
        key="next_scheduled_status",
        name="Next scheduled status",
        translation_key="next_scheduled_status",
        device_class=SensorDeviceClass.ENUM,
        options=STATUS_OPTIONS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda station: station.next_scheduled_status,
    ),
    GreenFluxChargerSensorDescription(
        key="next_scheduled_start",
        name="Next scheduled start",
        translation_key="next_scheduled_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda station: station.next_scheduled_start,
    ),
    GreenFluxChargerSensorDescription(
        key="next_scheduled_end",
        name="Next scheduled end",
        translation_key="next_scheduled_end",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda station: station.next_scheduled_end,
    ),
    GreenFluxChargerSensorDescription(
        key="next_scheduled_message",
        name="Next scheduled message",
        translation_key="next_scheduled_message",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda station: station.next_scheduled_message,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GreenFluxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GreenFlux sensor entities and discover new sockets on refresh."""
    del hass
    known: set[tuple[str, str]] = set()

    @callback
    def async_add_new_entities() -> None:
        for subentry_id, subentry in entry.subentries.items():
            if subentry.subentry_type != SUBENTRY_TYPE_CHARGER:
                continue
            charger_id = str(subentry.data[CONF_CHARGER_ID])
            station = (entry.runtime_data.station_coordinator.data or {}).get(charger_id)
            parent_device_id = entry.runtime_data.charger_device_ids.get(charger_id)
            if station is None or parent_device_id is None:
                continue

            entities: list[SensorEntity] = []
            charger_identity = (charger_id, "__charger__")
            if charger_identity not in known:
                known.add(charger_identity)
                entities.extend(
                    GreenFluxChargerSensor(entry, charger_id, description)
                    for description in CHARGER_SENSORS
                )

            for socket in station.sockets:
                identity = (charger_id, socket.key)
                if identity in known:
                    continue
                known.add(identity)
                entities.extend(
                    GreenFluxSocketSensor(
                        entry,
                        charger_id,
                        socket,
                        parent_device_id,
                        description,
                    )
                    for description in SENSORS
                )
            if entities:
                async_add_entities(entities, config_subentry_id=subentry_id)

    async_add_new_entities()
    entry.async_on_unload(
        entry.runtime_data.station_coordinator.async_add_listener(
            async_add_new_entities
        )
    )


class GreenFluxSocketSensor(GreenFluxSocketEntity, SensorEntity):
    """A sensor belonging to one GreenFlux socket."""

    entity_description: GreenFluxSensorDescription

    def __init__(
        self,
        entry: GreenFluxConfigEntry,
        charger_id: str,
        socket: GreenFluxSocket,
        parent_device_id: str,
        description: GreenFluxSensorDescription,
    ) -> None:
        super().__init__(entry, charger_id, socket, parent_device_id)
        self.entity_description = description
        object_id = f"{self.object_id_prefix}_{description.key}"
        self._attr_unique_id = object_id
        self.entity_id = f"sensor.{object_id}"

    @property
    def native_value(self) -> Any:
        """Return the current sensor value."""
        station = (self.coordinator.data or {}).get(self.charger_id)
        socket = self.socket
        if station is None or socket is None:
            return None
        return self.entity_description.value_fn(station, socket, self)


class GreenFluxChargerSensor(CoordinatorEntity, SensorEntity):
    """A sensor attached directly to a GreenFlux charger device."""

    _attr_has_entity_name = True
    entity_description: GreenFluxChargerSensorDescription

    def __init__(
        self,
        entry: GreenFluxConfigEntry,
        charger_id: str,
        description: GreenFluxChargerSensorDescription,
    ) -> None:
        super().__init__(entry.runtime_data.station_coordinator)
        self.charger_id = charger_id
        self.entity_description = description
        platform_number = int(entry.data.get(CONF_PLATFORM_NUMBER, 1))
        prefix = charger_prefix(platform_number, charger_id)
        object_id = f"{prefix}_{description.key}"
        self._attr_unique_id = object_id
        self.entity_id = f"sensor.{object_id}"
        self._attr_device_info = {"identifiers": {(DOMAIN, prefix)}}

    @property
    def available(self) -> bool:
        """Return whether the charger exists in the latest station data."""
        return super().available and self.charger_id in (self.coordinator.data or {})

    @property
    def native_value(self) -> Any:
        """Return the current charger-level value."""
        station = (self.coordinator.data or {}).get(self.charger_id)
        if station is None:
            return None
        return self.entity_description.value_fn(station)
