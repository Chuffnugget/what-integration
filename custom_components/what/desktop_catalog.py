"""Entity identities, permissions and presentation metadata for WHAT Desktop."""
from __future__ import annotations

import asyncio
import math
import re

from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er

from .desktop_controls import DesktopError, PUBLIC_ATTRIBUTES, controls, numeric, script_inputs

VIEW = "desktop_view"
VIEW_ALL = "desktop_view_all"
DESKTOP_ACCESS = "desktop_access"
CONTROL = "desktop_control"
CONTROL_ALL_LIGHTS = "desktop_control_all_lights"
PRESETS = "desktop_presets"
ADVANCED = "desktop_advanced"
ANY_ENTITY = frozenset(("", "all", "any", "value", "state", "entity", "indicator"))
ACTION_FILTERS = frozenset(("button", "action", "slider", "number_control", "selector", "choice",
                            "toggle", "switch_control", "text", "date", "time", "datetime"))
NUMERIC_FILTERS = frozenset(("graph", "gauge", "history", "numeric"))
# These states can legitimately be unknown until the first event, image or invocation.
STATE_OPTIONAL_DOMAINS = frozenset(("button", "input_button", "scene", "event", "image", "image_processing", "notify"))
# Registry capabilities can remain present when a device is off or disconnected.
# Only fall back to static schemas. Never substitute an old reading for current state.
CAPABILITY_KEYS = {
    "alarm_control_panel": ("code_format", "code_arm_required"),
    "climate": ("min_temp", "max_temp", "target_temp_step", "hvac_modes", "preset_modes", "fan_modes",
                "swing_modes", "swing_horizontal_modes", "min_humidity", "max_humidity"),
    "fan": ("preset_modes", "percentage_step"),
    "humidifier": ("min_humidity", "max_humidity", "available_modes"),
    "input_datetime": ("has_date", "has_time"),
    "input_number": ("min", "max", "step", "mode"), "number": ("min", "max", "step", "mode"),
    "input_select": ("options",), "select": ("options",),
    "input_text": ("mode", "min", "max"), "text": ("mode", "min", "max"),
    "light": ("supported_color_modes", "min_color_temp_kelvin", "max_color_temp_kelvin", "effect_list"),
    "lock": ("code_format",),
    "siren": ("available_tones",),
    "vacuum": ("fan_speed_list",),
    "water_heater": ("min_temp", "max_temp", "operation_list"),
}
EXTRA_ATTRIBUTES = {
    "climate": "current_humidity min_humidity max_humidity swing_mode swing_modes swing_horizontal_mode swing_horizontal_modes",
    "counter": "initial minimum maximum",
    "cover": "current_tilt_position",
    "fan": "oscillating direction",
    "humidifier": "current_humidity min_humidity max_humidity mode available_modes action",
    "input_number": "mode", "number": "mode",
    "text": "mode", "input_text": "mode",
    "light": "hs_color xy_color rgbw_color rgbww_color effect_list",
    "media_player": "media_duration media_position media_position_updated_at sound_mode sound_mode_list shuffle repeat",
    "remote": "activity_list current_activity",
    "siren": "available_tones",
    "update": "installed_version latest_version skipped_version in_progress update_percentage",
    "vacuum": "fan_speed fan_speed_list",
    "water_heater": "operation_mode operation_list away_mode",
}
DOMAIN_ATTRIBUTES = {domain: PUBLIC_ATTRIBUTES.union(keys.split()) for domain, keys in EXTRA_ATTRIBUTES.items()}


def view_all(options):
    """Older selections, including an explicitly empty list, remain restricted."""
    return options.get(VIEW_ALL, VIEW not in options and CONTROL not in options) is True


def access_mode(options):
    """Keep the effective grants of pairings that saved Desktop sharing choices."""
    if DESKTOP_ACCESS in options:
        mode = options[DESKTOP_ACCESS]
        return mode if mode in ("control", "view", "selected") else "view"
    if any(key in options for key in (VIEW_ALL, VIEW, CONTROL, CONTROL_ALL_LIGHTS, ADVANCED, PRESETS)):
        return "selected"
    return "control"


