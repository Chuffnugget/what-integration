"""Parse and validate frames from the PC before any of it reaches entities."""
from __future__ import annotations

import math
import json
from datetime import datetime
import re
from typing import Any
from .beacn_protocol import validate_beacn
from .displays_protocol import validate_displays
from .dynamic_lighting import validate_lighting
from .icue_protocol import validate_icue as _validate_icue
from .icue_automation_protocol import validate_automation
from .custom_protocol import validate_custom

MAX_MESSAGE = 8 * 1024 * 1024
MAX_SENSORS = 10000
SOURCES = ("hwinfo", "windows", "icue", "nvidia", "coretemp", "beacn", "custom")
KINDS = ("number", "text", "binary", "timestamp")


class ProtocolError(Exception):
    """The remote app sent an unsupported or invalid message."""


class IncompatibleProtocolError(ProtocolError):
    """The app and integration use different protocol versions."""


class IdentityMismatchError(ProtocolError):
    """A verified endpoint belongs to a different PC."""


def decode_json(payload, maximum=MAX_MESSAGE):
    """Reject ambiguous JSON, non-finite numbers and oversized/nested frames."""
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("Non-finite JSON value")

    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("Non-finite JSON value")
        return parsed

    try:
        if not isinstance(payload, (str, bytes)) or len(payload) > maximum:
            raise ValueError("Invalid JSON size")
        result = json.loads(payload, object_pairs_hook=object_pairs, parse_constant=constant, parse_float=number)
        containers = [(result, 0)]
        while containers:
            value, depth = containers.pop()
            if depth > 32:
                raise ValueError("JSON nesting exceeds protocol limit")
            children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
            containers.extend((child, depth + 1) for child in children if isinstance(child, (dict, list)))
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError) as err:
        raise ProtocolError("Invalid JSON frame") from err


def fingerprint_bytes(value: str) -> bytes:
    """Only accept a complete, manually supplied SHA-256 fingerprint."""
    value = value.replace(":", "").replace(" ", "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Expected a 32-byte SHA-256 certificate fingerprint")
    return bytes.fromhex(value)


def _text(data: dict, key: str, maximum: int = 512) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ProtocolError(f"Invalid {key}")
    return value


def _validate_app(app: Any) -> None:
    if not isinstance(app, dict) or type(app.get("version")) is not int or app["version"] != 1:
        raise ProtocolError("Invalid WHAT app metadata")
    for key in ("control_enabled", "logging", "restart_available"):
        if type(app.get(key)) is not bool:
            raise ProtocolError("Invalid WHAT app permission or setting")
    normal, high = app.get("publishing_interval_ms"), app.get("high_interval_ms")
    if (type(normal) is not int or type(high) is not int
            or not 100 <= normal <= 60000 or not 50 <= high <= normal):
        raise ProtocolError("Invalid WHAT publishing intervals")
    for key, maximum in (("uptime_seconds", 2**53), ("cpu_percent", 100), ("memory_bytes", 2**53)):
        value = app.get(key)
        if value is None and key != "uptime_seconds":
            continue
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
            raise ProtocolError("Invalid WHAT process diagnostic")
    update = app.get("update")
    if (not isinstance(update, dict) or not isinstance(update.get("channel"), str)
            or not re.fullmatch(r"[a-z]{1,16}", update["channel"]) or type(update.get("busy")) is not bool):
        raise ProtocolError("Invalid WHAT update state")
    version_pattern = r"[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}(?:-beta\.[0-9]{1,5})?"
    for key in ("installed", "latest"):
        value = update.get(key)
        if key == "latest" and value is None:
            continue
        if not isinstance(value, str) or not re.fullmatch(version_pattern, value):
            raise ProtocolError("Invalid WHAT update version")
    latest = update.get("latest")
    # The update entity links to this URL, so only accept the release page for the
    # reported version. Anything else is dropped rather than failing the frame.
    expected_url = (
        f"https://github.com/Chuffnugget/what-windows/releases/tag/v{latest}"
        if latest else None
    )
    if update.get("release_url") != expected_url:
        update["release_url"] = None
    checked = update.get("checked_at")
    if checked is not None:
        try:
            if not isinstance(checked, str) or len(checked) > 40 or datetime.fromisoformat(checked).tzinfo is None:
                raise ValueError
        except ValueError as err:
            raise ProtocolError("Invalid WHAT update timestamp") from err


CONNECTION_FIELDS = ("id", "name", "type", "status", "scope", "connected_since", "last_reply")
CONNECTION_STATES = ("connected", "disabled", "stopped", "waiting", "connecting", "retrying", "error")


