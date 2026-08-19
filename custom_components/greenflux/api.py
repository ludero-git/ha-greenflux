"""Async GreenFlux API client."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, urlparse

from aiohttp import ClientError, ClientResponseError, ClientSession

from .const import (
    API_VERSION,
    CHARGE_STATION_LIMIT,
    METER_VALUE_LIMIT,
    METER_VALUE_LOOKBACK,
    NOTIFICATION_LIMIT,
)

REQUEST_TIMEOUT_SECONDS = 30
DEFAULT_RATE_LIMIT_RETRY_SECONDS = 15 * 60


class GreenFluxError(Exception):
    """Base GreenFlux error."""


class GreenFluxAuthError(GreenFluxError):
    """Authentication failed."""


class GreenFluxNotFoundError(GreenFluxError):
    """A requested GreenFlux resource does not exist."""


class GreenFluxRateLimitError(GreenFluxError):
    """GreenFlux rate limit was reached."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class GreenFluxConnectionError(GreenFluxError):
    """Communication with GreenFlux failed."""


class GreenFluxApiError(GreenFluxError):
    """GreenFlux returned an unexpected response."""


def normalize_api_url(value: str) -> str:
    """Normalize the user supplied GreenFlux platform URL."""
    value = value.strip().rstrip("/")
    if not value:
        raise ValueError("API URL is empty")
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Invalid API URL")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


