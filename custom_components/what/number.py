"""Numbers: WHAT publishing interval and BEACN levels."""
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory
from .app_entity import AppControlEntity
from .beacn import discover_beacn

# Serialize actions within this platform; commands are never replayed.
PARALLEL_UPDATES = 1

async def async_setup_entry(hass, entry, async_add_entities):
    discover_beacn(entry, async_add_entities, "number")
    async_add_entities([PublishingInterval(entry.runtime_data)])


class PublishingInterval(AppControlEntity, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = "ms"
    _attr_native_max_value = 60000
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator):
        super().__init__(coordinator, "app_publish_interval", "Publishing interval")

    @property
    def native_min_value(self):
        return max(100, self.app.get("high_interval_ms", 100))

    @property
    def native_value(self):
        return self.app.get("publishing_interval_ms")

    async def async_set_native_value(self, value):
        await self.command("app_publish_interval", value)