def _validate_connections(rows: Any) -> list:
    """Check the PC's list of Home Assistant installations.

    Rows with a type, status or scope this version doesn't know are skipped, and
    unknown keys are dropped, so a newer app doesn't disconnect an older integration.
    """
    if not isinstance(rows, list) or len(rows) > MAX_SENSORS:
        raise ProtocolError("Invalid installation connections")
    identifiers = set()
    accepted = []
    for row in rows:
        if not isinstance(row, dict) or any(key not in row for key in CONNECTION_FIELDS):
            raise ProtocolError("Invalid installation connection fields")
        row = {key: row[key] for key in CONNECTION_FIELDS}
        identifier = row["id"]
        name = row["name"]
        if (not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier)
                or identifier in identifiers or not isinstance(name, str)
                or not name.strip() or len(name) > 80 or any(ord(char) < 32 for char in name)):
            raise ProtocolError("Invalid installation identity")
        identifiers.add(identifier)
        for key in ("connected_since", "last_reply"):
            if type(row[key]) is not int or not 0 <= row[key] <= 253402300799000:
                raise ProtocolError("Invalid installation timestamp")
        if (row["type"] in ("api", "mqtt") and row["status"] in CONNECTION_STATES
                and row["scope"] in ("none", "local", "cloud", "hybrid")):
            accepted.append(row)
    return accepted


