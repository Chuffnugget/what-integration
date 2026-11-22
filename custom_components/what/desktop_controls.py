"""Entity-scoped actions and field validation for WHAT Desktop."""
from __future__ import annotations

from datetime import date, datetime, time
import math
import re

from homeassistant.components.light import (
    DEFAULT_MAX_KELVIN, DEFAULT_MIN_KELVIN, LightEntityFeature,
    brightness_supported, color_supported, color_temp_supported,
)


class DesktopError(ValueError):
    """A validation message suitable to display on the paired PC."""


# Public, presentation-related attributes. Camera tokens, pictures with tokens,
# coordinates, credentials and arbitrary integration-specific attributes stay in HA.
PUBLIC_ATTRIBUTES = frozenset("""
device_class state_class unit_of_measurement supported_features brightness
supported_color_modes color_mode rgb_color color_temp_kelvin min_color_temp_kelvin effect
max_color_temp_kelvin current_temperature temperature target_temp_high target_temp_low
min_temp max_temp target_temp_step hvac_modes hvac_action fan_mode fan_modes preset_mode
preset_modes current_position percentage percentage_step volume_level is_volume_muted
media_title media_artist source source_list min max step options has_date has_time
duration remaining finishes_at last_triggered event_type event_types humidity
temperature_unit wind_speed wind_speed_unit pressure pressure_unit
""".split())


def numeric(value):
    """Return a finite numeric entity state, or None for nonnumeric data."""
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def field(name, kind, **options):
    return {"name": name, "label": name.replace("_", " ").capitalize(), "type": kind,
            "required": True, **options}


