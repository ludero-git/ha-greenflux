"""Data coordinators for GreenFlux."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    GreenFluxApiClient,
    GreenFluxAuthError,
    GreenFluxError,
    GreenFluxRateLimitError,
)
from .compat import update_failed
from .const import (
    INITIAL_NOTIFICATION_LOOKBACK,
    METER_VALUE_DELAY,
    METER_VALUE_INITIAL_LOOKBACK,
    METER_VALUE_OVERLAP,
    NAME,
    NOTIFICATION_OVERLAP,
    NOTIFICATION_UPDATE_INTERVAL,
    STATION_UPDATE_INTERVAL,
)
from .models import (
    GreenFluxSocket,
    GreenFluxStation,
    SocketRuntimeState,
    apply_meter_values,
    apply_notifications,
    normalize_station,
)

_LOGGER = logging.getLogger(__name__)


class GreenFluxStationCoordinator(
    DataUpdateCoordinator[dict[str, GreenFluxStation]]
):
    """Refresh the platform-wide charge station list at a safe cadence."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        api: GreenFluxApiClient,
        charger_ids: set[str],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{NAME} charge stations",
            update_interval=STATION_UPDATE_INTERVAL,
        )
        self.api = api
        self.charger_ids = charger_ids

    def normalize_snapshot(
        self, raw_stations: list[dict[str, Any]]
    ) -> dict[str, GreenFluxStation]:
        """Normalize and filter one platform-wide station snapshot."""
        stations: dict[str, GreenFluxStation] = {}
        for raw in raw_stations:
            try:
                station = normalize_station(raw)
            except ValueError:
                _LOGGER.debug("Ignoring a GreenFlux charge station without a usable ID")
                continue
            if station.charger_id in self.charger_ids:
                stations[station.charger_id] = station

        missing = self.charger_ids - stations.keys()
        if missing:
            _LOGGER.warning(
                "Configured GreenFlux charge stations not present in bulk response: %s",
                ", ".join(sorted(missing)),
            )
        _LOGGER.debug(
            "Loaded %d configured GreenFlux charge stations from %d API records",
            len(stations),
            len(raw_stations),
        )
        return stations

    async def _async_update_data(self) -> dict[str, GreenFluxStation]:
        if not self.charger_ids:
            return {}
        try:
            raw_stations = await self.api.async_get_charge_stations()
        except GreenFluxAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GreenFluxRateLimitError as err:
            raise update_failed(str(err), err.retry_after) from err
        except GreenFluxError as err:
            raise UpdateFailed(str(err)) from err
        return self.normalize_snapshot(raw_stations)


@dataclass(slots=True)
class NotificationData:
    """Derived fast-changing socket state."""

    states: dict[tuple[str, str], SocketRuntimeState] = field(default_factory=dict)
    last_query_end: datetime | None = None
    last_meter_query_end: datetime | None = None


class GreenFluxNotificationCoordinator(DataUpdateCoordinator[NotificationData]):
    """Poll bulk live feeds and derive fast-changing socket state."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        api: GreenFluxApiClient,
        station_coordinator: GreenFluxStationCoordinator,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{NAME} notifications",
            update_interval=NOTIFICATION_UPDATE_INTERVAL,
        )
        self.api = api
        self.station_coordinator = station_coordinator
        self._state = NotificationData()

    async def _async_update_data(self) -> NotificationData:
        if not self.station_coordinator.charger_ids:
            return self._state

        sockets: dict[str, tuple[GreenFluxSocket, ...]] = {
            charger_id: station.sockets
            for charger_id, station in (self.station_coordinator.data or {}).items()
        }
        if not sockets:
            return self._state

        now = datetime.now(timezone.utc)
        if self._state.last_query_end is None:
            start = now - INITIAL_NOTIFICATION_LOOKBACK
        else:
            start = self._state.last_query_end - NOTIFICATION_OVERLAP

        meter_end = now - METER_VALUE_DELAY
        if self._state.last_meter_query_end is None:
            meter_start = meter_end - METER_VALUE_INITIAL_LOOKBACK
        else:
            meter_start = self._state.last_meter_query_end - METER_VALUE_OVERLAP

        try:
            records = await self.api.async_get_charge_station_notifications(start, now)
            meter_values = await self.api.async_get_meter_values(meter_start, meter_end)
        except GreenFluxAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GreenFluxRateLimitError as err:
            raise update_failed(str(err), err.retry_after) from err
        except GreenFluxError as err:
            raise UpdateFailed(str(err)) from err

        selected = [
            record
            for record in records
            if str(
                record.get("charge_station_id")
                or record.get("chargeStationId")
                or ""
            )
            in self.station_coordinator.charger_ids
        ]
        selected_meter_values = [
            record
            for record in meter_values
            if str(
                record.get("charge_station_id")
                or record.get("chargeStationId")
                or ""
            )
            in self.station_coordinator.charger_ids
        ]

        apply_meter_values(selected_meter_values, self._state.states, sockets)
        apply_notifications(selected, self._state.states, sockets)
        self._state.last_query_end = now
        self._state.last_meter_query_end = meter_end
        _LOGGER.debug(
            "Processed %d GreenFlux notifications and %d MeterValues for configured chargers",
            len(selected),
            len(selected_meter_values),
        )
        return self._state
