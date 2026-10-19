"""Shared PC identity for WHAT entities."""
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .device_groups import MODULES, module_for, device_name, entity_name


# Only fixed WHAT controls are translated. PC-supplied reading and command names stay intact.
TRANSLATED_ENTITIES = frozenset({
    "app_cpu",
    "app_last_seen",
    "app_logging",
    "app_memory",
    "app_publish_interval",
    "app_restart",
    "app_update",
    "app_uptime",
    "beacn_status",
    "beacn_unavailable_controls",
    "command_hibernate",
    "command_lock",
    "command_monitor_sleep",
    "command_reboot",
    "command_shutdown",
    "command_sleep",
    "connection",
    "connection_active_mode",
    "connection_mode",
    "connection_transport",
    "connection_uptime",
    "entity_cleanup",
    "entity_cleanup_preview",
    "entity_cleanup_remove",
    "license",
    "published_entities",
    "source_problem",
    "source_status",
    "unavailable_readings",
    "wallpaper_engine_current",
    "wallpaper_engine_hideIcons",
    "wallpaper_engine_mute",
    "wallpaper_engine_nextWallpaper",
    "wallpaper_engine_pause",
    "wallpaper_engine_play",
    "wallpaper_engine_showIcons",
    "wallpaper_engine_status",
    "wallpaper_engine_stop",
    "wallpaper_engine_unmute",
    "windows_announcements",
    "windows_displays_arrangement",
    "windows_launch_url",
    "windows_media_control",
    "windows_power_mode_ac",
    "windows_power_mode_dc",
    "windows_power_plan",
    "windows_problem",
})


class WhatEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def _handle_coordinator_update(self):
        identifier = getattr(self, "identifier", None)
        if (identifier is None or not self.coordinator.last_update_success
                or getattr(self.coordinator, "force_entity_update", True)
                or identifier in getattr(self.coordinator, "changed_ids", ())):
            self.async_write_ha_state()

    def __init__(self, coordinator, identifier: str, name: str):
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.unique_id}_{identifier}"
        module = module_for(identifier, getattr(coordinator, "readings", {}))
        if identifier in TRANSLATED_ENTITIES:
            # Existing Wallpaper Engine IDs contain capitals. HA translation keys
            # must be lowercase; the entity's stored identity stays unchanged.
            self._attr_translation_key = identifier.lower()
        else:
            self._attr_name = entity_name(name, module)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.unique_id + ("_module_" + module if module else ""))},
            name=device_name(coordinator.hello["name"], module),
            manufacturer="WHAT",
            model=MODULES.get(module, "Windows PC"),
            sw_version=coordinator.hello["app_version"],
        )

        if module:
            self._attr_device_info["via_device_id"] = coordinator.parent_device_id
