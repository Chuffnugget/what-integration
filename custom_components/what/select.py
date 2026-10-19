"""Selects: power plan and power mode, display layouts, BEACN, iCUE and iCUE Automation options."""
from .displays import discover_displays
from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import HomeAssistantError
from .control_entity import WindowsControlEntity
from .icue import IcueEntity, discover
from .icue_lighting import discover_lighting
from .icue_lighting_select import LightingLinkSelect
from .icue_automation import discover_automation
from .select_options import option_labels

# Serialize actions within this platform; commands are never replayed.
PARALLEL_UPDATES = 1


async def async_setup_entry(hass, entry, async_add_entities):
    from .beacn import discover_beacn
    discover_beacn(entry, async_add_entities, "select")
    discover_displays(entry, async_add_entities, "select")
    discover_automation(entry, async_add_entities)
    discover_lighting(
        entry,
        async_add_entities,
        lambda c: (LightingLinkSelect(c, p) for p in ("Speed", "Direction", "Rotation", "RandomColor")),
    )
    discover(
        entry,
        async_add_entities,
        lambda c, d: [IcueEqualizer(c, d)] if any(p["id"] == 5 and p["writable"] for p in d["properties"]) else [],
    )
    async_add_entities(
        [PowerPlan(entry.runtime_data), PowerMode(entry.runtime_data, "ac", "plugged in"), PowerMode(entry.runtime_data, "dc", "battery")],
    )


class PowerPlan(WindowsControlEntity, SelectEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, "windows_power_plan", "Windows power plan", "power_plan")
    @property
    def plans(self):
        plans = self.controls.get("power", {}).get("plans", [])
        return option_labels((p["name"], p["id"]) for p in plans)
    @property
    def options(self):
        return list(self.plans)
    @property
    def current_option(self):
        return next(
            (name for name, identifier in self.plans.items() if identifier == self.controls.get("power", {}).get("active")),
            None,
        )
    async def async_select_option(self, option):
        if option not in self.plans:
            raise HomeAssistantError(
                "Power plan is no longer available",
                translation_domain="what",
                translation_key="power_plan_is_no_longer_available",
            )
        await self.command("power_plan", self.plans[option])

class IcueEqualizer(IcueEntity, SelectEntity):
    _attr_options = ["1", "2", "3", "4", "5"]
    def __init__(self, coordinator, device):
        super().__init__(coordinator, device, "equalizer", "Equalizer preset")
    @property
    def prop(self):
        return next((p for p in (self.device or {}).get("properties", []) if p["id"] == 5), {})
    @property
    def available(self):
        return super().available and self.prop.get("writable", False)
    @property
    def current_option(self):
        return str(self.prop["value"]) if "value" in self.prop else None
    async def async_select_option(self, option):
        if option not in self.options:
            raise HomeAssistantError(
                "Invalid equalizer preset",
                translation_domain="what",
                translation_key="invalid_equalizer_preset",
            )
        await self.send("icue_property", property=5, value=int(option))


class PowerMode(WindowsControlEntity, SelectEntity):
    _modes = {"efficiency": "Best power efficiency", "balanced": "Balanced", "performance": "Best performance"}
    _attr_options = list(_modes.values())
    def __init__(self, coordinator, supply, label):
        super().__init__(
            coordinator,
            "windows_power_mode_" + supply,
            "Power mode: " + label,
            "power_plan",
        )
        self.supply = supply
    @property
    def available(self):
        return super().available and self.current_option is not None
    @property
    def current_option(self):
        power = self.controls.get("power", {})
        key = power.get("mode_keys", {}).get(self.supply)
        return self._modes.get(key) or power.get("modes", {}).get(self.supply)
    async def async_select_option(self, option):
        if option not in self.options:
            raise HomeAssistantError(
                "Invalid power mode",
                translation_domain="what",
                translation_key="invalid_power_mode",
            )
        # Older apps accept labels. Newer apps advertise stable keys so wording
        # changes do not alter the command protocol or existing entity options.
        power = self.controls.get("power", {})
        value = next(key for key, label in self._modes.items() if label == option) if "mode_keys" in power else option
        await self.command("power_mode_" + self.supply, value)
