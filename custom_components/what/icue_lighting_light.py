"""Light entities for the two colour slots of the Lighting Link effect. Turning off stops the effect."""
from homeassistant.components.light import LightEntity, ColorMode, LightEntityFeature
from homeassistant.exceptions import HomeAssistantError
from .icue_lighting import LightingEntity


class LightingLinkLight(LightingEntity, LightEntity):
    def __init__(self, coordinator, slot=1):
        super().__init__(
            coordinator,
            "effect" if slot == 1 else "colour_2",
            "iCUE Lighting Link" if slot == 1 else "iCUE Lighting Link second colour",
        )
        self.slot = slot
        self.key = f"Color{slot}"
        self._attr_supported_features = LightEntityFeature.EFFECT if slot == 1 else LightEntityFeature(0)
    @property
    def available(self):
        return super().available and (self.slot == 1 or self.key in self.supported_parameters)
    @property
    def supported_color_modes(self):
        return {ColorMode.RGB} if self.key in self.supported_parameters or self.current_effect is None else {ColorMode.ONOFF}
    @property
    def color_mode(self):
        return next(iter(self.supported_color_modes))
    @property
    def is_on(self):
        return self.current_effect is not None
    @property
    def raw_rgb(self):
        value = self.parameters.get(self.key)
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5)) if isinstance(value, str) and len(value) == 7 else None
    @property
    def rgb_color(self):
        rgb = self.raw_rgb
        return tuple(round(v * 255 / max(rgb)) for v in rgb) if rgb and max(rgb) else (255, 255, 255) if rgb else None
    @property
    def brightness(self):
        return max(self.raw_rgb) if self.raw_rgb is not None else None
    @property
    def effect_list(self):
        return [e["name"] for e in self.lighting.get("effects", [])]
    @property
    def effect(self):
        return next(
            (e["name"] for e in self.lighting.get("effects", []) if e["id"] == self.current_effect),
            None,
        )

    async def async_turn_on(self, **kwargs):
        choices = {e["name"]: e for e in self.lighting.get("effects", [])}
        explicit = "effect" in kwargs
        if explicit and (self.slot != 1 or kwargs["effect"] not in choices):
            raise HomeAssistantError(
                "Unsupported Lighting Link effect",
                translation_domain="what",
                translation_key="unsupported_lighting_link_effect",
            )
        target = choices[kwargs["effect"]] if explicit else next((e for e in choices.values() if e["id"] == self.current_effect), None)
        if target is None:
            target = next(
                (e for e in choices.values() if e["id"] == "StaticColor"),
                next(iter(choices.values()), None),
            )
        if target is None:
            raise HomeAssistantError(
                "No supported Lighting Link effects",
                translation_domain="what",
                translation_key="no_supported_lighting_link_effects",
            )
        if (
            "rgb_color" in kwargs
            and self.key not in target["parameters"]
            and not explicit
            and self.slot == 1
        ):
            target = next((e for e in choices.values() if e["id"] == "StaticColor"), target)
        args = {"effect": target["id"]}
        if not explicit and target["id"] == self.current_effect:
            args["expected_effect"] = self.current_effect
        if "rgb_color" in kwargs or "brightness" in kwargs:
            if self.key not in target["parameters"]:
                raise HomeAssistantError(
                    "This effect has no adjustable colour/brightness slot",
                    translation_domain="what",
                    translation_key="this_effect_has_no_adjustable_colour_brightness_slot",
                )
            rgb = kwargs.get("rgb_color", self.rgb_color or (255, 255, 255))
            level = kwargs.get("brightness", self.brightness if self.brightness is not None else 255)
            if (
                not isinstance(rgb, (list, tuple))
                or len(rgb) != 3
                or any(type(v) not in (int, float) or not 0 <= v <= 255 for v in rgb)
                or type(level) not in (int, float)
                or not 0 <= level <= 255
            ):
                raise HomeAssistantError(
                    "Invalid Lighting Link colour or brightness",
                    translation_domain="what",
                    translation_key="invalid_lighting_link_colour_or_brightness",
                )
            args["parameters"] = {self.key: "#" + "".join(f"{round(v*level/255):02x}" for v in rgb)}
        await self.send(args)

    async def async_turn_off(self, **kwargs):
        await self.send({"stop": True})
