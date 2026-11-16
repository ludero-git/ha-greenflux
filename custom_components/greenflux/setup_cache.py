"""Temporary cache for GreenFlux station data."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from homeassistant.core import HomeAssistant

DATA_SETUP_CACHE = "greenflux_setup_station_cache"


def cache_key(api_url: str, token: str) -> str:
    """Build a cache key for the API credentials."""
    return sha256(f"{api_url}\0{token}".encode()).hexdigest()


def store_station_snapshot(
    hass: HomeAssistant,
    api_url: str,
    token: str,
    stations: list[dict[str, Any]],
) -> None:
    """Store station data already fetched during a config flow."""
    cache = hass.data.setdefault(DATA_SETUP_CACHE, {})
    cache.setdefault(cache_key(api_url, token), []).extend(stations)


def pop_station_snapshot(
    hass: HomeAssistant,
    api_url: str,
    token: str,
) -> list[dict[str, Any]] | None:
    """Consume cached station data during integration setup."""
    cache = hass.data.get(DATA_SETUP_CACHE)
    if not isinstance(cache, dict):
        return None

    snapshot = cache.pop(cache_key(api_url, token), None)
    if not cache:
        hass.data.pop(DATA_SETUP_CACHE, None)
    return snapshot
