"""Text entity that opens a URL on the PC. The URL isn't kept as the entity state."""
from homeassistant.components.text import TextEntity, TextMode
from .control_entity import WindowsControlEntity

# Serialize actions within this platform; commands are never replayed.
PARALLEL_UPDATES = 1


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([LaunchURL(entry.runtime_data)])


class LaunchURL(WindowsControlEntity, TextEntity):
    _attr_native_min = 1
    _attr_native_max = 2048
    _attr_mode = TextMode.TEXT
    # URLs are commands, not a stored text value or recorder history.
    _attr_native_value = None
    def __init__(self, coordinator):
        super().__init__(coordinator, "windows_launch_url", "Open web URL", "urls")
    async def async_set_value(self, value):
        if not value:
            from homeassistant.exceptions import HomeAssistantError
            raise HomeAssistantError(
                "Enter an HTTP or HTTPS URL.",
                translation_domain="what",
                translation_key="enter_an_http_or_https_url",
            )
        await self.command("open_url", value)
        self.async_write_ha_state()