def stable_keys(hass, entity_ids):
    registry = er.async_get(hass)
    return list(dict.fromkeys(entry.id if (entry := registry.async_get(entity_id)) else entity_id
                              for entity_id in entity_ids))


def current_id(hass, key):
    entry = er.async_get(hass).async_get(key)
    return entry.entity_id if entry else (key if "." in key else "")


def safe_value(value):
    # Avoid unbounded or non-serializable attributes from third-party entities.
    if isinstance(value, (list, tuple, set, frozenset)):
        return [safe_value(v) for v in list(value)[:128] if isinstance(v, (str, int, float, bool))]
    if isinstance(value, str):
        return value[:256]
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)  # HA capability flags may be IntFlag objects.
    if type(value) is float and math.isfinite(value):
        return value
    return None


def entity_attributes(domain, state_attributes, item):
    """Use live metadata first, including explicit capability removals."""
    attrs = dict(state_attributes)
    if item:
        capabilities = item.capabilities or {}
        for key in CAPABILITY_KEYS.get(domain, ()):
            if key not in attrs and key in capabilities:
                attrs[key] = capabilities[key]
        if item.supported_features is not None:
            attrs.setdefault("supported_features", item.supported_features)
        if device_class := item.device_class or item.original_device_class:
            attrs.setdefault("device_class", device_class)
    if domain == "light" and "supported_color_modes" not in attrs and isinstance(mode := attrs.get("color_mode"), str):
        # Some state-only lights report their current mode without a capability list.
        attrs["supported_color_modes"] = [mode]
    return attrs