class GreenFluxApiClient:
    """Small aiohttp client for the GreenFlux CPO APIs."""

    def __init__(self, session: ClientSession, api_url: str, token: str) -> None:
        self._session = session
        self.api_url = normalize_api_url(api_url)
        self._token = token.strip()
        self._rate_limited_until = 0.0

    @property
    def host(self) -> str:
        """Return the configured platform host."""
        return urlparse(self.api_url).netloc

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Send one API request and normalize transport-level errors."""
        remaining = self._rate_limited_until - time.monotonic()
        if remaining > 0:
            raise GreenFluxRateLimitError(
                f"GreenFlux rate limit cooldown is active for another {remaining:.0f} seconds.",
                remaining,
            )

        url = f"{self.api_url}{path}"
        headers = {
            "Authorization": f"Token {self._token}",
            "Accept": "application/json",
        }
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                async with self._session.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json,
                ) as response:
                    if response.status in {401, 403}:
                        raise GreenFluxAuthError("GreenFlux rejected the API token")
                    if response.status == 404:
                        raise GreenFluxNotFoundError(
                            "GreenFlux could not find the requested resource"
                        )
                    if response.status == 429:
                        retry_after = _retry_after_seconds(
                            response.headers.get("Retry-After")
                        ) or DEFAULT_RATE_LIMIT_RETRY_SECONDS
                        self._activate_rate_limit(retry_after)
                        raise GreenFluxRateLimitError(
                            f"GreenFlux rate limit reached. Retry after {retry_after:.0f} seconds.",
                            retry_after,
                        )
                    try:
                        response.raise_for_status()
                    except ClientResponseError as err:
                        text = (await response.text()).strip()
                        raise GreenFluxApiError(
                            f"GreenFlux returned HTTP {response.status}: {text[:300]}"
                        ) from err
                    if response.status == 204:
                        return None
                    return await response.json(content_type=None)
        except (
            GreenFluxAuthError,
            GreenFluxNotFoundError,
            GreenFluxRateLimitError,
            GreenFluxApiError,
        ):
            raise
        except (ClientError, TimeoutError) as err:
            raise GreenFluxConnectionError(str(err)) from err
        except ValueError as err:
            raise GreenFluxApiError("GreenFlux returned invalid JSON") from err

    def _activate_rate_limit(self, retry_after: float | None) -> float:
        """Apply a client-wide cooldown after GreenFlux reports a rate limit."""
        delay = max(1.0, retry_after or DEFAULT_RATE_LIMIT_RETRY_SECONDS)
        self._rate_limited_until = max(
            self._rate_limited_until, time.monotonic() + delay
        )
        return delay

    def _raise_for_application_error(self, payload: Any) -> None:
        """Raise an API error and activate cooldown for 2xx rate-limit replies."""
        try:
            _raise_for_application_error(payload)
        except GreenFluxRateLimitError as err:
            delay = self._activate_rate_limit(err.retry_after)
            raise GreenFluxRateLimitError(str(err), delay) from err

    async def async_validate(self) -> None:
        """Validate credentials with the smallest charge-station list request."""
        payload = await self._request(
            "GET",
            f"/api/{API_VERSION}/ChargeStations",
            params={"offset": 0, "limit": 1},
        )
        self._raise_for_application_error(payload)

    async def async_get_charge_station(self, charger_id: str) -> dict[str, Any]:
        """Get one charge station for validation or an explicit lookup."""
        data = await self._request(
            "GET",
            f"/api/{API_VERSION}/ChargeStations/{quote(charger_id, safe='')}",
        )
        self._raise_for_application_error(data)
        return _unwrap_object(data)

    async def async_get_charge_stations(self) -> list[dict[str, Any]]:
        """Get all charge stations, following offset pagination where present."""
        stations: list[dict[str, Any]] = []
        offset = 0
        limit = CHARGE_STATION_LIMIT
        while True:
            payload = await self._request(
                "GET",
                f"/api/{API_VERSION}/ChargeStations",
                params={"offset": offset, "limit": limit},
            )
            self._raise_for_application_error(payload)
            page = _unwrap_list(payload)
            stations.extend(item for item in page if isinstance(item, dict))
            if len(page) < limit:
                break
            offset += len(page)
        return stations

    async def async_get_charge_station_notifications(
        self,
        date_from: datetime,
        date_to: datetime,
    ) -> list[dict[str, Any]]:
        """Get operational charge station notifications for a time range."""
        notifications: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = await self._request(
                "GET",
                f"/api/{API_VERSION}/chargestationnotifications",
                params={
                    "date_from": _iso(date_from),
                    "date_to": _iso(date_to),
                    "offset": offset,
                    "limit": NOTIFICATION_LIMIT,
                },
            )
            self._raise_for_application_error(payload)
            page = _unwrap_list(payload)
            notifications.extend(item for item in page if isinstance(item, dict))
            if len(page) < NOTIFICATION_LIMIT:
                break
            offset += len(page)
        return notifications

    async def async_get_meter_values(
        self,
        date_from: datetime,
        date_to: datetime,
    ) -> list[dict[str, Any]]:
        """Get meter values for a short time range."""
        values: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = await self._request(
                "GET",
                f"/api/{API_VERSION}/metervalues",
                params={
                    "date_from": _iso(date_from),
                    "date_to": _iso(date_to),
                    "offset": offset,
                    "limit": METER_VALUE_LIMIT,
                },
            )
            self._raise_for_application_error(payload)
            page = _unwrap_list(payload)
            if not page and isinstance(payload, dict) and isinstance(
                payload.get("data"), dict
            ):
                page = [payload["data"]]
            values.extend(item for item in page if isinstance(item, dict))
            if len(page) < METER_VALUE_LIMIT:
                break
            offset += len(page)
        return values

    async def async_find_recent_session_id(
        self,
        *,
        charger_id: str,
        evse_uid: str,
        connector_id: str | int,
        not_before: datetime | None = None,
    ) -> str | None:
        """Resolve a GreenFlux session ID from recent MeterValues on demand."""
        now = datetime.now(timezone.utc)
        date_from = now - METER_VALUE_LOOKBACK
        if not_before is not None:
            if not_before.tzinfo is None:
                not_before = not_before.replace(tzinfo=timezone.utc)
            else:
                not_before = not_before.astimezone(timezone.utc)
            date_from = max(date_from, not_before)
        records = await self.async_get_meter_values(date_from, now)
        wanted_connector = str(connector_id)
        matches: list[tuple[str, str]] = []
        for record in records:
            record_charger = _recursive_value(
                record, "charge_station_id", "chargeStationId"
            )
            record_evse = _recursive_value(record, "evse_id", "evseId")
            record_connector = _recursive_value(
                record, "connector_id", "connectorId", "connector_ID"
            )
            session_id = _recursive_value(record, "session_id", "sessionId")
            if (
                str(record_charger or "") == charger_id
                and str(record_evse or "") == evse_uid
                and str(record_connector or "") == wanted_connector
                and session_id not in (None, "")
            ):
                timestamp = _recursive_value(record, "created", "timestamp")
                matches.append((str(timestamp or ""), str(session_id)))
        if not matches:
            return None
        matches.sort()
        return matches[-1][1]

    async def async_start_session(
        self,
        *,
        charger_id: str,
        location_id: str,
        evse_uid: str,
        connector_id: str | int | None,
        token_uid: str | None = None,
        auth_id: str | None = None,
    ) -> dict[str, Any]:
        """Send START_SESSION."""
        token: dict[str, Any] = {
            "uid": token_uid,
            "auth_id": auth_id,
            "valid": True,
        }
        body: dict[str, Any] = {
            "token": token,
            "location_id": location_id,
            "evse_uid": evse_uid,
            "chargestation_id": charger_id,
        }
        if connector_id is not None:
            body["connector_id"] = str(connector_id)
        return _as_dict(
            await self._request(
                "POST", f"/api/{API_VERSION}/remotecommands/START_SESSION", json=body
            )
        )

    async def async_stop_session(self, session_id: str) -> dict[str, Any]:
        """Send STOP_SESSION."""
        return _as_dict(
            await self._request(
                "POST",
                f"/api/{API_VERSION}/remotecommands/STOP_SESSION",
                json={"session_id": session_id},
            )
        )

    async def async_unlock_connector(
        self,
        *,
        location_id: str,
        evse_uid: str,
        connector_id: str | int,
    ) -> dict[str, Any]:
        """Send UNLOCK_CONNECTOR."""
        return _as_dict(
            await self._request(
                "POST",
                f"/api/{API_VERSION}/remotecommands/UNLOCK_CONNECTOR",
                json={
                    "location_id": location_id,
                    "evse_uid": evse_uid,
                    "connector_id": str(connector_id),
                },
            )
        )

    async def async_reset(
        self,
        *,
        charger_id: str,
        evse_uid: str | None = None,
        reset_type: str = "Soft",
        scheduled: str = "Immediate",
    ) -> dict[str, Any]:
        """Send RESET."""
        body: dict[str, Any] = {
            "charge_station_id": charger_id,
            "evse_uid": evse_uid,
            "type": reset_type,
            "scheduled": scheduled,
        }
        return _as_dict(
            await self._request(
                "POST", f"/api/{API_VERSION}/remotecommands/RESET", json=body
            )
        )

    async def async_get_command_notification(
        self, evse_uid: str, notification_id: str
    ) -> dict[str, Any]:
        """Get the actual charge station response to a remote command."""
        payload = await self._request(
            "GET",
            f"/api/{API_VERSION}/remotecommands/"
            f"{quote(evse_uid, safe='')}/{quote(notification_id, safe='')}",
        )
        self._raise_for_application_error(payload)
        return _as_dict(payload)


def command_result(response: dict[str, Any]) -> str | None:
    """Return the initial GreenFlux remote-command result when present."""
    value = _recursive_value(response, "result")
    return str(value).strip() if value not in (None, "") else None


def command_notification_id(response: dict[str, Any]) -> str | None:
    """Return a remote-command notification ID from common response shapes."""
    value = _recursive_value(
        response,
        "charge_station_notification_id",
        "chargeStationNotificationId",
        "message_id",
        "messageId",
    )
    return str(value).strip() if value not in (None, "") else None


def raise_for_command_rejection(response: dict[str, Any]) -> None:
    """Raise when a GreenFlux remote command was not accepted for processing."""
    if "status_code" in response:
        try:
            status_code = int(response["status_code"])
        except (TypeError, ValueError):
            status_code = 1000
        if status_code != 1000:
            message = (
                response.get("status_message")
                or response.get("message")
                or "remote command failed"
            )
            raise GreenFluxApiError(
                f"GreenFlux returned status {status_code}: {message}"
            )

    result = command_result(response)
    if result is not None and result.upper() != "ACCEPTED":
        raise GreenFluxApiError(f"GreenFlux rejected the remote command: {result}")


def _iso(value: datetime) -> str:
    """Format a UTC-aware datetime for query parameters."""
    return value.isoformat().replace("+00:00", "Z")


def _retry_after_seconds(value: str | None) -> float | None:
    """Parse Retry-After as seconds or an HTTP date."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        now = datetime.now(retry_at.tzinfo)
        return max(0.0, (retry_at - now).total_seconds())


