"""Update entity showing whether a newer WHAT app is available (install it from the PC)."""
from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.const import EntityCategory
from .app_entity import AppEntity

# Readings arrive through the coordinator.
PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([WhatUpdate(entry.runtime_data)])


class WhatUpdate(AppEntity, UpdateEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_title = "WHAT"
    _attr_supported_features = UpdateEntityFeature(0)

    def __init__(self, coordinator):
        super().__init__(coordinator, "app_update", "WHAT update")

    @property
    def release(self):
        return self.app.get("update", {})

    @property
    def installed_version(self):
        return self.release.get("installed")

    @property
    def latest_version(self):
        return self.release.get("latest")

    @property
    def release_url(self):
        return self.release.get("release_url")

    @property
    def extra_state_attributes(self):
        return {"channel": self.release.get("channel"), "last_checked": self.release.get("checked_at")}
