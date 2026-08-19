"""Compatibility helpers for Home Assistant device APIs."""

from __future__ import annotations

import inspect
from typing import Any

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import UpdateFailed


def socket_device_info(
    *,
    identifiers: set[tuple[str, str]],
    name: str,
    parent_device_id: str,
    parent_identifier: tuple[str, str],
) -> dr.DeviceInfo:
    """Build socket device info using the newest API available in Home Assistant."""
    child_device_info = getattr(dr, "ChildDeviceInfo", None)
    if child_device_info is not None:
        return child_device_info(
            identifiers=identifiers,
            name=name,
            parent_device_id=parent_device_id,
        )

    device_info_annotations = getattr(dr.DeviceInfo, "__annotations__", {})
    if "via_device_id" in device_info_annotations:
        return dr.DeviceInfo(
            identifiers=identifiers,
            name=name,
            via_device_id=parent_device_id,
        )

    return dr.DeviceInfo(
        identifiers=identifiers,
        name=name,
        via_device=parent_identifier,
    )


def charger_parent_kwargs(
    *,
    platform_device_id: str,
    platform_identifier: tuple[str, str],
) -> dict[str, Any]:
    """Return parent-device kwargs supported by this Home Assistant version."""
    annotations = getattr(dr.DeviceInfo, "__annotations__", {})
    if "via_device_id" in annotations:
        return {"via_device_id": platform_device_id}
    return {"via_device": platform_identifier}


def update_failed(message: str, retry_after: float | None = None) -> UpdateFailed:
    """Build UpdateFailed with retry_after when supported by this HA version."""
    if retry_after is not None:
        try:
            parameters = inspect.signature(UpdateFailed).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "retry_after" in parameters:
            return UpdateFailed(message, retry_after=retry_after)
    return UpdateFailed(message)