def validate_message(data: Any, kind: str, device_id: str | None = None) -> dict:
    """Return a validated message; reject duplicates, NaN and incompatible versions."""
    if isinstance(data, dict) and type(data.get("protocol")) is int and data["protocol"] != 2:
        raise IncompatibleProtocolError("Unsupported protocol version")
    if (
        not isinstance(data, dict)
        or data.get("type") != kind
        or type(data.get("protocol")) is not int
        or data["protocol"] != 2
    ):
        raise ProtocolError("Unsupported message type or protocol version")
    identity = _text(data, "device_id", 128)
    if device_id is not None and identity != device_id:
        raise IdentityMismatchError("The PC identity changed; re-pair the intended PC")
    if kind == "hello":
        _text(data, "name")
        _text(data, "app_version", 64)
        for capability in ("delta_v1", "commands_v1", "transport_switch_v1", "command_deadline_v1"):
            if capability in data and type(data[capability]) is not bool:
                raise ProtocolError("Invalid protocol capability")
        return data
    if kind != "snapshot":
        raise ProtocolError("Unknown message kind")
    # Early beta apps sent these settings as "true"/"false" strings. Accept those
    # two spellings only; anything else is still rejected rather than guessed.
    for module, keys in (("wallpaper", ("control_enabled", "share_path", "cloud")),
                         ("beacn", ("control_enabled", "cloud"))):
        state = data.get(module)
        if isinstance(state, dict):
            for key in keys:
                value = state.get(key)
                if type(value) is str and value in ("true", "false"):
                    state[key] = value == "true"
    if "app" in data:
        _validate_app(data["app"])
    if "connections" in data:
        data["connections"] = _validate_connections(data["connections"])
    if "connection_options" in data:
        choices = data["connection_options"]
        if (not isinstance(choices, dict)
                or any(type(choices.get(key)) is not bool for key in ("remote_enabled", "mqtt_enabled"))):
            raise ProtocolError("Invalid connection options")
        data["connection_options"] = {key: choices[key] for key in ("remote_enabled", "mqtt_enabled")}
    if "connection_config" in data:
        config = data["connection_config"]
        fields = ("revision", "preferred", "remote_enabled", "mqtt_enabled",
                  "direct_host", "direct_port", "fingerprint")
        if isinstance(config, dict) and all(key in config for key in fields):
            config = data["connection_config"] = {key: config[key] for key in fields}
        if (not isinstance(config, dict) or set(config) != set(fields)
                or not isinstance(config["revision"], str) or not re.fullmatch(r"[a-f0-9]{32}", config["revision"])
                or config["preferred"] not in ("api", "mqtt")
                or type(config["remote_enabled"]) is not bool or type(config["mqtt_enabled"]) is not bool
                or not isinstance(config["direct_host"], str) or len(config["direct_host"]) > 253
                or any(ord(char) < 32 or char.isspace() or char in "/@?#" for char in config["direct_host"])
                or type(config["direct_port"]) is not int or not 1 <= config["direct_port"] <= 65535
                or not isinstance(config["fingerprint"], str) or not re.fullmatch(r"[a-f0-9]{64}", config["fingerprint"])):
            raise ProtocolError("Invalid connection configuration")
    if "selected_sensor_ids" in data:
        selected = data["selected_sensor_ids"]
        if (not isinstance(selected, list) or len(selected) > MAX_SENSORS
            or any(not isinstance(v, str) or not v or len(v) > 128 for v in selected)
            or len(set(selected)) != len(selected)):
            raise ProtocolError("Invalid selected sensor catalogue")
    if "beacn" in data:
        validate_beacn(data["beacn"], ProtocolError)
    if "custom" in data:
        validate_custom(data["custom"], ProtocolError)
    if "announcements" in data:
        state = data["announcements"]
        fields = ("enabled", "state", "volume", "muted", "error")
        if isinstance(state, dict) and all(key in state for key in fields):
            state = data["announcements"] = {key: state[key] for key in fields}
            if not isinstance(state["state"], str) or not 1 <= len(state["state"]) <= 64:
                raise ProtocolError("Invalid announcement state")
            if state["state"] not in ("idle", "playing", "paused", "buffering"):
                state["state"] = "idle"
        if (not isinstance(state, dict) or set(state) != set(fields)
                or type(state["enabled"]) is not bool or type(state["muted"]) is not bool
                or type(state["volume"]) not in (int, float) or not math.isfinite(state["volume"])
                or not 0 <= state["volume"] <= 1
                or not isinstance(state["error"], str) or len(state["error"]) > 256):
            raise ProtocolError("Invalid announcement player metadata")
    if "notifications" in data:
        notifications = data["notifications"]
        if (not isinstance(notifications, dict) or set(notifications) != {"version", "events"}
                or type(notifications["version"]) is not int or notifications["version"] != 1
                or not isinstance(notifications["events"], list) or len(notifications["events"]) > 32):
            raise ProtocolError("Invalid notification metadata")
        seen = set()
        event_fields = ("id", "notification_id", "action", "reply", "created")
        notifications["events"] = [
            {key: event[key] for key in event_fields} if isinstance(event, dict) and all(key in event for key in event_fields)
            else event
            for event in notifications["events"]
        ]
        for event in notifications["events"]:
            if (not isinstance(event, dict) or set(event) != set(event_fields)
                    or not isinstance(event["id"], str) or not re.fullmatch(r"[a-f0-9]{32}", event["id"])
                    or event["id"] in seen or not isinstance(event["notification_id"], str)
                    or not 1 <= len(event["notification_id"]) <= 64
                    or not isinstance(event["action"], str) or not 1 <= len(event["action"]) <= 64
                    or not isinstance(event["reply"], str) or len(event["reply"]) > 256
                    or type(event["created"]) not in (int, float) or not math.isfinite(event["created"])
                    or not 0 < event["created"] < 2 ** 53):
                raise ProtocolError("Invalid notification action event")
            seen.add(event["id"])
    if "wallpaper" in data:
        wallpaper = data["wallpaper"]
        if not isinstance(wallpaper, dict):
            raise ProtocolError("Invalid Wallpaper Engine state")
        for key in ("enabled", "running", "installed", "control_enabled", "cloud", "share_path", "busy"):
            if key in wallpaper and type(wallpaper[key]) is not bool:
                raise ProtocolError("Invalid Wallpaper Engine flag")
        for key in ("status", "current_wallpaper"):
            if key in wallpaper and (not isinstance(wallpaper[key], str) or len(wallpaper[key]) > 8192):
                raise ProtocolError("Invalid Wallpaper Engine text")
    if "dynamic_lighting" in data:
        validate_lighting(data["dynamic_lighting"], ProtocolError)
    if "displays" in data:
        validate_displays(data["displays"], ProtocolError)
    if "icue_automation" in data:
        validate_automation(data["icue_automation"], ProtocolError)
    if "icue" in data:
        _validate_icue(data["icue"], ProtocolError)
    if "controls" in data:
        controls = data["controls"]
        if (
            not isinstance(controls, dict)
            or type(controls.get("version")) is not int
            or controls["version"] != 1
        ):
            raise ProtocolError("Invalid controls metadata")
        if "notifications_enabled" in controls and type(controls["notifications_enabled"]) is not bool:
            raise ProtocolError("Invalid notification permission")
        if "input_enabled" in controls and type(controls["input_enabled"]) is not bool:
            raise ProtocolError("Invalid input permission")
        permissions = controls.get("permissions")
        if "shell_enabled" in controls and type(controls["shell_enabled"]) is not bool:
            raise ProtocolError("Invalid shell permission")
        if not isinstance(permissions, dict) or any(type(v) is not bool for v in permissions.values()):
            raise ProtocolError("Invalid command permissions")
        # Permissions this version doesn't know are ignored; missing ones count as off.
        controls["permissions"] = {key: permissions.get(key, False)
                                   for key in ("media", "power", "power_plan", "urls", "monitor")}
        values = controls.get("values")
        if not isinstance(values, dict) or len(values) > 32:
            raise ProtocolError("Invalid control state")
        values = controls["values"] = {
            key: value for key, value in values.items()
            if isinstance(key, str) and len(key) <= 64
            and (key.startswith("win_media_") or key in {"win_audio_volume", "win_audio_muted"})
        }
        for key, value in values.items():
            if not ((
                type(value) is bool
                or (isinstance(value, str) and len(value) <= 512)
                or (type(value) in (int, float) and math.isfinite(value))
            )):
                raise ProtocolError("Invalid control value")
            if (
                (key.startswith("win_media_can_") or key in {"win_audio_muted", "win_media_shuffle"})
                and type(value) is not bool
            ):
                raise ProtocolError("Invalid control boolean")
            if (
                key in {"win_audio_volume", "win_media_position", "win_media_duration"}
                and (type(value) not in (int, float) or value < 0)
            ):
                raise ProtocolError("Invalid control number")
            if key == "win_audio_volume" and value > 100:
                raise ProtocolError("Invalid volume")
        power = controls.get("power")
        if (
            not isinstance(power, dict)
            or not isinstance(power.get("plans", []), list)
            or len(power.get("plans", [])) > 64
        ):
            raise ProtocolError("Invalid power plans")
        for plan in power.get("plans", []):
            if not isinstance(plan, dict):
                raise ProtocolError("Invalid power plan")
            _text(plan, "id", 38)
            _text(plan, "name", 256)
        if not isinstance(power.get("active", ""), str) or len(power.get("active", "")) > 38:
            raise ProtocolError("Invalid active power plan")
        modes = power.get("modes", {})
        if not isinstance(modes, dict):
            raise ProtocolError("Invalid power modes")
        if "modes" in power:
            power["modes"] = {supply: mode for supply, mode in modes.items()
                              if supply in ("ac", "dc")
                              and mode in ("Best power efficiency", "Balanced", "Best performance")}
        if "mode_keys" in power:
            keys = power["mode_keys"]
            if not isinstance(keys, dict):
                raise ProtocolError("Invalid power mode keys")
            power["mode_keys"] = {
                supply: mode for supply, mode in keys.items()
                if supply in ("ac", "dc") and mode in ("efficiency", "balanced", "performance")
            }
    if type(data.get("healthy")) is not bool:
        raise ProtocolError("Invalid source health")
    for field in ("hwinfo_enabled", "windows_healthy"):
        if type(data.get(field)) is not bool:
            raise ProtocolError(f"Invalid {field}")
    _text(data, "status", 2048)
    if not isinstance(data.get("license"), str):
        raise ProtocolError("Invalid self-reported licence")
    if data["license"] not in ("free", "pro", "unknown"):
        data["license"] = "unknown"
    for field in ("session_ms", "sent_at"):
        if field in data:
            value = data[field]
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value < 2 ** 53:
                raise ProtocolError(f"Invalid {field}")
    for field in ("sample_time", "sample_age", "publish_seconds"):
        value = data.get(field)
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ProtocolError(f"Invalid {field}")
    if not 1 <= data["publish_seconds"] <= 300 or data["sample_age"] < -1 or data["sample_time"] < 0:
        raise ProtocolError("Invalid sample timing")
    rows = data.get("sensors")
    if not isinstance(rows, list) or len(rows) > MAX_SENSORS:
        raise ProtocolError("Invalid sensor list")
    # Readings from a source, kind or state class this version doesn't know are
    # skipped instead of rejecting the whole frame.
    rows = data["sensors"] = [
        row for row in rows
        if not isinstance(row, dict) or (
            row.get("source") in SOURCES and row.get("kind") in KINDS
            and row.get("state_class", "") in ("", "measurement", "total_increasing"))
    ]
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ProtocolError("Invalid sensor")
        identity = _text(row, "id", 128)
        if identity in seen:
            raise ProtocolError("Duplicate sensor identifier")
        seen.add(identity)
        _text(row, "name")
        # HWiNFO can supply an empty group or unit for an otherwise valid reading.
        for field in ("group", "unit"):
            if not isinstance(row.get(field), str) or len(row[field]) > 512:
                raise ProtocolError(f"Invalid sensor {field}")
        if type(row.get("type")) is not int:
            raise ProtocolError("Invalid reading type")
        if type(row.get("available")) is not bool:
            raise ProtocolError("Invalid reading availability")
        kind = row["kind"]
        if kind != "number" and (row["unit"] or row.get("state_class")):
            raise ProtocolError("Non-numeric reading has numeric metadata")
        value = row.get("value")
        if value is None:
            if row["available"]:
                raise ProtocolError("Available reading has no value")
            continue
        if not row["available"]:
            raise ProtocolError("Unavailable reading contains a value")
        if kind == "number" and (type(value) not in (int, float) or not math.isfinite(value)):
            raise ProtocolError("Invalid numeric value")
        if kind == "binary" and type(value) is not bool:
            raise ProtocolError("Invalid binary value")
        if kind in ("text", "timestamp") and (not isinstance(value, str) or len(value) > 255):
            raise ProtocolError("Invalid text value")
        if kind == "timestamp":
            try:
                stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    raise ValueError("Missing time zone")
            except ValueError as err:
                raise ProtocolError("Invalid timestamp") from err
    return data


