"""Binary sensors: Windows on/off readings plus connection and data-problem diagnostics."""
from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback

from .entity import WhatEntity

# Readings arrive through the coordinator.
PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    known = set()
    discovery_started = False

    @callback
    def add_new():
        nonlocal discovery_started
        entities = []
        candidates = getattr(coordinator, "added_ids", coordinator.readings.keys()) if discovery_started else coordinator.readings.keys()
        discovery_started = True
        for identifier in candidates:
            row = coordinator.readings.get(identifier)
            if row is None:
                continue
            if identifier not in known and row["kind"] == "binary":
                known.add(identifier)
                entities.append(WindowsBinarySensor(coordinator, identifier, row))
        if entities:
            async_add_entities(entities)

    entry.async_on_unload(coordinator.async_add_listener(add_new))
    add_new()
    async_add_entities(
        [ConnectionSensor(coordinator), SourceSensor(coordinator), WindowsSourceSensor(coordinator)],
    )


class ConnectionSensor(WhatEntity, BinarySensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_available = True

    def __init__(self, coordinator):
        super().__init__(coordinator, "connection", "PC connection")

    @property
    def available(self):
        return True

    @property
    def is_on(self):
        return self.coordinator.last_update_success


class SourceSensor(WhatEntity, BinarySensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator):
        super().__init__(coordinator, "source_problem", "HWiNFO data problem")

    @property
    def is_on(self):
        return self.coordinator.data["hwinfo_enabled"] and not self.coordinator.data["healthy"]


class WindowsBinarySensor(WhatEntity, BinarySensorEntity):
    def __init__(self, coordinator, identifier, row):
        super().__init__(coordinator, identifier, f"{row['group']} {row['name']}")
        self.identifier = identifier
        # No lock device class: Windows' value is True when locked, the inverse
        # of HA's lock binary_sensor semantics. Preserve the plain boolean.

    @property
    def available(self):
        row = self.coordinator.readings.get(self.identifier)
        return bool(super().available and row and row["available"] and row["kind"] == "binary")

    @property
    def is_on(self):
        row = self.coordinator.readings.get(self.identifier)
        return row["value"] if row else None


class WindowsSourceSensor(WhatEntity, BinarySensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator):
        super().__init__(coordinator, "windows_problem", "Windows collection problem")

    @property
    def is_on(self):
        return not self.coordinator.data["windows_healthy"]
