"""Data coordinators for GreenFlux."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.storage import Store

from .api import (
    GreenFluxApiClient,
    GreenFluxAuthError,
    GreenFluxError,
    GreenFluxRateLimitError,
)
from .compat import update_failed
from .const import (
    CONF_API_TOKEN,
    DOMAIN,
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
    station_id,
)
from .setup_cache import cache_key

_LOGGER = logging.getLogger(__name__)


class GreenFluxStationCoordinator(
    DataUpdateCoordinator[dict[str, GreenFluxStation]]
):
    """Cache configured charge stations and sync changes hourly."""

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
        self._store = Store(
            hass, 1, f"{DOMAIN}.stations.{config_entry.entry_id}",
            private=True,
            serialize_in_event_loop=False,
        )
        self._cache_key = cache_key(api.api_url, config_entry.data[CONF_API_TOKEN])
        self._last_sync: datetime | None = None

    def normalize_snapshot(
        self, raw_stations: list[dict[str, Any]], full: bool = True
    ) -> dict[str, GreenFluxStation]:
        """Normalize only configured stations."""
        stations: dict[str, GreenFluxStation] = {}
        for raw in raw_stations:
            if station_id(raw) not in self.charger_ids:
                continue
            try:
                station = normalize_station(raw)
            except ValueError:
                _LOGGER.debug("Ignoring a GreenFlux charge station without a usable ID")
                continue
            stations[station.charger_id] = station
        missing = self.charger_ids - stations.keys()
        if full and missing:
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

    async def async_restore_cache(self, staged: list[dict[str, Any]] | None) -> bool:
        """Restore configured stations from disk and staged setup responses."""
        if not self.charger_ids:
            await self._store.async_remove()
            self.async_set_updated_data({})
            return True
        saved = await self._store.async_load()
        if (
            not isinstance(saved, dict)
            or saved.get("key", saved.get("credentials")) != self._cache_key
        ):
            saved = {}
        stored = saved.get("stations", {})
        if not isinstance(stored, dict):
            stored = {}
        records = {
            charger_id: raw for charger_id, raw in stored.items()
            if charger_id in self.charger_ids and isinstance(raw, dict)
        }
        staged_ids = set()
        for raw in staged or []:
            charger_id = station_id(raw)
            if charger_id in self.charger_ids:
                records[charger_id] = raw
                staged_ids.add(charger_id)
        if set(records) != self.charger_ids:
            return False
        try:
            self._last_sync = datetime.fromisoformat(saved["last_sync"])
        except (KeyError, TypeError, ValueError):
            self._last_sync = (
                datetime.now(timezone.utc) if staged_ids == self.charger_ids else None
            )
        if (
            self._last_sync is None or self._last_sync.tzinfo is None
            or self._last_sync > datetime.now(timezone.utc)
        ):
            return False
        stations = await self.hass.async_add_executor_job(
            self.normalize_snapshot, list(records.values())
        )
        if set(stations) != self.charger_ids:
            return False
        self.async_set_updated_data(stations)
        await self._save_cache(stations)
        return True

    async def _save_cache(self, stations: dict[str, GreenFluxStation]) -> None:
        """Persist only configured stations and the last successful sync."""
        await self._store.async_save({
            "key": self._cache_key,
            "last_sync": self._last_sync.isoformat() if self._last_sync else None,
            "stations": {
                charger_id: dict(station.raw) for charger_id, station in stations.items()
            },
        })

    async def _async_update_data(self) -> dict[str, GreenFluxStation]:
        if not self.charger_ids:
            return {}
        started = datetime.now(timezone.utc)
        date_from = self._last_sync - timedelta(minutes=5) if self._last_sync else None
        try:
            raw_stations = await self.api.async_get_charge_stations(date_from=date_from)
            changed = await self.hass.async_add_executor_job(
                self.normalize_snapshot, raw_stations, date_from is None
            )
        except GreenFluxAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GreenFluxRateLimitError as err:
            raise update_failed(str(err), err.retry_after) from err
        except GreenFluxError as err:
            raise UpdateFailed(str(err)) from err
        stations = dict(self.data or {}) if date_from else {}
        stations.update(changed)
        stations = {
            charger_id: station for charger_id, station in stations.items()
            if charger_id in self.charger_ids
        }
        self._last_sync = started
        await self._save_cache(stations)
        return stations

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
