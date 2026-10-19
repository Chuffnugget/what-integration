"""Buttons: Windows power actions, saved commands, display, iCUE, Wallpaper Engine and maintenance."""
from .displays import discover_displays
from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from .entity import WhatEntity
from .entity_cleanup import selection_is_fresh
from .control_entity import WindowsControlEntity
from .app_entity import AppControlEntity
from .icue_lighting import LightingEntity, discover_lighting
from .icue import IcueEntity, discover

# Serialize actions within this platform; commands are never replayed.
PARALLEL_UPDATES = 1


async def async_setup_entry(hass, entry, async_add_entities):
    from .custom import discover_commands
    discover_commands(entry, async_add_entities)
    async_add_entities([RestartWhat(entry.runtime_data)])
    async_add_entities([CleanupAction(entry.runtime_data, operation, name) for operation, name in (
        ("preview", "Preview entity cleanup"), ("remove", "Remove previewed entities"),
    )])
    from .beacn import discover_beacn
    discover_beacn(entry, async_add_entities, "button")
    from .wallpaper import WallpaperButton
    async_add_entities([WallpaperButton(entry.runtime_data, op, name) for op, name in (
        ("play", "play"), ("pause", "pause"), ("stop", "stop"), ("mute", "mute"), ("unmute", "unmute"),
        ("nextWallpaper", "next wallpaper"), ("hideIcons", "hide desktop icons"), ("showIcons", "show desktop icons"),
    )])
    discover_displays(entry, async_add_entities, "button")
    discover_lighting(entry, async_add_entities, lambda c: [LightingLinkStop(c)])
    discover(entry, async_add_entities, lambda c, d: [IcueRelease(c, d)] if d["leds"] else [])
    async_add_entities([WindowsAction(entry.runtime_data, action, name, permission) for action, name, permission in (
        ("sleep", "Sleep", "power"), ("hibernate", "Hibernate", "power"),
        ("reboot", "Restart Windows", "power"), ("shutdown", "Shut down Windows", "power"),
        ("lock", "Lock Windows", "power"), ("monitor_sleep", "Sleep monitors", "monitor"),
    )])


class WindowsAction(WindowsControlEntity, ButtonEntity):
    def __init__(self, coordinator, action, name, permission):
        super().__init__(coordinator, "command_" + action, name, permission)
        self.action = action
    async def async_press(self):
        await self.command(self.action)

class IcueRelease(IcueEntity, ButtonEntity):
    def __init__(self, coordinator, device):
        super().__init__(coordinator, device, "release", "Return lighting to iCUE")
    async def async_press(self):
        await self.send("icue_release")


class LightingLinkStop(LightingEntity, ButtonEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, "stop", "Stop iCUE Lighting Link override")
    async def async_press(self):
        await self.send({"stop": True})


class CleanupAction(WhatEntity, ButtonEntity):
    """PC-level maintenance with a separate, time-limited review step."""
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:broom"

    def __init__(self, coordinator, operation, name):
        super().__init__(coordinator, "entity_cleanup_" + operation, name)
        self.operation = operation

    @property
    def available(self):
        if self.operation == "remove":
            return self.coordinator.entity_maintenance.can_remove
        return selection_is_fresh(self.coordinator)

    async def async_press(self):
        await self.coordinator.entity_maintenance.async_device_action(self.operation)


class RestartWhat(AppControlEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:restart"

    def __init__(self, coordinator):
        super().__init__(coordinator, "app_restart", "Restart WHAT")

    @property
    def available(self):
        return super().available and self.app.get("restart_available", False)

    async def async_press(self):
        await self.command("app_restart")