def controls(entity_id, attrs, has_service, script_fields=None):
    """Generate the same action contract used by discovery and by execution."""
    domain, key = entity_id.split(".", 1)
    features = attrs.get("supported_features", 0)
    if not isinstance(features, int):
        features = 0
    actions = []

    def add(service, label, *fields, confirm=False, action_id=None):
        if has_service(domain, service):
            actions.append({"id": action_id or domain + "." + service, "label": label,
                            "fields": list(fields), "confirm": confirm})

    def number(name, minimum, maximum, step):
        bounds = {k: value for k, raw in (("min", minimum), ("max", maximum), ("step", step))
                  if (value := numeric(raw)) is not None and (k != "step" or value > 0)}
        return field(name, "number", **bounds)

    def choice(service, label, name, values, *, action_id=None):
        if isinstance(values, (list, tuple)) and values:
            options = [v for v in values if isinstance(v, str) and len(v) <= 256][:256]
            if options:
                add(service, label, field(name, "select", options=options), action_id=action_id)

    if domain in {"button", "input_button"}:
        add("press", "Press", confirm=attrs.get("device_class") in {"restart", "update"})
    if domain in {"switch", "light", "automation", "input_boolean", "humidifier", "remote"}:
        add("turn_off", "Disable" if domain == "automation" else "Turn off")
        extras = []
        if domain == "remote" and features & 4:
            choice("turn_on", "Select activity", "activity", attrs.get("activity_list"), action_id="remote.select_activity")
        if domain == "light":
            raw_modes = attrs.get("supported_color_modes")
            modes = ({v for v in raw_modes if isinstance(v, str)}
                     if isinstance(raw_modes, (list, tuple, set, frozenset)) else set())
            if brightness_supported(modes):
                extras.append(number("brightness_pct", 0, 100, 1))
            if color_supported(modes):
                extras.append(field("rgb_color", "color"))
            if color_temp_supported(modes):
                minimum = numeric(attrs.get("min_color_temp_kelvin", DEFAULT_MIN_KELVIN))
                maximum = numeric(attrs.get("max_color_temp_kelvin", DEFAULT_MAX_KELVIN))
                if minimum is not None and maximum is not None and 0 < minimum < maximum:
                    extras.append(number("color_temp_kelvin", minimum, maximum, 1))
        add("turn_on", "Enable" if domain == "automation" else "Turn on",
            *(item | {"required": False} for item in extras))
        add("toggle", "Toggle")
        if domain == "light" and features & LightEntityFeature.EFFECT:
            choice("turn_on", "Set effect", "effect", attrs.get("effect_list"), action_id="light.set_effect")
    if domain == "automation":
        add("trigger", "Run automation")
    if domain == "scene":
        add("turn_on", "Activate scene")
    if domain == "script":
        add(key, "Run script", *(script_fields or []))
        add("turn_off", "Stop script")
    if domain in {"number", "input_number"}:
        add("set_value", "Set value", number("value", attrs.get("min", 0), attrs.get("max", 100), attrs.get("step", 1)))
    if domain in {"select", "input_select"}:
        choice("select_option", "Select option", "option", attrs.get("options"))
    if domain in {"text", "input_text"} and attrs.get("mode") != "password":
        limit = numeric(attrs.get("max"))
        minimum = numeric(attrs.get("min"))
        add("set_value", "Set text", field("value", "text",
            min_length=int(minimum) if minimum is not None and minimum > 0 else 0,
            max_length=int(min(255, limit)) if limit is not None and limit >= 0 else 255))
    if domain == "input_datetime":
        fields = [field(k, k) for k in ("date", "time") if attrs.get("has_" + k)]
        if fields:
            add("set_datetime", "Set date and time", *fields)
    if domain in {"date", "time", "datetime"}:
        add("set_value", "Set " + domain, field("value", domain))
    if domain == "fan":
        if features & 16:
            add("turn_off", "Turn off")
        if features & 32:
            add("turn_on", "Turn on")
        if features & 16 and features & 32:
            add("toggle", "Toggle")
        if features & 1:
            add("set_percentage", "Set speed", number("percentage", 0, 100, attrs.get("percentage_step", 1)))
        if features & 2:
            add("oscillate", "Oscillate", field("oscillating", "boolean"))
        if features & 4:
            choice("set_direction", "Direction", "direction", ["forward", "reverse"])
        if features & 8:
            choice("set_preset_mode", "Set preset", "preset_mode", attrs.get("preset_modes"))
    if domain in {"climate", "water_heater"}:
        if features & 1:
            add("set_temperature", "Set temperature", number("temperature", attrs.get("min_temp", 7),
                attrs.get("max_temp", 35), attrs.get("target_temp_step") or 0.5))
        if domain == "climate":
            choice("set_hvac_mode", "Set HVAC mode", "hvac_mode", attrs.get("hvac_modes"))
            if features & 2:
                add("set_temperature", "Set temperature range", number("target_temp_low", attrs.get("min_temp", 7),
                    attrs.get("max_temp", 35), attrs.get("target_temp_step") or 0.5),
                    number("target_temp_high", attrs.get("min_temp", 7), attrs.get("max_temp", 35),
                           attrs.get("target_temp_step") or 0.5), action_id="climate.set_temperature_range")
            if features & 16:
                choice("set_preset_mode", "Set preset", "preset_mode", attrs.get("preset_modes"))
            if features & 8:
                choice("set_fan_mode", "Set fan mode", "fan_mode", attrs.get("fan_modes"))
            if features & 4:
                add("set_humidity", "Set humidity", number("humidity", attrs.get("min_humidity", 30), attrs.get("max_humidity", 99), 1))
            if features & 32:
                choice("set_swing_mode", "Swing", "swing_mode", attrs.get("swing_modes"))
            if features & 512:
                choice("set_swing_horizontal_mode", "Horizontal swing", "swing_horizontal_mode", attrs.get("swing_horizontal_modes"))
            if features & 128:
                add("turn_off", "Turn off")
            if features & 256:
                add("turn_on", "Turn on")
        else:
            if features & 2:
                choice("set_operation_mode", "Operation", "operation_mode", attrs.get("operation_list"))
            if features & 4:
                add("set_away_mode", "Away mode", field("away_mode", "boolean"))
            if features & 8:
                add("turn_on", "Turn on")
                add("turn_off", "Turn off")
    if domain == "humidifier":
        add("set_humidity", "Set humidity", number("humidity", attrs.get("min_humidity", 0), attrs.get("max_humidity", 100), 1))
        if features & 1:
            choice("set_mode", "Mode", "mode", attrs.get("available_modes"))
    if domain == "cover":
        for bit, service, label in ((1, "open_cover", "Open"), (2, "close_cover", "Close"), (8, "stop_cover", "Stop"),
                                    (16, "open_cover_tilt", "Open tilt"), (32, "close_cover_tilt", "Close tilt"),
                                    (64, "stop_cover_tilt", "Stop tilt")):
            if features & bit:
                add(service, label)
        if features & 4:
            add("set_cover_position", "Set position", number("position", 0, 100, 1))
        if features & 128:
            add("set_cover_tilt_position", "Tilt", number("tilt_position", 0, 100, 1))
    if domain == "valve":
        for bit, service in ((1, "open"), (2, "close"), (8, "stop")):
            if features & bit:
                add(service + "_valve", service.capitalize())
        if features & 4:
            add("set_valve_position", "Set position", number("position", 0, 100, 1))
    if domain == "media_player":
        for bit, service, label in ((1, "media_pause", "Pause"), (16384, "media_play", "Play"), (16, "media_previous_track", "Previous"),
                                     (32, "media_next_track", "Next"), (128, "turn_on", "Turn on"), (256, "turn_off", "Turn off"),
                                     (4096, "media_stop", "Stop")):
            if features & bit:
                add(service, label)
        if features & 4:
            add("volume_set", "Set volume", number("volume_level", 0, 1, 0.01))
        if features & 8:
            add("volume_mute", "Mute", field("is_volume_muted", "boolean"))
        if features & 2048:
            choice("select_source", "Select source", "source", attrs.get("source_list"))
        if features & 1024:
            add("volume_up", "Volume up")
            add("volume_down", "Volume down")
        duration = numeric(attrs.get("media_duration"))
        if features & 2 and duration is not None and duration > 0:
            add("media_seek", "Seek", number("seek_position", 0, duration, 1))
        if features & 32768:
            add("shuffle_set", "Shuffle", field("shuffle", "boolean"))
        if features & 65536:
            choice("select_sound_mode", "Sound mode", "sound_mode", attrs.get("sound_mode_list"))
        if features & 262144:
            choice("repeat_set", "Repeat", "repeat", ["off", "one", "all"])
    if domain == "vacuum":
        for bit, service, label in ((8192, "start", "Start"), (4, "pause", "Pause"), (8, "stop", "Stop"),
                                     (16, "return_to_base", "Return to base"), (512, "locate", "Locate"),
                                     (1024, "clean_spot", "Clean spot")):
            if features & bit:
                add(service, label)
        if features & 32:
            choice("set_fan_speed", "Fan speed", "fan_speed", attrs.get("fan_speed_list"))
    if domain == "lawn_mower":
        for bit, service, label in ((1, "start_mowing", "Start mowing"), (2, "pause", "Pause"), (4, "dock", "Dock")):
            if features & bit:
                add(service, label, confirm=service == "start_mowing")
    if domain == "lock":
        code = [field("code", "text", sensitive=True, max_length=64)] if attrs.get("code_format") else []
        add("lock", "Lock", *code, confirm=True)
        add("unlock", "Unlock", *code, confirm=True)
        if features & 1:
            add("open", "Unlatch", *code, confirm=True)
    if domain == "alarm_control_panel":
        code = [field("code", "text", sensitive=True, max_length=64)] if attrs.get("code_format") else []
        add("alarm_disarm", "Disarm", *code, confirm=True)
        for bit, mode in ((1, "home"), (2, "away"), (4, "night"), (16, "custom_bypass"), (32, "vacation")):
            if features & bit:
                add("alarm_arm_" + mode, "Arm " + mode.replace("_", " "),
                    *(code if attrs.get("code_arm_required", True) else []), confirm=True)
        if features & 8:
            add("alarm_trigger", "Trigger alarm", *code, confirm=True)
    if domain == "siren":
        if features & 1:
            parameters = []
            tones = attrs.get("available_tones")
            if isinstance(tones, dict):
                tones = list(tones.values())  # HA accepts a named tone or its device-specific key.
            if features & 4 and isinstance(tones, (list, tuple)):
                options = [v for v in tones if type(v) is int or isinstance(v, str) and len(v) <= 256][:128]
                if options:
                    parameters.append(field("tone", "select", options=options, required=False))
            if features & 8:
                parameters.append(number("volume_level", 0, 1, 0.05) | {"required": False})
            if features & 16:
                parameters.append(number("duration", 0, None, 1) | {"required": False})
            add("turn_on", "Turn on", *parameters, confirm=True)
        if features & 2:
            add("turn_off", "Turn off")
        if features & 1 and features & 2:
            add("toggle", "Toggle", confirm=True)
    if domain == "camera" and features & 1:
        add("turn_on", "Turn on")
        add("turn_off", "Turn off")
    if domain == "image_processing":
        add("scan", "Scan image")
    if domain == "calendar" and features & 1:
        add("create_event", "Create event", field("summary", "text", max_length=255),
            field("start_date_time", "datetime"), field("end_date_time", "datetime"), confirm=True)
    if domain == "todo":
        if features & 1:
            add("add_item", "Add item", field("item", "text", max_length=255))
        if features & 4:
            add("update_item", "Set item status", field("item", "text", max_length=255),
                field("status", "select", options=["needs_action", "completed"]))
        if features & 2:
            add("remove_item", "Remove item", field("item", "text", max_length=255), confirm=True)
    if domain == "notify":
        title = [field("title", "text", required=False, max_length=255)] if features & 1 else []
        add("send_message", "Send notification", field("message", "text"), *title, confirm=True)
    if domain == "assist_satellite":
        if features & 1:
            add("announce", "Announce", field("message", "text"), confirm=True)
        if features & 2:
            add("start_conversation", "Start conversation", field("start_message", "text"), confirm=True)
    if domain == "timer":
        for service in ("start", "pause", "cancel", "finish"):
            add(service, service.capitalize())
    if domain == "counter":
        for service in ("increment", "decrement", "reset"):
            add(service, service.capitalize())
        add("set_value", "Set value", number("value", attrs.get("minimum"), attrs.get("maximum"), 1))
    if domain == "update":
        if features & 1:
            add("install", "Install update", confirm=True)
        if attrs.get("skipped_version"):
            add("clear_skipped", "Show skipped update")
        elif attrs.get("latest_version") and attrs.get("installed_version") != attrs["latest_version"]:
            add("skip", "Skip this update", confirm=True)
    return actions


