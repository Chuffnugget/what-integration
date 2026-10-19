"""Config entry diagnostics. Only listed fields are included, so addresses, tokens and readings stay out."""
import re
import time

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.loader import async_get_integration

from .connection_info import connection_info
from .const import DOMAIN, PROTOCOL_VERSION

MODULES = ("windows", "hwinfo", "coretemp", "nvidia", "beacn", "icue",
           "icue_automation", "wallpaper", "custom", "dynamic_lighting", "displays")
FLAGS = ("enabled", "connected", "ready", "healthy", "running", "installed", "available",
         "control_enabled", "cloud", "busy", "reconnect_pending")
VERSION_FIELDS = ("app_version", "sdk_version", "driver_version", "installed_version")


def safe_version(value):
    if isinstance(value, str) and re.fullmatch(r"\d{1,5}(?:\.\d{1,5}){0,3}(?:[-+][a-zA-Z0-9.]{1,32})?", value):
        return value
    return None


def module_diagnostics(data, readings):
    result = {}
    for name in MODULES:
        state = data.get(name, {})
        if not isinstance(state, dict):
            state = {}
        result[name] = {
            "reported": name in data,
            "readings": 0,
            "unavailable_readings": 0,
            "flags": {key: state[key] for key in FLAGS if type(state.get(key)) is bool},
            "versions": {key: safe_version(state.get(key)) for key in VERSION_FIELDS},
        }
        for key in ("controls", "devices", "actions", "monitors"):
            if isinstance(state.get(key), list):
                result[name][key + "_count"] = len(state[key])
    for row in readings.values():
        source = result.get(row.get("source"))
        if source is not None:
            source["reported"] = True
            source["readings"] += 1
            source["unavailable_readings"] += int(not row.get("available") or row.get("value") is None)
    for source, wire_keys in {
        "hwinfo": {"enabled": "hwinfo_enabled", "healthy": "healthy"},
        "windows": {"healthy": "windows_healthy"},
    }.items():
        for flag, key in wire_keys.items():
            if type(data.get(key)) is bool:
                result[source]["reported"] = True
                result[source]["flags"][flag] = data[key]
    return result


async def async_get_config_entry_diagnostics(hass, entry):
    integration = await async_get_integration(hass, DOMAIN)
    report = {
        "integration_version": integration.version,
        "home_assistant_version": HA_VERSION,
        "entry_state": entry.state.value,
        "protocol": PROTOCOL_VERSION,
        "routes_enabled": {key: entry.options.get(key) is True for key in ("remote_enabled", "mqtt_enabled")},
    }
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is None:
        return report | {"connected": False, "snapshot_available": False}
    data = coordinator.data or {}
    controls = data.get("controls", {})
    app = data.get("app", {})
    last_seen = getattr(coordinator, "last_snapshot_received", None)
    report.update({
        "connected": coordinator.last_update_success,
        "snapshot_available": bool(data),
        "snapshot_age_seconds": max(0, round(time.monotonic() - last_seen, 1)) if last_seen else None,
        "source_sample_age": data.get("sample_age"),
        "publish_seconds": data.get("publish_seconds"),
        "self_reported_edition": data.get("license"),
        "connection": connection_info(coordinator),
        "connection_health": coordinator.health.diagnostics(),
        "app_version": safe_version(coordinator.hello.get("app_version")),
        "modules": module_diagnostics(data, coordinator.readings),
        "windows_permissions": {key: value for key, value in controls.get("permissions", {}).items()
                                if key in {"media", "power", "power_plan", "urls", "monitor"}
                                and type(value) is bool},
        "extra_permissions": {key: controls[key] for key in
                              ("notifications_enabled", "input_enabled", "shell_enabled")
                              if type(controls.get(key)) is bool},
        "app": {key: app[key] for key in ("control_enabled", "logging", "restart_available",
                                         "publishing_interval_ms", "high_interval_ms", "uptime_seconds",
                                         "cpu_percent", "memory_bytes") if key in app},
    })
    return report
