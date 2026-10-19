"""Which module (sub-device) an entity belongs to, and how devices and entities are named."""
from .connection_info import APP_SENSORS, CONNECTION_SENSORS

MODULES = {"windows": "Windows", "hwinfo": "HWiNFO", "nvidia": "NVIDIA",
           "coretemp": "Core Temp", "beacn": "BEACN", "wallpaper": "Wallpaper Engine", "icue": "iCUE", "custom": "Custom"}
DIAGNOSTICS = {"entity_cleanup", "connection", "source_status", "source_problem", "license", "windows_problem",
               "beacn_status", "wallpaper_engine_status", "wallpaper_engine_current", "windows_displays_arrangement"}

DIAGNOSTICS.update(CONNECTION_SENSORS)
DIAGNOSTICS.update(APP_SENSORS)

def module_for(identifier, readings):
    if identifier in CONNECTION_SENSORS or identifier in APP_SENSORS:
        return None
    source = readings.get(identifier, {}).get("source")
    if source in MODULES:
        return source
    if identifier in {"source_status", "source_problem", "license"}:
        return "hwinfo"
    for prefix, module in (("wallpaper_", "wallpaper"), ("beacn_", "beacn"), ("icue_", "icue"),
                           ("custom_", "custom"),
                           ("win_", "windows"), ("windows_", "windows"), ("command_", "windows")):
        if identifier.startswith(prefix):
            return module
    return None

def device_name(pc, module=None, hardware=None):
    return " · ".join(part for part in ("WHAT", pc, MODULES.get(module), hardware) if part)

def entity_name(name, module):
    # Device naming supplies the module; avoid repeating it in every entity name.
    prefix = MODULES.get(module, "")
    if prefix and name.startswith(prefix + " "):
        return name[len(prefix) + 1:]
    return name
