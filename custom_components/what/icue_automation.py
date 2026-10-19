"""Selects for iCUE Automation presets and profiles."""
import aiohttp
from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import HomeAssistantError
from .entity import WhatEntity
from .select_options import option_labels


def discover_automation(entry, async_add_entities):
    coordinator = entry.runtime_data
    seen = set()

    def update():
        added = []
        for control in (coordinator.data or {}).get("icue_automation", {}).get("controls", []):
            if control["id"] not in seen:
                seen.add(control["id"])
                added.append(IcueAutomationSelect(coordinator, control))
        if added:
            async_add_entities(added)

    entry.async_on_unload(coordinator.async_add_listener(update))
    update()


class IcueAutomationSelect(WhatEntity, SelectEntity):
    def __init__(self, coordinator, control):
        self.control_id = control["id"]
        name = control["name"] if self.control_id == "profile" else f'iCUE {control["device"]}: {control["name"]}'
        super().__init__(coordinator, "icue_automation_" + self.control_id, name)
        self._attr_icon = "mdi:fan" if self.control_id.startswith("cooling_") else "mdi:palette"

    @property
    def module(self):
        return (self.coordinator.data or {}).get("icue_automation", {})

    @property
    def control(self):
        return next((c for c in self.module.get("controls", []) if c["id"] == self.control_id), None)

    @property
    def available(self):
        return bool(self.coordinator.last_update_success and self.module.get("enabled")
                    and self.module.get("connected") and self.module.get("control_enabled") and self.control)

    @property
    def choices(self):
        values = (self.control or {}).get("options", [])
        return option_labels((o["name"], o["id"]) for o in values)

    @property
    def options(self):
        return list(self.choices)

    @property
    def current_option(self):
        current = (self.control or {}).get("current")
        return next((name for name, identifier in self.choices.items() if identifier == current), None)

    @property
    def extra_state_attributes(self):
        return {
            "experimental_api": True,
            "current_preset_reported": bool((self.control or {}).get("confirmed")),
        }

    def _handle_coordinator_update(self):
        state = (self.available, self.control)
        if state != getattr(self, "_previous_automation_state", None):
            self._previous_automation_state = state
            self.async_write_ha_state()

    async def async_select_option(self, option):
        if not self.available:
            raise HomeAssistantError(
                "Enable iCUE Profiles & device controls and its Home Assistant permission on the PC; approve SDK access in iCUE",
                translation_domain="what",
                translation_key="enable_icue_profiles_device_controls_and_its_home_assistant",
            )
        choices = self.choices
        if option not in choices:
            raise HomeAssistantError(
                "That iCUE preset is no longer available",
                translation_domain="what",
                translation_key="that_icue_preset_is_no_longer_available",
            )
        try:
            await self.coordinator.client.command(
                "icue_automation",
                {"control": self.control_id, "option": choices[option]},
            )
        except (ConnectionError, OSError, TimeoutError, aiohttp.ClientError) as err:
            raise HomeAssistantError(str(err) or "iCUE command timed out; not retried") from err
