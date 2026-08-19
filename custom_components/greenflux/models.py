"""GreenFlux response models and tolerant parsers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
from typing import Any

from .const import DEFAULT_STATUS, STATUS_OPTIONS


@dataclass(frozen=True, slots=True)
class GreenFluxSocket:
    """One physical connector/socket exposed by a charge station."""

    key: str
    ordinal: int
    evse_uid: str
    evse_id: str | None
    connector_id: str
    name: str
    status: str
    power_type: str | None
    voltage: float | None
    amperage: float | None
    max_power: float | None
    next_scheduled_status: str | None
    next_scheduled_start: datetime | None
    next_scheduled_end: datetime | None
    next_scheduled_message: str | None


@dataclass(frozen=True, slots=True)
class GreenFluxStation:
    """Normalized GreenFlux charge station."""

    charger_id: str
    location_id: str | None
    manufacturer: str | None
    model: str | None
    serial_number: str | None
    firmware_version: str | None
    ip_address: str | None
    maintenance_info: str | None
    next_scheduled_status: str | None
    next_scheduled_start: datetime | None
    next_scheduled_end: datetime | None
    next_scheduled_message: str | None
    status: str
    sockets: tuple[GreenFluxSocket, ...]
    raw: Mapping[str, Any] = field(repr=False)


@dataclass(slots=True)
class SocketRuntimeState:
    """Fast-changing state derived from ChargeStation Notifications."""

    tag_id: str | None = None
    last_tag_id: str | None = None
    transaction_id: str | None = None
    session_id: str | None = None
    status: str | None = None
    state_of_charge: float | None = None
    current_power_w: float | None = None
    energy_kwh: float | None = None
    meter_updated_at: datetime | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None


def normalize_station(raw: Mapping[str, Any]) -> GreenFluxStation:
    """Normalize a charge station object without assuming casing conventions."""
    charger_id = _text(
        _pick(raw, "charge_station_id", "chargeStationId", "chargestation_id", "id")
    )
    if not charger_id:
        raise ValueError("Charge station object does not contain an ID")

    location_id = _text(
        _pick(raw, "location_id", "locationId", "charge_location_id", "chargeLocationId")
    )
    manufacturer = _metadata_text(
        raw,
        "manufacturer",
        "vendor",
        "charge_point_vendor",
        "chargePointVendor",
        "brand",
    )
    model = _metadata_text(raw, "model", "charge_point_model", "chargePointModel")
    serial_number = _metadata_text(
        raw,
        "serial_number",
        "serialNumber",
        "charge_point_serial_number",
        "chargePointSerialNumber",
    )
    firmware_version = _metadata_text(
        raw,
        "firmware_version",
        "firmwareVersion",
        "firmware",
        "software_version",
        "softwareVersion",
    )
    ip_address = _metadata_text(
        raw, "client_ip_address", "clientIpAddress", "ip_address", "ipAddress", "ip"
    )
    station_status = normalize_status(_pick(raw, "status", "state"))
    maintenance_info = _metadata_text(raw, "maintenance_info", "maintenanceInfo")
    (
        station_scheduled_status,
        station_scheduled_start,
        station_scheduled_end,
        station_scheduled_message,
    ) = _next_status_schedule(raw)

    sockets: list[GreenFluxSocket] = []
    evses = _mapping_list(_pick(raw, "evses", "EVSEs", "evse"))
    for evse_index, evse in enumerate(evses, start=1):
        evse_uid = _text(
            _pick(evse, "uid", "evse_uid", "evseUid", "evse_id", "evseId", "id")
        ) or str(evse_index)
        evse_id = _text(_pick(evse, "evse_id", "evseId"))
        evse_status = normalize_status(_pick(evse, "status", "state"))
        connectors = _mapping_list(_pick(evse, "connectors", "connector"))
        if not connectors:
            connectors = [evse]

        for connector_index, connector in enumerate(connectors, start=1):
            connector_id = _text(
                _pick(connector, "connector_id", "connectorId", "id")
            ) or str(connector_index)
            status = normalize_status(_pick(connector, "status", "state"))
            if status == DEFAULT_STATUS:
                status = evse_status if evse_status != DEFAULT_STATUS else station_status
            power_type = _text(
                _first_non_none(
                    _pick(connector, "power_type", "powerType"),
                    _pick(evse, "power_type", "powerType"),
                )
            )
            voltage = _number(
                _first_non_none(
                    _pick(connector, "voltage", "max_voltage", "maxVoltage"),
                    _pick(evse, "voltage", "max_voltage", "maxVoltage"),
                )
            )
            amperage = _number(
                _first_non_none(
                    _pick(
                        connector,
                        "amperage",
                        "max_amperage",
                        "maxAmperage",
                        "max_current",
                        "maxCurrent",
                    ),
                    _pick(
                        evse,
                        "amperage",
                        "max_amperage",
                        "maxAmperage",
                        "max_current",
                        "maxCurrent",
                    ),
                )
            )
            max_power = _number(
                _first_non_none(
                    _pick(
                        connector,
                        "max_electric_power",
                        "maxElectricPower",
                        "max_power",
                        "maxPower",
                    ),
                    _pick(
                        evse,
                        "max_electric_power",
                        "maxElectricPower",
                        "max_power",
                        "maxPower",
                    ),
                )
            )
            schedule_source = connector
            if _pick(connector, "status_schedule", "statusSchedule") is None:
                schedule_source = evse
            (
                socket_scheduled_status,
                socket_scheduled_start,
                socket_scheduled_end,
                socket_scheduled_message,
            ) = _next_status_schedule(schedule_source)
            key = f"{evse_uid}:{connector_id}"
            sockets.append(
                GreenFluxSocket(
                    key=key,
                    ordinal=len(sockets) + 1,
                    evse_uid=evse_uid,
                    evse_id=evse_id,
                    connector_id=connector_id,
                    name=f"Socket {len(sockets) + 1}",
                    status=status,
                    power_type=power_type,
                    voltage=voltage,
                    amperage=amperage,
                    max_power=max_power,
                    next_scheduled_status=socket_scheduled_status,
                    next_scheduled_start=socket_scheduled_start,
                    next_scheduled_end=socket_scheduled_end,
                    next_scheduled_message=socket_scheduled_message,
                )
            )

    return GreenFluxStation(
        charger_id=charger_id,
        location_id=location_id,
        manufacturer=manufacturer,
        model=model,
        serial_number=serial_number,
        firmware_version=firmware_version,
        ip_address=ip_address,
        maintenance_info=maintenance_info,
        next_scheduled_status=station_scheduled_status,
        next_scheduled_start=station_scheduled_start,
        next_scheduled_end=station_scheduled_end,
        next_scheduled_message=station_scheduled_message,
        status=station_status,
        sockets=tuple(sockets),
        raw=raw,
    )


def apply_notifications(
    records: Iterable[Mapping[str, Any]],
    states: dict[tuple[str, str], SocketRuntimeState],
    station_sockets: Mapping[str, tuple[GreenFluxSocket, ...]],
) -> None:
    """Apply OCPP notifications to in-memory per-socket runtime state."""
    ordered = sorted(records, key=_created_sort_key)
    for record in ordered:
        charger_id = _text(
            _pick(record, "charge_station_id", "chargeStationId", "chargestation_id")
        )
        if not charger_id or charger_id not in station_sockets:
            continue

        action = _text(
            _pick(record, "message_action", "MessageName", "messageName")
        ) or ""
        action_key = action.casefold()
        payload = _payload(record)
        evse_uid = _text(
            _pick(record, "evse_id", "evseId", "evse_uid", "evseUid")
        )
        connector_id = _text(_pick(payload, "connector_id", "connectorId"))
        if not connector_id:
            evse_obj = _pick(payload, "evse", "Evse")
            if isinstance(evse_obj, Mapping):
                connector_id = _text(
                    _pick(evse_obj, "connector_id", "connectorId")
                )

        transaction_id = _transaction_id(payload)
        targets = _matching_socket_keys(
            station_sockets[charger_id], evse_uid, connector_id
        )
        if (
            not targets
            and action_key == "stoptransaction"
            and transaction_id is not None
        ):
            targets = [
                socket.key
                for socket in station_sockets[charger_id]
                if states.get((charger_id, socket.key), SocketRuntimeState()).transaction_id
                == transaction_id
            ]
        if not targets:
            continue

        created = _parse_datetime(_pick(record, "created", "timestamp"))
        session_id = _text(_find_recursive(record, "session_id", "sessionId"))

        if action_key == "starttransaction":
            tag_id = _text(_pick(payload, "id_tag", "idTag", "tag_id", "tagId"))
            for key in targets:
                state = states.setdefault((charger_id, key), SocketRuntimeState())
                if tag_id:
                    state.tag_id = tag_id
                    state.last_tag_id = tag_id
                if transaction_id:
                    state.transaction_id = transaction_id
                if session_id:
                    state.session_id = session_id
                state.started_at = created or state.started_at
                state.updated_at = created or state.updated_at
            continue

        if action_key == "stoptransaction":
            for key in targets:
                state = states.setdefault((charger_id, key), SocketRuntimeState())
                state.tag_id = None
                state.transaction_id = None
                state.session_id = None
                state.state_of_charge = None
                state.current_power_w = 0.0
                state.started_at = None
                state.updated_at = created or state.updated_at
            continue

        if action_key == "transactionevent":
            event_type = (
                _text(_pick(payload, "event_type", "eventType")) or ""
            ).casefold()
            token_obj = _pick(payload, "id_token", "idToken")
            state_of_charge = _state_of_charge(payload)
            tag_id = None
            if isinstance(token_obj, Mapping):
                tag_id = _text(_pick(token_obj, "id_token", "idToken", "uid"))
            for key in targets:
                state = states.setdefault((charger_id, key), SocketRuntimeState())
                if event_type == "ended":
                    state.tag_id = None
                    state.transaction_id = None
                    state.session_id = None
                    state.state_of_charge = None
                    state.current_power_w = 0.0
                    state.started_at = None
                else:
                    if tag_id:
                        state.tag_id = tag_id
                        state.last_tag_id = tag_id
                    if transaction_id:
                        state.transaction_id = transaction_id
                    if session_id:
                        state.session_id = session_id
                    if state_of_charge is not None:
                        state.state_of_charge = state_of_charge
                    if event_type == "started":
                        state.started_at = created or state.started_at
                state.updated_at = created or state.updated_at
            continue

        if action_key == "statusnotification":
            status = normalize_status(
                _pick(payload, "status", "connector_status", "connectorStatus")
            )
            for key in targets:
                state = states.setdefault((charger_id, key), SocketRuntimeState())
                state.status = status
                state.updated_at = created or state.updated_at



def apply_meter_values(
    records: Iterable[Mapping[str, Any]],
    states: dict[tuple[str, str], SocketRuntimeState],
    station_sockets: Mapping[str, tuple[GreenFluxSocket, ...]],
) -> None:
    """Apply the latest EVSE MeterValues to per-socket runtime state."""
    samples: dict[
        tuple[str, str, str, datetime],
        list[tuple[str | None, float]],
    ] = {}
    latest_sessions: dict[tuple[str, str], tuple[datetime, str]] = {}

    for record in records:
        charger_id = _text(
            _pick(record, "charge_station_id", "chargeStationId", "chargestation_id")
        )
        if not charger_id or charger_id not in station_sockets:
            continue

        evse_id = _text(_pick(record, "evse_id", "evseId", "evse_uid", "evseUid"))
        connector_id = _text(_pick(record, "connector_id", "connectorId"))
        targets = _matching_meter_socket_keys(
            station_sockets[charger_id], evse_id, connector_id
        )
        if not targets:
            continue

        measurand = (_text(_pick(record, "measurand")) or "").casefold()
        if measurand not in {"poweractiveimport", "energyactiveimportregister"}:
            continue

        value = _number(_pick(record, "value"))
        timestamp = _parse_datetime(
            _first_non_none(_pick(record, "timestamp"), _pick(record, "created"))
        )
        if value is None or timestamp is None:
            continue
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        else:
            timestamp = timestamp.astimezone(timezone.utc)

        unit = (_text(_pick(record, "unit")) or "").casefold()
        normalized = _normalize_meter_value(measurand, value, unit)
        if normalized is None:
            continue
        phase = _text(_pick(record, "phase"))
        session_id = _text(_pick(record, "session_id", "sessionId"))
        for socket_key in targets:
            samples.setdefault(
                (charger_id, socket_key, measurand, timestamp), []
            ).append((phase, normalized))
            if session_id:
                session_key = (charger_id, socket_key)
                previous_session = latest_sessions.get(session_key)
                if previous_session is None or timestamp >= previous_session[0]:
                    latest_sessions[session_key] = (timestamp, session_id)

    latest: dict[tuple[str, str, str], tuple[datetime, float]] = {}
    for (charger_id, socket_key, measurand, timestamp), values in samples.items():
        aggregate = _aggregate_meter_sample(values)
        if aggregate is None:
            continue
        key = (charger_id, socket_key, measurand)
        previous = latest.get(key)
        if previous is None or timestamp >= previous[0]:
            latest[key] = (timestamp, aggregate)

    for (charger_id, socket_key, measurand), (timestamp, value) in latest.items():
        state = states.setdefault((charger_id, socket_key), SocketRuntimeState())
        if measurand == "poweractiveimport":
            state.current_power_w = value
        else:
            state.energy_kwh = value
        if state.meter_updated_at is None or timestamp >= state.meter_updated_at:
            state.meter_updated_at = timestamp

    for (charger_id, socket_key), (timestamp, session_id) in latest_sessions.items():
        state = states.setdefault((charger_id, socket_key), SocketRuntimeState())
        state.session_id = session_id
        if state.meter_updated_at is None or timestamp >= state.meter_updated_at:
            state.meter_updated_at = timestamp


def _matching_meter_socket_keys(
    sockets: tuple[GreenFluxSocket, ...],
    evse_id: str | None,
    connector_id: str | None,
) -> list[str]:
    """Resolve a MeterValue to sockets using either EVSE ID representation."""
    matches = list(sockets)
    if evse_id:
        matches = [
            item
            for item in matches
            if evse_id in {item.evse_uid, item.evse_id}
        ]
        if not matches:
            return []
    if connector_id and connector_id != "0":
        matches = [item for item in matches if item.connector_id == connector_id]
        if not matches:
            return []
    return [item.key for item in matches]


def _normalize_meter_value(measurand: str, value: float, unit: str) -> float | None:
    """Normalize power to watts and cumulative energy to kWh."""
    if measurand == "poweractiveimport":
        factors = {"": 1.0, "w": 1.0, "kw": 1000.0, "mw": 1_000_000.0}
        factor = factors.get(unit)
        return value * factor if factor is not None else None
    if measurand == "energyactiveimportregister":
        factors = {"wh": 0.001, "kwh": 1.0, "mwh": 1000.0}
        factor = factors.get(unit)
        return value * factor if factor is not None else None
    return None


def _aggregate_meter_sample(values: list[tuple[str | None, float]]) -> float | None:
    """Prefer an aggregate meter value; otherwise sum phase-specific values."""
    if not values:
        return None
    aggregate = [
        value
        for phase, value in values
        if phase is None or phase.casefold() in {"", "none", "total"}
    ]
    if aggregate:
        return aggregate[-1]

    per_phase: dict[str, float] = {}
    for phase, value in values:
        if phase:
            per_phase[phase.casefold()] = value
    return sum(per_phase.values()) if per_phase else None


def _next_status_schedule(
    raw: Mapping[str, Any],
) -> tuple[str | None, datetime | None, datetime | None, str | None]:
    """Return the current/next schedule item, or the most recent past item."""
    now = datetime.now(timezone.utc)
    candidates: list[tuple[datetime, datetime | None, str | None, str | None]] = []
    for item in _mapping_list(_pick(raw, "status_schedule", "statusSchedule")):
        start = _parse_datetime(_pick(item, "period_begin", "periodBegin"))
        if start is None:
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        else:
            start = start.astimezone(timezone.utc)
        end = _parse_datetime(_pick(item, "period_end", "periodEnd"))
        if end is not None:
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            else:
                end = end.astimezone(timezone.utc)
        candidates.append(
            (
                start,
                end,
                normalize_status(_pick(item, "status", "state")),
                _text(_pick(item, "status_message", "statusMessage", "message")),
            )
        )
    if not candidates:
        return None, None, None, None

    current_or_future = [
        item for item in candidates if item[1] is None or item[1] >= now
    ]
    if current_or_future:
        start, end, status, message = min(
            current_or_future, key=lambda item: item[0]
        )
    else:
        start, end, status, message = max(candidates, key=lambda item: item[0])

    return status, start, end, message


def _state_of_charge(payload: Mapping[str, Any]) -> float | None:
    """Extract vehicle state of charge when an OCPP payload reports it."""
    direct = _number(
        _first_non_none(
            _pick(payload, "state_of_charge", "stateOfCharge", "soc", "SoC"),
            _find_recursive(payload, "state_of_charge", "stateOfCharge"),
        )
    )
    if direct is not None:
        return direct

    def walk(value: Any) -> float | None:
        if isinstance(value, Mapping):
            measurand = (_text(_pick(value, "measurand")) or "").casefold()
            if measurand in {"soc", "stateofcharge", "state_of_charge"}:
                result = _number(_pick(value, "value"))
                if result is not None:
                    return result
            for child in value.values():
                found = walk(child)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = walk(child)
                if found is not None:
                    return found
        return None

    return walk(payload)

def normalize_status(value: Any) -> str:
    """Map GreenFlux/OCPP statuses to the requested Home Assistant state set."""
    if value is None:
        return DEFAULT_STATUS
    raw = re.sub(r"[^A-Za-z0-9]", "", str(value)).upper()
    direct = {
        "AVAILABLE": "AVAILABLE",
        "BLOCKED": "BLOCKED",
        "CHARGING": "CHARGING",
        "INOPERATIVE": "INOPERATIVE",
        "OUTOFORDER": "OUTOFORDER",
        "PLANNED": "PLANNED",
        "REMOVED": "REMOVED",
        "RESERVED": "RESERVED",
        "UNKNOWN": "UNKNOWN",
    }
    if raw in direct:
        return direct[raw]
    if raw in {
        "PREPARING",
        "SUSPENDEDEV",
        "SUSPENDEDEVSE",
        "FINISHING",
        "OCCUPIED",
    }:
        return "CHARGING"
    if raw == "FAULTED":
        return "OUTOFORDER"
    if raw == "UNAVAILABLE":
        return "INOPERATIVE"
    return DEFAULT_STATUS if DEFAULT_STATUS in STATUS_OPTIONS else "UNKNOWN"


def _matching_socket_keys(
    sockets: tuple[GreenFluxSocket, ...],
    evse_uid: str | None,
    connector_id: str | None,
) -> list[str]:
    """Resolve a notification to one or more sockets without broad false matches."""
    matches = list(sockets)
    if evse_uid:
        matches = [
            item
            for item in matches
            if evse_uid in {item.evse_uid, item.evse_id}
        ]
        if not matches:
            return []
    if connector_id and connector_id != "0":
        matches = [item for item in matches if item.connector_id == connector_id]
        if not matches:
            return []
    return [item.key for item in matches]


def _transaction_id(payload: Mapping[str, Any]) -> str | None:
    """Extract an OCPP 1.6 or OCPP 2.0.1 transaction ID."""
    direct = _text(_pick(payload, "transaction_id", "transactionId"))
    if direct:
        return direct
    transaction_obj = _pick(payload, "transaction_info", "transactionInfo")
    if isinstance(transaction_obj, Mapping):
        return _text(_pick(transaction_obj, "transaction_id", "transactionId"))
    return None


def _payload(record: Mapping[str, Any]) -> Mapping[str, Any]:
    value = _pick(
        record,
        "charge_station_message_payload",
        "chargeStationMessagePayload",
        "payload",
    )
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, Mapping) else {}
    return {}


def _created_sort_key(record: Mapping[str, Any]) -> str:
    value = _pick(record, "created", "timestamp")
    return str(value or "")


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _metadata_text(raw: Mapping[str, Any], *names: str) -> str | None:
    """Read metadata from the top level first, then tolerate nested API shapes."""
    return _text(_first_non_none(_pick(raw, *names), _find_recursive(raw, *names)))


def _find_recursive(value: Any, *names: str) -> Any:
    wanted = {_norm(name) for name in names}
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _norm(str(key)) in wanted and child not in (None, ""):
                return child
            found = _find_recursive(child, *names)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_recursive(child, *names)
            if found not in (None, ""):
                return found
    return None


def _pick(mapping: Mapping[str, Any], *names: str) -> Any:
    normalized = {_norm(str(key)): value for key, value in mapping.items()}
    for name in names:
        key = _norm(name)
        if key in normalized:
            return normalized[key]
    return None


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _mapping_list(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_non_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None
