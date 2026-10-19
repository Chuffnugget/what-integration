"""Whole-device and optional individual iCUE LED lights."""
from homeassistant.components.light import LightEntity, ColorMode
from .icue_lighting import discover_lighting
from .dynamic_lighting_light import discover_dynamic_lighting
from .icue_lighting_light import LightingLinkLight
from .icue import IcueEntity, discover

# Serialize actions within this platform; commands are never replayed.
PARALLEL_UPDATES = 1

async def async_setup_entry(hass, entry, async_add_entities):
    def factory(coordinator, device):
        if device["leds"]:
            yield IcueLight(coordinator, device)
            for led in device["leds"]:
                yield IcueLight(coordinator, device, led["id"])
    discover(entry, async_add_entities, factory)
    discover_dynamic_lighting(entry, async_add_entities)
    discover_lighting(
        entry,
        async_add_entities,
        lambda c: [LightingLinkLight(c), LightingLinkLight(c, 2)],
    )

class IcueLight(IcueEntity, LightEntity):
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB
    def __init__(self, coordinator, device, led=None):
        super().__init__(
            coordinator,
            device,
            "rgb" if led is None else f"led_{led}",
            "RGB lighting" if led is None else f"LED {led}",
        )
        self.led = led
        self._attr_entity_registry_enabled_default = led is None
    @property
    def channels(self):
        rows = (self.device or {}).get("leds", [])
        values = [row["rgb"] for row in rows if (self.led is None or row["id"] == self.led) and row["rgb"] is not None]
        return tuple(round(sum(v[i] for v in values) / len(values)) for i in range(3)) if values else None
    @property
    def available(self):
        return (
            super().available
            and (self.led is None or any(row["id"] == self.led for row in self.device["leds"]))
        )
    @property
    def is_on(self):
        return any(self.channels) if self.channels is not None else None
    @property
    def brightness(self):
        return max(self.channels) if self.channels is not None else None
    @property
    def rgb_color(self):
        values = self.channels
        if values is None:
            return None
        return tuple(round(v * 255 / max(values)) for v in values) if max(values) else (255, 255, 255)
    async def async_turn_on(self, **kwargs):
        rgb = kwargs.get("rgb_color", self.rgb_color or (255, 255, 255))
        brightness = kwargs.get("brightness", self.brightness or 255)
        await self.send(
            "icue_colors",
            rgb=[round(c * brightness / 255) for c in rgb],
            **{"leds": [self.led]} if self.led is not None else {},
        )
    async def async_turn_off(self, **kwargs):
        await self.send(
            "icue_colors",
            rgb=[0, 0, 0],
            **{"leds": [self.led]} if self.led is not None else {},
        )