class StreamFrame(dict):
    """Validated local hints; never accept these attributes from wire JSON."""

    def __init__(self, payload, readings, baseline, changed_ids):
        super().__init__(payload)
        self.readings = readings
        self.baseline = baseline
        self.changed_ids = changed_ids


class StreamState:
    """Reconstruct negotiated deltas, rejecting gaps before mutating state."""

    def __init__(self):
        self.rows = {}
        self.sensor_list = []
        self.sequence = 0
        self.icue = None
        self.icue_automation = None

    def accept(self, data, device_id, deltas=False):
        if not deltas:
            return validate_message(data, "snapshot", device_id)
        if not isinstance(data, dict):
            raise ProtocolError("Invalid stream frame")
        seq = data.get("sequence")
        if type(seq) is not int or seq != self.sequence + 1:
            raise ProtocolError("Stream sequence gap; reconnect required")
        kind = data.get("type")
        if kind == "delta":
            if (
                not self.sequence
                or type(data.get("base_sequence")) is not int
                or data["base_sequence"] != self.sequence
            ):
                raise ProtocolError("Delta has no matching baseline")
            removed = data.get("removed")
            if (
                not isinstance(removed, list)
                or len(removed) > MAX_SENSORS
                or any(not isinstance(i, str) for i in removed)
                or len(set(removed)) != len(removed)
            ):
                raise ProtocolError("Invalid removed readings")
            # Readings skipped as unknown never entered the baseline.
            removed = [i for i in removed if i in self.rows]
            validated = validate_message({**data, "type": "snapshot"}, "snapshot", device_id)
            changes = {r["id"]: r for r in validated["sensors"]}
            if changes.keys() & set(removed):
                raise ProtocolError("Reading both changed and removed")
            removed_ids = set(removed)
            changed_ids = {key for key, row in changes.items() if self.rows.get(key) != row} | removed_ids
            # Heartbeats and unchanged values retain the immutable baseline.
            # Changed frames copy the dictionary in C rather than rebuilding it
            # in a Python loop; previous coordinator frames remain untouched.
            rows = self.rows
            if changed_ids:
                rows = self.rows.copy()
                for key in removed_ids:
                    del rows[key]
                rows.update(changes)
        elif kind == "snapshot":
            validated = validate_message(data, "snapshot", device_id)
            rows = {r["id"]: r for r in validated["sensors"]}
        else:
            raise ProtocolError("Unsupported stream frame")
        if len(rows) > MAX_SENSORS:
            raise ProtocolError("Too many accumulated readings")
        baseline = self.rows
        if kind != "delta":
            changed_ids = None
        if rows is not baseline:
            self.sensor_list = list(rows.values())
        self.rows, self.sequence = rows, seq
        if "icue" in validated or kind == "snapshot":
            self.icue = validated.get("icue")
        if "icue_automation" in validated or kind == "snapshot":
            self.icue_automation = validated.get("icue_automation")
        return StreamFrame(
            {
                **validated,
                "type": "snapshot",
                "sensors": self.sensor_list,
                **({"icue_automation": self.icue_automation} if self.icue_automation is not None else {}),
                **({"icue": self.icue} if self.icue is not None else {}),
            },
            rows,
            baseline,
            changed_ids,
        )