def _raise_for_application_error(payload: Any) -> None:
    """Raise for GreenFlux application errors returned inside HTTP 2xx."""
    if not isinstance(payload, dict) or "status_code" not in payload:
        return
    try:
        status_code = int(payload["status_code"])
    except (TypeError, ValueError):
        return
    if status_code == 1000:
        return
    message = payload.get("status_message") or payload.get("message") or "API error"
    rendered = f"GreenFlux returned status {status_code}: {message}"
    if "rate limit" in str(message).casefold():
        raise GreenFluxRateLimitError(
            rendered, DEFAULT_RATE_LIMIT_RETRY_SECONDS
        )
    raise GreenFluxApiError(rendered)


def _unwrap_list(payload: Any) -> list[Any]:
    """Return a list from common GreenFlux response envelopes."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "results", "charge_stations", "chargeStations"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _unwrap_object(payload: Any) -> dict[str, Any]:
    """Return one object from common GreenFlux response envelopes."""
    if not isinstance(payload, dict):
        raise GreenFluxApiError("GreenFlux did not return an object")
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        return data[0]
    return payload


def _as_dict(payload: Any) -> dict[str, Any]:
    """Normalize an API response to a dictionary."""
    if payload is None:
        return {}
    if isinstance(payload, dict):
        return payload
    return {"response": payload}


def _recursive_value(value: Any, *names: str) -> Any:
    """Find a named value in a small nested API response."""
    wanted = {name.casefold() for name in names}
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() in wanted and child not in (None, ""):
                return child
            found = _recursive_value(child, *names)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for child in value:
            found = _recursive_value(child, *names)
            if found not in (None, ""):
                return found
    return None
