"""Switches: WHAT diagnostic logging, BEACN toggles and writable iCUE device settings."""
from homeassistant.const import EntityCategory
from .app_entity import AppControlEntity
from homeassistant.components.switch import SwitchEntity
from .icue import IcueEntity, discover

# Serialize actions within this platform; commands are never replayed.
PARALLEL_UPDATES = 1

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([DiagnosticLogging(entry.runtime_data)])
    from .beacn import discover_beacn
    discover_beacn(entry, async_add_entities, "switch")
    discover(
        entry,
        async_add_entities,
        lambda c, d: (IcueSwitch(c, d, p) for p in d["properties"] if p["writable"] and p["id"] in (2, 3, 4) and type(p["value"]) is bool),
    )

class IcueSwitch(IcueEntity, SwitchEntity):
    def __init__(self, coordinator, device, prop):
        super().__init__(coordinator, device, f"control_{prop['id']}", prop["name"])
        self.property_id = prop["id"]
    @property
    def prop(self):
        return next(
            (p for p in (self.device or {}).get("properties", []) if p["id"] == self.property_id),
            {},
        )
    @property
    def available(self):
        return super().available and self.prop.get("writable", False)
    @property
    def is_on(self):
        return self.prop.get("value")
    async def async_turn_on(self, **kwargs):
        await self.send("icue_property", property=self.property_id, value=True)
    async def async_turn_off(self, **kwargs):
        await self.send("icue_property", property=self.property_id, value=False)


class DiagnosticLogging(AppControlEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:text-box-search-outline"

    def __init__(self, coordinator):
        super().__init__(coordinator, "app_logging", "Diagnostic logging")

    @property
    def is_on(self):
        return self.app.get("logging")

    async def async_turn_on(self, **kwargs):
        await self.command("app_logging", True)

    async def async_turn_off(self, **kwargs):
        await self.command("app_logging", False)