class DesktopCatalog:
    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        self.descriptions = {}
        self._permissions = None
        self._view = self._control = frozenset()

    def grants(self):
        # ConfigEntry replaces this immutable mapping whenever options change.
        # Search across thousands of entities builds the sets once per revision.
        options = self.entry.options
        if self._permissions is not options:
            self._permissions = options
            self._mode = access_mode(options)
            self._control_lights = self._mode == "selected" and options.get(CONTROL_ALL_LIGHTS) is True
            self._control = frozenset(options.get(CONTROL, ())) if self._mode == "selected" else frozenset()
            self._view = self._control.union(options.get(VIEW, ()))
        return self._view, self._control

    def resolve(self, binding, *, write=False):
        if not isinstance(binding, dict):
            raise DesktopError("Choose a shared entity.")
        requested = binding.get("registry_id") or binding.get("entity_id")
        if not isinstance(requested, str) or not 1 <= len(requested) <= 256:
            raise DesktopError("Choose a shared entity.")
        registry = er.async_get(self.hass)
        item = registry.async_get(requested)
        if binding.get("registry_id") and (item is None or binding["registry_id"] != item.id):
            raise DesktopError("This entity is unavailable.")
        entity_id = item.entity_id if item else requested
        view, _ = self.grants()
        if item is not None and item.disabled_by is not None:
            raise DesktopError("This entity is unavailable.")
        if view_all(self.entry.options) or self._lights_granted(entity_id):
            if item is None and self.hass.states.get(entity_id) is None:
                raise DesktopError("This entity is unavailable.")
        elif (item.id if item else requested) not in view and not (not binding.get("registry_id") and entity_id in view):
            raise DesktopError("This PC has no access to this entity.")
        if "." not in entity_id:
            raise DesktopError("This entity is unavailable.")
        if write and not self._can_control_visible(entity_id, item.id if item else ""):
            raise DesktopError("This PC has no access to this entity.")
        return entity_id, item

    def _lights_granted(self, entity_id):
        return self._control_lights and entity_id.startswith("light.")

    def _can_control_visible(self, entity_id, registry_id):
        """Called after resolving a visible entity; the service must also be supported."""
        if self._mode == "control":
            return True
        return self._mode == "selected" and (
            self._lights_granted(entity_id) or entity_id in self._control
            or bool(registry_id and registry_id in self._control)
        )

    def preset(self, entity_id):
        if self.entry.options.get(ADVANCED) is True and access_mode(self.entry.options) != "view" and isinstance(entity_id, str):
            return self.entry.options.get(PRESETS, {}).get(entity_id)
        return None

    def row(self, binding, *, metadata_only=False, include_capabilities=False):
        entity_id = binding.get("entity_id") if isinstance(binding, dict) else None
        if (preset := self.preset(entity_id)) and not binding.get("registry_id"):
            device_action = preset.get("device_action")
            domain = device_action["domain"] if device_action else preset["service"].partition(".")[0]
            metadata = {"entity_id": entity_id, "registry_id": "", "name": preset["name"], "domain": "action",
                        "integration": domain, "device": "", "area": "", "area_id": ""}
            if metadata_only:
                return metadata
            if device_action:
                available = dr.async_get(self.hass).async_get(device_action["device_id"]) is not None
            else:
                available = self.hass.services.has_service(*preset["service"].split(".", 1))
            return {**metadata, "state": "", "available": available,
                    "icon": "mdi:gesture-tap-button", "attributes": {}, "unit": "",
                    "actions": [{"id": "run", "label": preset["name"], "fields": [], "confirm": True}]}

        entity_id, item = self.resolve(binding)
        hass = self.hass
        state = hass.states.get(entity_id)
        attrs = state.attributes if state is not None else {}
        domain, suffix = entity_id.split(".", 1)
        device = dr.async_get(hass).async_get(item.device_id) if item and item.device_id else None
        area_id = (item.area_id if item else None) or (device.area_id if device else None)
        area = ar.async_get(hass).async_get_area(area_id) if area_id else None
        metadata = {"entity_id": entity_id, "registry_id": item.id if item else "",
                    "name": str(attrs.get("friendly_name") or (item.name or item.original_name if item else None)
                                or entity_id)[:256], "domain": domain, "integration": item.platform if item else "",
                    "area": area.name if area else "", "area_id": area.id if area else "",
                    "device": (device.name_by_user or device.name or "") if device else ""}
        if metadata_only:
            return metadata
        attrs = entity_attributes(domain, attrs, item)
        if domain in ("climate", "water_heater", "weather"):
            attrs.setdefault("temperature_unit", hass.config.units.temperature_unit)
        public = DOMAIN_ATTRIBUTES.get(domain, PUBLIC_ATTRIBUTES)
        masked = domain in {"text", "input_text"} and attrs.get("mode") == "password"
        actions = []
        extra = {}
        if not masked and self._can_control_visible(entity_id, item.id if item else ""):
            fields = script_inputs(self.descriptions.get("script", {}).get(suffix)) if domain == "script" else []
            actions = controls(entity_id, attrs, hass.services.has_service, fields)
        elif include_capabilities and not masked:
            # Read-only PCs can render disabled controls. Keep saved script fields private.
            if capabilities := controls(entity_id, attrs, hass.services.has_service):
                extra["capabilities"] = capabilities
        return {**metadata, **extra,
                "icon": attrs.get("icon", ""), "state": state.state if state and not masked else "unavailable",
                "available": bool(state and not masked and state.state != "unavailable" and (
                    state.state != "unknown" or domain in STATE_OPTIONAL_DOMAINS)),
                "unit": str(attrs.get("unit_of_measurement") or "")[:64],
                "attributes": {k: safe_value(v) for k, v in attrs.items() if k in public and not masked},
                "actions": actions}

    def _candidate_ids(self):
        if view_all(self.entry.options):
            # State-only helpers and enabled registry entries from offline devices
            # can exist independently. Take a snapshot before yielding to HA.
            ids = set(self.hass.states.async_entity_ids())
            ids.update(item.entity_id for item in er.async_get(self.hass).entities.values()
                       if item.disabled_by is None)
        else:
            ids = {entity_id for key in self.grants()[0] if (entity_id := current_id(self.hass, key))}
            if self._control_lights:
                ids.update(self.hass.states.async_entity_ids("light"))
                ids.update(item.entity_id for item in er.async_get(self.hass).entities.values()
                           if item.domain == "light" and item.disabled_by is None)
        if self.entry.options.get(ADVANCED) is True and access_mode(self.entry.options) != "view":
            ids.update(self.entry.options.get(PRESETS, {}))
        return ids

    def _matches_widget(self, row, kind):
        domain = row["domain"]
        if kind in ANY_ENTITY:
            return True
        if kind in ACTION_FILTERS or kind.startswith("control:"):
            # PC sensor catalogs can contain thousands of readings with no entity controls.
            if domain in ("sensor", "binary_sensor"):
                return False
            if kind.startswith("control:") and domain != kind.removeprefix("control:"):
                return False
            if domain != "action" and not self._can_control_visible(row["entity_id"], row["registry_id"]):
                return False
            return matches_actions(self.row({"entity_id": row["entity_id"]})["actions"], kind)
        if kind == "presence":
            return domain in ("person", "device_tracker")
        if kind == "admin":
            return domain == "action"
        if kind in NUMERIC_FILTERS:
            state = self.hass.states.get(row["entity_id"])
            if domain in ("number", "input_number", "counter"):
                return True
            if not state or (domain in {"text", "input_text"} and state.attributes.get("mode") == "password"):
                return False
            return (numeric(state.state) is not None or bool(state.attributes.get("unit_of_measurement"))
                    or state.attributes.get("state_class") in ("measurement", "total_increasing", "total"))
        return domain == kind

    async def search(self, query, kind, offset, limit, area_id="", domain=""):
        if not isinstance(query, str) or len(query) > 256 or not isinstance(kind, str) or len(kind) > 64:
            raise DesktopError("Invalid search.")
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 25:
            raise DesktopError("Invalid search page.")
        if not isinstance(area_id, str) or len(area_id) > 256:
            raise DesktopError("Invalid search area.")
        if not isinstance(domain, str) or len(domain) > 128:
            raise DesktopError("Invalid search domain.")
        revision = self.entry.options
        query = query.casefold().strip()
        # Treat separators in entity IDs and natural-language searches alike.
        terms = re.findall(r"[^\W_]+", query) or ([query] if query else [])
        shared_areas = {}
        domains = set()
        shared_total = 0
        total = 0
        page_end = offset + limit
        trim_at = page_end + max(128, page_end)
        rows = []
        for index, entity_id in enumerate(self._candidate_ids()):
            if index and index % 64 == 0:
                await asyncio.sleep(0)
                if self.entry.options is not revision:
                    raise DesktopError("Sharing changed. Search again.")
            try:
                row = self.row({"entity_id": entity_id}, metadata_only=True)
            except DesktopError:
                # A dynamic entity can disappear between two batches.
                continue
            shared_total += 1
            domains.add(row["domain"])
            if row["area_id"] and len(row["area_id"]) <= 256:
                shared_areas[row["area_id"]] = row["area"]
            if domain and row["domain"] != domain:
                continue
            if area_id and row["area_id"] != area_id:
                continue
            if terms:
                fields = " ".join(row[key] for key in ("name", "entity_id", "device", "area", "integration")).casefold()
                if not all(term in fields for term in terms):
                    continue
            if not self._matches_widget(row, kind):
                continue
            name = row["name"].casefold()
            exact = query == name or query == entity_id
            total += 1
            rows.append((not exact, name, entity_id))
            # Early pages only need the best matches up to the end of the page.
            # Trim in batches so sorting a large installation cannot stall HA.
            if len(rows) >= trim_at:
                rows.sort()
                del rows[page_end:]
        rows.sort()
        areas = sorted(shared_areas.items(), key=lambda item: (item[1].casefold(), item[0]))
        page = []
        for _, _, entity_id in rows[offset:page_end]:
            try:
                page.append(self.row({"entity_id": entity_id}))
            except DesktopError:
                continue
        return {"rows": page, "total": total, "shared_total": shared_total,
                "view_mode": "all" if view_all(revision) else "selected", "domains": sorted(domains),
                "areas": [{"id": key, "name": name[:128]} for key, name in areas[:128]]}


def matches_actions(actions, kind):
    if kind.startswith("control:") or kind in ("button", "action"):
        return bool(actions)
    fields = [f for a in actions for f in a["fields"]]
    if kind in ("slider", "number_control"):
        return any(f["type"] == "number" for f in fields)
    if kind in ("selector", "choice"):
        return any(f["type"] == "select" for f in fields)
    if kind in ("toggle", "switch_control"):
        services = {a["id"].split(".")[-1] for a in actions}
        return "toggle" in services or {"turn_on", "turn_off"} <= services
    if kind in ("text", "date", "time", "datetime"):
        return any(f["type"] == kind for f in fields)
    return False