def validate_fields(fields, data):
    """Accept only the fields advertised for this specific action and entity."""
    specs = {item["name"]: item for item in fields}
    if not isinstance(data, dict) or set(data) - specs.keys() or any(
        spec.get("required") and name not in data for name, spec in specs.items()
    ):
        raise DesktopError("Check the required action fields.")
    for name, value in data.items():
        spec = specs[name]
        kind = spec["type"]
        if kind == "number":
            ok = type(value) in (int, float) and numeric(value) is not None and spec.get("min", -1e15) <= value <= spec.get("max", 1e15)
        elif kind == "boolean":
            ok = type(value) is bool
        elif kind == "color":
            ok = isinstance(value, list) and len(value) == 3 and all(type(v) is int and 0 <= v <= 255 for v in value)
        elif kind == "select":
            ok = value in spec["options"]
        else:
            ok = isinstance(value, str) and spec.get("min_length", 0) <= len(value) <= spec.get("max_length", 1024)
            if ok and kind in ("date", "time", "datetime"):
                try:
                    {"date": date, "time": time, "datetime": datetime}[kind].fromisoformat(value)
                except ValueError:
                    ok = False
        if not ok:
            raise DesktopError("Invalid value for " + name + ".")
    return data


def script_inputs(description):
    """Expose supported scalar script variables; never accept arbitrary service targets."""
    result = []
    for name, item in (description or {}).get("fields", {}).items():
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,63}", name) or name in {"entity_id", "area_id", "device_id", "floor_id", "label_id", "target"}:
            continue
        config = item.get("selector", {}) or {}
        kind = next(iter(config), "text")
        options = config.get(kind) or {}
        if kind not in {"text", "number", "boolean", "select", "date", "time", "datetime", "color_rgb"}:
            continue
        spec = field(name, "color" if kind == "color_rgb" else kind, label=item.get("name") or name,
                     required=bool(item.get("required") and "default" not in item))
        if kind == "text" and options.get("type") == "password":
            spec["sensitive"] = True
        if kind == "number":
            spec.update({k: options[k] for k in ("min", "max", "step") if options.get(k) is not None})
        if kind == "select":
            if options.get("multiple"):
                continue
            spec["options"] = [v.get("value") if isinstance(v, dict) else v for v in options.get("options", [])][:256]
        result.append(spec)
    return result[:24]

