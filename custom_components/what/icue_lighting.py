"""Base class and discovery for iCUE Lighting Link entities."""
import aiohttp
from homeassistant.exceptions import HomeAssistantError
from .entity import WhatEntity


def discover_lighting(entry, add, factory):
    coordinator = entry.runtime_data
    seen = False
    def update():
        nonlocal seen
        if (
            not seen
            and (coordinator.data or {}).get("icue_automation", {}).get("lighting", {}).get("effects")
        ):
            seen = True
            add(list(factory(coordinator)))
    entry.async_on_unload(coordinator.async_add_listener(update))
    update()


class LightingEntity(WhatEntity):
    def __init__(self, coordinator, identifier, name):
        super().__init__(coordinator, "icue_lighting_" + identifier, name)
        self._attr_icon = "mdi:palette"

    @property
    def module(self):
        return (self.coordinator.data or {}).get("icue_automation", {})
    @property
    def lighting(self):
        return self.module.get("lighting", {})
    @property
    def current_effect(self):
        return self.lighting.get("effect")
    @property
    def parameters(self):
        return self.lighting.get("parameters", {})
    @property
    def supported_parameters(self):
        return next(
            (e["parameters"] for e in self.lighting.get("effects", []) if e["id"] == self.current_effect),
            [],
        )
    @property
    def available(self):
        return bool(self.coordinator.last_update_success and self.module.get("enabled") and self.module.get("connected")
                    and self.module.get("control_enabled") and self.lighting.get("available"))
    @property
    def extra_state_attributes(self):
        return {
            "experimental_api": True,
            "scope": "global Lighting Link override",
            "off_action": "Stop override and return to iCUE profile lighting",
        }

    def _handle_coordinator_update(self):
        state = (self.available, self.lighting)
        if state != getattr(self, "_previous_lighting_state", None):
            self._previous_lighting_state = state
            self.async_write_ha_state()

    async def send(self, args):
        if not self.available:
            raise HomeAssistantError(
                "Enable iCUE Profiles & device controls and its Home Assistant permission; approve SDK access in iCUE",
                translation_domain="what",
                translation_key="enable_icue_profiles_device_controls_and_its_home_assistant_permission_approve_sdk_access_in_icue",
            )
        try:
            await self.coordinator.client.command("icue_lighting", args)
        except (ConnectionError, OSError, TimeoutError, aiohttp.ClientError) as err:
            raise HomeAssistantError(str(err) or "Lighting Link request timed out; not retried") from err

    async def set_parameter(self, parameter, value):
        if parameter not in self.supported_parameters:
            raise HomeAssistantError(
                "The current Lighting Link effect does not support this parameter",
                translation_domain="what",
                translation_key="the_current_lighting_link_effect_does_not_support_this",
            )
        await self.send({"expected_effect": self.current_effect, "parameters": {parameter: value}})
