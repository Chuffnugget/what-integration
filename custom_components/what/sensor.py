"""Dynamic hardware sensors and source-status diagnostics."""
from datetime import datetime
import time

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.core import callback

from .entity import WhatEntity
from .connection_info import APP_SENSORS, CONNECTION_SENSORS, connection_info
from .app_entity import AppEntity

# Readings arrive through the coordinator.
PARALLEL_UPDATES = 0

# Only assign a device class where its unit is unambiguous and supported by HA.
UNITS = {
    "C": ("°C", SensorDeviceClass.TEMPERATURE), "°C": ("°C", SensorDeviceClass.TEMPERATURE),
    "F": ("°F", SensorDeviceClass.TEMPERATURE), "°F": ("°F", SensorDeviceClass.TEMPERATURE),
    "V": ("V", SensorDeviceClass.VOLTAGE), "mV": ("mV", SensorDeviceClass.VOLTAGE),
    "A": ("A", SensorDeviceClass.CURRENT), "mA": ("mA", SensorDeviceClass.CURRENT),
    "W": ("W", SensorDeviceClass.POWER), "kW": ("kW", SensorDeviceClass.POWER),
    "Hz": ("Hz", SensorDeviceClass.FREQUENCY), "MHz": ("MHz", SensorDeviceClass.FREQUENCY),
    "GHz": ("GHz", SensorDeviceClass.FREQUENCY),
    "B": ("B", SensorDeviceClass.DATA_SIZE),
    "kB": ("kB", SensorDeviceClass.DATA_SIZE), "MB": ("MB", SensorDeviceClass.DATA_SIZE),
    "GB": ("GB", SensorDeviceClass.DATA_SIZE),
    "B/s": ("B/s", SensorDeviceClass.DATA_RATE),
    "kB/s": ("kB/s", SensorDeviceClass.DATA_RATE), "MB/s": ("MB/s", SensorDeviceClass.DATA_RATE),
    "GB/s": ("GB/s", SensorDeviceClass.DATA_RATE),
    "bit/s": ("bit/s", SensorDeviceClass.DATA_RATE),
    "s": ("s", SensorDeviceClass.DURATION),
}


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities([AppDiagnostic(coordinator, key, name) for key, name in APP_SENSORS.items()])
    async_add_entities(
        [ConnectionDiagnostic(coordinator, key, name) for key, name in CONNECTION_SENSORS.items()],
    )
    from .installation_sensor import async_setup_installation_sensors
    async_setup_installation_sensors(entry, async_add_entities)
    from .beacn import BeacnStatus
    async_add_entities([BeacnStatus(coordinator)])
    from .wallpaper import WallpaperStatus, WallpaperCurrent
    async_add_entities([WallpaperStatus(coordinator), WallpaperCurrent(coordinator)])
    async_add_entities([DisplayArrangement(coordinator), CleanupStatus(coordinator)])
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
            if row is None or identifier in APP_SENSORS:
                continue
            if identifier not in known and row["kind"] != "binary":
                known.add(identifier)
                entities.append(HardwareSensor(coordinator, identifier, row))
        if entities:
            async_add_entities(entities)

    entry.async_on_unload(coordinator.async_add_listener(add_new))
    add_new()
    async_add_entities([StatusSensor(coordinator), LicenceSensor(coordinator)])


class HardwareSensor(WhatEntity, SensorEntity):
    def __init__(self, coordinator, identifier, row):
        super().__init__(
            coordinator,
            identifier,
            f"{'Windows ' if row['source'] == 'windows' else ''}{row['group']} {row['name']}".strip(),
        )
        self.identifier = identifier
        unit, device_class = UNITS.get(row["unit"], (row["unit"] or None, None))
        self._attr_native_unit_of_measurement = unit
        # Temperature differences do not use the offset in Celsius/Fahrenheit conversion.
        # Keep the existing Celsius unit and history, without HA's absolute-temperature conversion.
        if row["source"] == "coretemp" and identifier.endswith("_distance_to_tjmax"):
            device_class = None
        self._attr_device_class = device_class
        self._kind = row["kind"]
        self._percentage = self._kind == "number" and unit == "%"
        if self._percentage:
            self._attr_suggested_display_precision = 2
        self._attr_state_class = None
        if self._kind == "number" and row.get("state_class"):
            self._attr_state_class = SensorStateClass(row["state_class"])
        if self._kind == "timestamp":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        if self._kind != "number":
            self._attr_native_unit_of_measurement = None
        # Prevent silently changing the meaning/unit of existing history.
        self._original_unit = row["unit"]

    @property
    def available(self):
        row = self.coordinator.readings.get(self.identifier)
        return bool(super().available and row and row["available"]
                    and row["value"] is not None and row["unit"] == self._original_unit and row["kind"] == self._kind)

    @property
    def native_value(self):
        row = self.coordinator.readings.get(self.identifier)
        if not row or row["value"] is None:
            return None
        if self._kind == "timestamp":
            return datetime.fromisoformat(row["value"].replace("Z", "+00:00"))
        if self._percentage:
            return round(row["value"], 2)
        return row["value"]


