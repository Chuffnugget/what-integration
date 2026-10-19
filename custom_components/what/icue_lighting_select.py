"""Selects for Lighting Link effect settings (speed, direction, rotation, random colour)."""
from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import HomeAssistantError
from .icue_lighting import LightingEntity


class LightingLinkSelect(LightingEntity, SelectEntity):
    def __init__(self, coordinator, parameter):
        super().__init__(
            coordinator,
            parameter.lower(),
            "iCUE Lighting Link " + ("random colours" if parameter == "RandomColor" else parameter.lower()),
        )
        self.parameter = parameter
        self.maximum = 2 if parameter == "Speed" else 3 if parameter == "Direction" else 1
    @property
    def available(self):
        return super().available and self.parameter in self.supported_parameters
    @property
    def options(self):
        return ["Chosen colours", "Random colours"] if self.parameter == "RandomColor" else [str(n) for n in range(self.maximum + 1)]
    @property
    def current_option(self):
        value = self.parameters.get(self.parameter)
        if value is None:
            return None
        return self.options[int(value)]
    async def async_select_option(self, option):
        if option not in self.options:
            raise HomeAssistantError(
                "Invalid Lighting Link option",
                translation_domain="what",
                translation_key="invalid_lighting_link_option",
            )
        value = self.options.index(option)
        await self.set_parameter(
            self.parameter,
            bool(value) if self.parameter == "RandomColor" else value,
        )
