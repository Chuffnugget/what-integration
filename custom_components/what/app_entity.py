"""Base classes for entities about the WHAT app itself (on the parent PC device)."""
from .entity import WhatEntity


class AppEntity(WhatEntity):
    @property
    def app(self):
        return (self.coordinator.data or {}).get("app", {})

    @property
    def available(self):
        return self.coordinator.last_update_success and bool(self.app)

    def _handle_coordinator_update(self):
        available = bool(self.available)
        if ("app" in getattr(self.coordinator, "changed_modules", {"app"})
                or available != getattr(self, "_previous_available", None)
                or getattr(self.coordinator, "force_entity_update", False)):
            self._previous_available = available
            self.async_write_ha_state()


class AppControlEntity(AppEntity):
    @property
    def available(self):
        return (super().available and self.coordinator.client.commands
                and self.app.get("control_enabled", False))

    async def command(self, action, value=None):
        await self.coordinator.async_app_command(action, value)
