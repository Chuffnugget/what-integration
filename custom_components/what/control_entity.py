"""Base class for entities that send Windows commands, gated by the PC's permissions."""
from .entity import WhatEntity


class WindowsControlEntity(WhatEntity):
    def __init__(self, coordinator, identifier, name, permission):
        super().__init__(coordinator, identifier, name)
        self.permission = permission

    @property
    def controls(self):
        return (self.coordinator.data or {}).get("controls", {})

    @property
    def available(self):
        return (
            self.coordinator.last_update_success
            and self.coordinator.client.commands
            and self.controls.get("permissions", {}).get(self.permission, False)
        )

    def _handle_coordinator_update(self):
        availability = bool(self.available)
        if ("controls" in getattr(self.coordinator, "changed_modules", {"controls"})
                or availability != getattr(self, "_previous_available", None)
                or getattr(self.coordinator, "force_entity_update", False)):
            self._previous_available = availability
            self.async_write_ha_state()

    async def command(self, action, value=None):
        await self.coordinator.async_command(action, self.permission, value)
