"""One sensor per Home Assistant installation the PC reports, showing its connection state."""
from datetime import datetime, timezone

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback

from .entity import WhatEntity


def async_setup_installation_sensors(entry, async_add_entities):
    coordinator = entry.runtime_data
    known = set()

    @callback
    def add_new():
        current = getattr(coordinator, "installation_connections", {})
        additions = [InstallationConnection(coordinator, identifier) for identifier in current if identifier not in known]
        known.update(current)
        if additions:
            async_add_entities(additions)

    entry.async_on_unload(coordinator.async_add_listener(add_new))
    add_new()


class InstallationConnection(WhatEntity, SensorEntity):
    """Other destinations are unknown if this Home Assistant cannot reach WHAT."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_icon = "mdi:lan-connect"
    _attr_options = ["connected", "disabled", "stopped", "waiting", "connecting", "retrying", "error"]
    _unrecorded_attributes = frozenset({"last_reply"})

    def __init__(self, coordinator, installation_id):
        self.installation_id = installation_id
        row = getattr(coordinator, "installation_connections", {}).get(installation_id, {})
        super().__init__(coordinator, "installation_connection_" + installation_id,
                         "Connection - " + row.get("name", installation_id))

    @property
    def row(self):
        return getattr(self.coordinator, "installation_connections", {}).get(self.installation_id)

    @property
    def name(self):
        # Preserve the last friendly name when a deleted installation becomes unavailable.
        if self.row:
            self._attr_name = "Connection - " + self.row["name"]
        return self._attr_name

    @property
    def available(self):
        return bool(self.coordinator.last_update_success and self.row)

    @property
    def native_value(self):
        return self.row["status"] if self.row else None

    @property
    def extra_state_attributes(self):
        row = self.row
        if not self.available:
            return None
        def timestamp(key):
            value = row[key]
            return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat() if value else None
        return {
            "installation_id": self.installation_id,
            "transport": "MQTT" if row["type"] == "mqtt" else "Home Assistant API",
            "sharing_scope": row["scope"],
            "connected_since": timestamp("connected_since"),
            "last_reply": timestamp("last_reply"),
        }
