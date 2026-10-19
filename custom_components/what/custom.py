"""Buttons for the saved commands defined on the PC (the script itself stays on the PC)."""
import aiohttp
from homeassistant.components.button import ButtonEntity
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .device_groups import entity_name
from .entity import WhatEntity


def discover_commands(entry, async_add_entities):
    coordinator = entry.runtime_data
    known = set()

    @callback
    def update():
        additions = []
        for command in (coordinator.data or {}).get("custom", {}).get("actions", []):
            if command["id"] not in known:
                known.add(command["id"])
                additions.append(SavedCommand(coordinator, command))
        if additions:
            async_add_entities(additions)

    entry.async_on_unload(coordinator.async_add_listener(update))
    update()


class SavedCommand(WhatEntity, ButtonEntity):
    _attr_icon = "mdi:console"

    def __init__(self, coordinator, command):
        super().__init__(coordinator, command["id"], command["name"])
        self.command_id = command["id"]

    @property
    def command(self):
        return next((value for value in (self.coordinator.data or {}).get("custom", {}).get("actions", [])
                     if value["id"] == self.command_id), {})

    @property
    def available(self):
        state = (self.coordinator.data or {}).get("custom", {})
        return bool(self.coordinator.last_update_success and state.get("enabled")
                    and state.get("control_enabled") and self.command.get("available"))

    @callback
    def _handle_coordinator_update(self):
        # Pick up renames from the PC; the registry identity stays the same.
        if "name" in self.command:
            self._attr_name = entity_name(self.command["name"], "custom")
        if ("custom" in getattr(self.coordinator, "changed_modules", {"custom"})
                or getattr(self.coordinator, "force_entity_update", True)):
            self.async_write_ha_state()

    async def async_press(self):
        if not self.available:
            raise HomeAssistantError(
                "Check the Custom module, global saved-command permission and this command's "
                "Home Assistant permission in WHAT. Cloud connections also need both cloud permissions.",
                translation_domain="what",
                translation_key="check_the_custom_module_global_saved_command_permission_and",
            )
        try:
            await self.coordinator.client.command("custom_action", {"id": self.command_id}, timeout=65)
        except (ConnectionError, OSError, TimeoutError, aiohttp.ClientError) as err:
            raise HomeAssistantError(str(err) or "Command outcome unknown; it was not retried") from err
