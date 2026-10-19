"""Light entities for Windows Dynamic Lighting devices. State is the last colour WHAT applied."""
import aiohttp
from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.exceptions import HomeAssistantError
from .entity import WhatEntity


def discover_dynamic_lighting(entry, async_add_entities):
    coordinator = entry.runtime_data
    seen = set()

    def update():
        added = []
        for device in (coordinator.data or {}).get("dynamic_lighting", {}).get("devices", []):
            if device["id"] not in seen:
                seen.add(device["id"])
                added.append(DynamicLightingLight(coordinator, device))
        if added:
            async_add_entities(added)

    entry.async_on_unload(coordinator.async_add_listener(update))
    update()


class DynamicLightingLight(WhatEntity, LightEntity):
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB

    def __init__(self, coordinator, device):
        self.lighting_id = device["id"]
        super().__init__(coordinator, device["id"], device["name"] + " lighting")

    @property
    def module(self):
        return (self.coordinator.data or {}).get("dynamic_lighting", {})

    @property
    def device(self):
        return next((d for d in self.module.get("devices", []) if d["id"] == self.lighting_id), {})

    @property
    def available(self):
        return (
            self.coordinator.last_update_success
            and self.module.get("enabled", False)
            and self.module.get("control_enabled", False)
            and self.device.get("available", False)
        )

    @property
    def is_on(self):
        rgb = self.device.get("rgb")
        brightness = self.device.get("brightness")
        return bool(any(rgb) and brightness > 0) if rgb is not None and brightness is not None else None

    @property
    def rgb_color(self):
        rgb = self.device.get("rgb")
        return tuple(rgb) if rgb is not None else None

    @property
    def brightness(self):
        value = self.device.get("brightness")
        return round(value * 255 / 100) if value is not None else None

    @property
    def extra_state_attributes(self):
        return {"lamp_count": self.device.get("lamp_count"), "state_source": "Last colour applied by WHAT"}

    def _handle_coordinator_update(self):
        state = (self.available, self.device)
        if state != getattr(self, "_previous_lighting_state", None):
            self._previous_lighting_state = state
            self.async_write_ha_state()

    async def _send(self, rgb, brightness):
        if not self.available:
            raise HomeAssistantError(
                "Enable WHAT Dynamic Lighting, Home Assistant control and Windows lighting priority",
                translation_domain="what",
                translation_key="enable_what_dynamic_lighting_home_assistant_control_and_windows",
            )
        try:
            await self.coordinator.client.command(
                "dynamic_lighting",
                {
                    "operation": "set",
                    "device": self.lighting_id,
                    "rgb": list(rgb),
                    "brightness": round(brightness * 100 / 255, 2),
                },
            )
        except (ConnectionError, OSError, TimeoutError, aiohttp.ClientError) as err:
            raise HomeAssistantError(str(err) or "Windows lighting request failed; not retried") from err

    async def async_turn_on(self, **kwargs):
        await self._send(
            kwargs.get(
                "rgb_color",
                self.rgb_color if self.rgb_color and any(self.rgb_color) else (255, 255, 255),
            ),
            kwargs.get("brightness", self.brightness or 255),
        )

    async def async_turn_off(self, **kwargs):
        await self._send(self.rgb_color or (255, 255, 255), 0)