class StatusSensor(WhatEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator):
        super().__init__(coordinator, "source_status", "HWiNFO status")

    @property
    def native_value(self):
        return self.coordinator.data["status"][:255]


class LicenceSensor(WhatEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["unknown", "free", "pro"]

    def __init__(self, coordinator):
        super().__init__(coordinator, "license", "HWiNFO edition (self-reported)")

    @property
    def native_value(self):
        return self.coordinator.data["license"]


class DisplayArrangement(WhatEntity, SensorEntity):
    """One stable source for the dashboard editor, including disabled monitors."""
    _attr_icon = "mdi:monitor-dashboard"
    _unrecorded_attributes = frozenset({"monitors", "status", "control_enabled", "layout_editor", "busy", "pending"})

    def __init__(self, coordinator):
        super().__init__(coordinator, "windows_displays_arrangement", "Display arrangement")

    @property
    def module(self):
        return (self.coordinator.data or {}).get("displays", {})
    @property
    def available(self):
        return bool(
            (
                self.coordinator.last_update_success
                and self.module.get("enabled")
                and self.module.get("available")
            ),
        )
    @property
    def native_value(self):
        return sum(m.get("enabled", False) for m in self.module.get("monitors", []))
    @property
    def extra_state_attributes(self):
        if not self.module.get("enabled"):
            return {"monitors": [], "control_enabled": False}
        return {k: self.module.get(k, [] if k == "monitors" else False) for k in ("monitors", "status", "control_enabled", "layout_editor", "busy", "pending")}


class ConnectionDiagnostic(WhatEntity, SensorEntity):
    """PC-level diagnostics; disconnect remains visible rather than unavailable."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, key, name):
        super().__init__(coordinator, key, name)
        self.key = key
        if key == "connection_uptime":
            self._attr_native_unit_of_measurement = "s"
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_suggested_display_precision = 0

    @property
    def available(self):
        return self.key.startswith("connection_") or self.coordinator.last_update_success

    @property
    def native_value(self):
        return connection_info(self.coordinator)[self.key]

    @property
    def extra_state_attributes(self):
        if self.key == "connection_active_mode":
            info = connection_info(self.coordinator)
            return {"transport": info["connection_transport"], "endpoint_scope": info["endpoint_scope"]}
        if self.key == "connection_transport":
            return {"endpoint_scope": connection_info(self.coordinator)["endpoint_scope"]}
        if self.key == "connection_uptime":
            since = getattr(self.coordinator, "connected_since", None)
            return {"connected_since": since.isoformat() if since else None}
        return None


class CleanupStatus(WhatEntity, SensorEntity):
    """The latest cleanup preview, kept on the parent PC device."""
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:broom"
    _unrecorded_attributes = frozenset({"entities"})

    def __init__(self, coordinator):
        super().__init__(coordinator, "entity_cleanup", "Entity cleanup")

    @property
    def native_value(self):
        return self.coordinator.entity_maintenance.state["status"]

    @property
    def extra_state_attributes(self):
        return {key: value for key, value in self.coordinator.entity_maintenance.state.items()
                if key != "status"}


class AppDiagnostic(AppEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, key, name):
        super().__init__(coordinator, key, name)
        self.key = key
        self._last_written = 0
        if key == "app_last_seen":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        elif key == "app_uptime":
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_native_unit_of_measurement = "s"
            self._attr_suggested_display_precision = 0
        else:
            self._attr_native_unit_of_measurement = "%" if key == "app_cpu" else "MiB"
            self._attr_suggested_display_precision = 1
            self._attr_state_class = SensorStateClass.MEASUREMENT
            if key == "app_memory":
                self._attr_device_class = SensorDeviceClass.DATA_SIZE

    @property
    def available(self):
        # Keep the last reception time visible after the PC disconnects.
        return True if self.key == "app_last_seen" else super().available

    @property
    def native_value(self):
        if self.key == "app_last_seen":
            return getattr(self.coordinator, "last_seen", None)
        field = {"app_uptime": "uptime_seconds", "app_cpu": "cpu_percent", "app_memory": "memory_bytes"}[self.key]
        value = self.app.get(field)
        if value is None:
            return None
        if self.key == "app_memory":
            return round(value / 1048576, 1)
        return round(value, 1) if self.key == "app_cpu" else int(value)

    @property
    def extra_state_attributes(self):
        if self.key == "app_memory":
            return {"measurement": "Working set of WHAT.exe; helper processes excluded"}
        if self.key == "app_cpu":
            return {"measurement": "WHAT.exe CPU time across all logical processors; helpers excluded"}
        return None

    def _handle_coordinator_update(self):
        if self.key != "app_last_seen":
            super()._handle_coordinator_update()
            return
        now = time.monotonic()
        # Fast sensor deltas can arrive many times a second. Record at most every
        # five seconds, plus the exact last reception time when a connection ends.
        if (now - self._last_written >= 5 or not self.coordinator.last_update_success
                or getattr(self.coordinator, "force_entity_update", False)):
            self._last_written = now
            self.async_write_ha_state()
