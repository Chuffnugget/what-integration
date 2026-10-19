"""Validation of the Windows Dynamic Lighting state sent by the PC."""
import math
import re


def validate_lighting(value, error):
    if not isinstance(value, dict):
        raise error("Invalid Dynamic Lighting state")
    for key in ("enabled", "control_enabled", "cloud"):
        if type(value.get(key)) is not bool:
            raise error("Invalid Dynamic Lighting permission")
    if "status" in value and (not isinstance(value["status"], str) or len(value["status"]) > 512):
        raise error("Invalid Dynamic Lighting status")
    devices = value.get("devices")
    if not isinstance(devices, list) or len(devices) > 64:
        raise error("Invalid Dynamic Lighting devices")
    seen = set()
    for device in devices:
        if not isinstance(device, dict):
            raise error("Invalid Dynamic Lighting device")
        identifier = device.get("id")
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"win_lighting_[a-f0-9]{24}", identifier)
            or identifier in seen
        ):
            raise error("Invalid Dynamic Lighting device ID")
        seen.add(identifier)
        if not isinstance(device.get("name"), str) or len(device["name"]) > 255:
            raise error("Invalid Dynamic Lighting device name")
        if (
            type(device.get("available")) is not bool
            or type(device.get("lamp_count")) is not int
            or not 0 <= device["lamp_count"] <= 65535
        ):
            raise error("Invalid Dynamic Lighting capabilities")
        rgb = device.get("rgb")
        if (
            rgb is not None
            and (
                not isinstance(rgb, list)
                or len(rgb) != 3
                or any(type(c) is not int or not 0 <= c <= 255 for c in rgb)
            )
        ):
            raise error("Invalid Dynamic Lighting RGB")
        brightness = device.get("brightness")
        if (
            brightness is not None
            and (
                type(brightness) not in (int, float)
                or not math.isfinite(brightness)
                or not 0 <= brightness <= 100
            )
        ):
            raise error("Invalid Dynamic Lighting brightness")
