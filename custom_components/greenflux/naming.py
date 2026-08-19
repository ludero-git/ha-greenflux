"""Naming helpers for GreenFlux Home Assistant objects."""

from homeassistant.util import slugify


def platform_prefix(platform_number: int) -> str:
    """Return the stable prefix for one configured GreenFlux platform."""
    return f"p{platform_number}"


def charger_prefix(platform_number: int, charger_id: str) -> str:
    """Return the stable prefix for one charger."""
    return f"{platform_prefix(platform_number)}_{slugify(charger_id)}"


def socket_prefix(platform_number: int, charger_id: str, socket_ordinal: int) -> str:
    """Return the stable prefix for one charger socket."""
    return f"{charger_prefix(platform_number, charger_id)}_s{socket_ordinal}"


def charger_name(platform_number: int, charger_id: str) -> str:
    """Return the display name for one charger."""
    return f"P{platform_number} {charger_id}"


def socket_name(platform_number: int, charger_id: str, socket_ordinal: int) -> str:
    """Return the display name for one socket."""
    return f"{charger_name(platform_number, charger_id)} Socket {socket_ordinal}"
