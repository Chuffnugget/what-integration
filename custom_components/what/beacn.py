"""BEACN mixer entities created from the controls the PC reports."""
import math
from decimal import Decimal
import aiohttp
from homeassistant.components.number import NumberEntity
from homeassistant.components.select import SelectEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.button import ButtonEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from .entity import WhatEntity
from .select_options import option_labels

def state(coordinator):
    return (coordinator.data or {}).get("beacn", {})

async def send(coordinator, identifier, value=None):
    s = state(coordinator)
    c = next((c for c in s.get("controls", []) if c["id"] == identifier), {})
    if (
        not coordinator.last_update_success
        or not all(s.get(k) for k in ("enabled", "ready", "control_enabled"))
        or not c.get("available")
        or s.get("busy")
    ):
        raise HomeAssistantError(
            "Enable BEACN and Home Assistant control on the PC, and keep the supported BEACN app running",
            translation_domain="what",
            translation_key="enable_beacn_and_ha_control_on_the_pc_and",
        )
    kind = c.get("kind")
    if kind == "select" and value not in c.get("options", []):
        raise HomeAssistantError(
            "BEACN selection is no longer available",
            translation_domain="what",
            translation_key="beacn_selection_is_no_longer_available",
        )
    if kind == "switch" and type(value) is not bool:
        raise HomeAssistantError(
            "Expected a boolean",
            translation_domain="what",
            translation_key="expected_a_boolean",
        )
    if (
        kind == "number"
        and (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not c["min"] <= value <= c["max"]
        )
    ):
        raise HomeAssistantError(
            "Value outside BEACN range",
            translation_domain="what",
            translation_key="value_outside_beacn_range",
        )
    args = {"control": identifier}
    if kind != "button":
        args["value"] = value
    try:
        return await coordinator.client.command("beacn", args, timeout=12)
    except (ConnectionError, OSError, TimeoutError, aiohttp.ClientError) as err:
        raise HomeAssistantError(str(err) or "BEACN command outcome unknown; it was not retried") from err

class BeacnEntity(WhatEntity):
    _attr_icon = "mdi:tune-vertical"
    def __init__(self, coordinator, control):
        super().__init__(coordinator, "beacn_" + control["id"], "BEACN " + control["name"])
        self.control_id = control["id"]
    @property
    def control(self):
        return next(
            (c for c in state(self.coordinator).get("controls", []) if c["id"] == self.control_id),
            {},
        )
    @property
    def available(self):
        s = state(self.coordinator)
        return (
            self.coordinator.last_update_success
            and all(s.get(k) for k in ("enabled", "ready", "control_enabled"))
            and self.control.get("available", False)
        )
    @callback
    def _handle_coordinator_update(self):
        if (
            "beacn" in getattr(self.coordinator, "changed_modules", {"beacn"})
            or self.available != getattr(self, "_previous_available", None)
            or getattr(self.coordinator, "force_entity_update", False)
        ):
            self._previous_available = self.available
            self.async_write_ha_state()

class BeacnNumber(BeacnEntity, NumberEntity):
    @property
    def native_value(self):
        value = self.control.get("value")
        if value is None:
            return None
        # Native volume readback contains float noise; use the control's
        # declared precision without changing commands or observed state.
        decimals = max(0, -Decimal(str(self.native_step)).normalize().as_tuple().exponent)
        return round(value, decimals) if decimals else round(value)
    @property
    def native_min_value(self):
        return self.control.get("min", 0)
    @property
    def native_max_value(self):
        return self.control.get("max", 100)
    @property
    def native_step(self):
        return self.control.get("step", 1)
    async def async_set_native_value(self, value):
        await send(self.coordinator, self.control_id, value)

class BeacnSelect(BeacnEntity, SelectEntity):
    @property
    def choices(self):
        return option_labels((value, value) for value in dict.fromkeys(self.control.get("options", [])))
    @property
    def options(self):
        return list(self.choices)
    @property
    def current_option(self):
        return next((label for label, value in self.choices.items() if value == self.control.get("value")), None)
    async def async_select_option(self, option):
        if option not in self.choices:
            raise HomeAssistantError(
                "That BEACN option is no longer available",
                translation_domain="what",
                translation_key="that_beacn_option_is_no_longer_available",
            )
        await send(self.coordinator, self.control_id, self.choices[option])

class BeacnSwitch(BeacnEntity, SwitchEntity):
    @property
    def is_on(self):
        return self.control.get("value")
    async def async_turn_on(self, **kwargs):
        await send(self.coordinator, self.control_id, True)
    async def async_turn_off(self, **kwargs):
        await send(self.coordinator, self.control_id, False)

class BeacnButton(BeacnEntity, ButtonEntity):
    async def async_press(self):
        await send(self.coordinator, self.control_id)

class BeacnStatus(WhatEntity, SensorEntity):
    _attr_icon = "mdi:tune-vertical"
    def __init__(self, coordinator):
        super().__init__(coordinator, "beacn_status", "BEACN status")
    @property
    def native_value(self):
        s = state(self.coordinator)
        return "Disabled" if not s.get("enabled") else "Ready" if s.get("ready") else "Unavailable"
    @property
    def extra_state_attributes(self):
        s = state(self.coordinator)
        return {k: s[k] for k in ("status", "command_status", "global", "channels", "reconnect_pending") if k in s}

def discover_beacn(entry, async_add_entities, kind):
    coordinator = entry.runtime_data
    known = set()
    classes = {"number": BeacnNumber, "select": BeacnSelect, "switch": BeacnSwitch, "button": BeacnButton}
    @callback
    def update():
        additions = []
        for c in state(coordinator).get("controls", []):
            if c["kind"] == kind and c["id"] not in known:
                known.add(c["id"])
                additions.append(classes[kind](coordinator, c))
        if additions:
            async_add_entities(additions)
    entry.async_on_unload(coordinator.async_add_listener(update))
    update()
