"""Validation of the BEACN control list sent by the PC."""
import math
import re

def validate_beacn(state, error=ValueError):
    def reject():
        raise error("Invalid BEACN state")
    if not isinstance(state, dict):
        reject()
    for key in ("enabled", "connected", "ready", "control_enabled", "cloud", "busy", "reconnect_pending"):
        if key in state and type(state[key]) is not bool:
            reject()
    for key in ("status", "command_status", "last_command"):
        if key in state and (not isinstance(state[key], str) or len(state[key]) > 8192):
            reject()
    unsupported = state.get("unsupported_controls", [])
    if (not isinstance(unsupported, list) or len(unsupported) > 128
            or any(
                (not isinstance(v, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", v) for v in unsupported),
            )
            or len(set(unsupported)) != len(unsupported)):
        reject()
    controls = state.get("controls", [])
    if not isinstance(controls, list) or len(controls) > 128:
        reject()
    seen = set()
    for c in controls:
        if not isinstance(c, dict):
            reject()
        identifier = c.get("id")
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", identifier)
            or identifier in seen
        ):
            reject()
        if identifier in unsupported:
            reject()
        seen.add(identifier)
        if (
            not isinstance(c.get("name"), str)
            or len(c["name"]) > 512
            or type(c.get("available")) is not bool
        ):
            reject()
        kind, value = c.get("kind"), c.get("value")
        if kind not in ("number", "select", "switch", "button"):
            reject()
        if kind == "number":
            for key in ("min", "max", "step"):
                if type(c.get(key)) not in (int, float) or not math.isfinite(c[key]):
                    reject()
            if c["min"] > c["max"] or c["step"] <= 0:
                reject()
            if (
                value is not None
                and (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or not c["min"] <= value <= c["max"]
                )
            ):
                reject()
        if kind == "switch" and value is not None and type(value) is not bool:
            reject()
        if kind == "select":
            options = c.get("options")
            if (
                not isinstance(options, list)
                or len(options) > 128
                or any(not isinstance(v, str) or len(v) > 512 for v in options)
            ):
                reject()
            if value is not None and (not isinstance(value, str) or len(value) > 512):
                reject()
    # Bound nested read-only diagnostics too; never expose arbitrary remote objects.
    def bounded(value, depth=0):
        if depth > 5:
            reject()
        if isinstance(value, dict):
            if len(value) > 128:
                reject()
            for key, item in value.items():
                if not isinstance(key, str) or len(key) > 128:
                    reject()
                bounded(item, depth + 1)
        elif isinstance(value, list):
            if len(value) > 128:
                reject()
            for item in value:
                bounded(item, depth + 1)
        elif isinstance(value, str):
            if len(value) > 8192:
                reject()
        elif type(value) in (int, float):
            if not math.isfinite(value):
                reject()
        elif value is not None and type(value) is not bool:
            reject()
    bounded(state)
    if not isinstance(state.get("global", {}), dict) or not isinstance(state.get("channels", []), list):
        reject()
    for channel in state.get("channels", []):
        if (
            not isinstance(channel, dict)
            or type(channel.get("id")) is not int
            or not 0 <= channel["id"] < 32
        ):
            reject()
