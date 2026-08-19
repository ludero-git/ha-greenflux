"""Short-lived setup cache used to avoid duplicate GreenFlux bulk reads."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from homeassistant.core import HomeAssistant

DATA_SETUP_CACHE = "greenflux_setup_station_cache"


def _key(api_url: str, token: str) -> str:
    """Build a non-secret cache key for one platform credential pair."""
    value = f"{api_url}\0{token}".encode()
    return sha256(value).hexdigest()


def store_station_snapshot(
    hass: HomeAssistant,
    api_url: str,
    token: str,
    stations: list[dict[str, Any]],
) -> None:
    """Store station data fetched by an auto-create config flow."""
    cache = hass.data.setdefault(DATA_SETUP_CACHE, {})
    cache[_key(api_url, token)] = stations


def pop_station_snapshot(
    hass: HomeAssistant,
    api_url: str,
    token: str,
) -> list[dict[str, Any]] | None:
    """Consume a station snapshot when the new config entry is set up."""
    cache = hass.data.get(DATA_SETUP_CACHE)
    if not isinstance(cache, dict):
        return None
    snapshot = cache.pop(_key(api_url, token), None)
    if not cache:
        hass.data.pop(DATA_SETUP_CACHE, None)
    return snapshot
