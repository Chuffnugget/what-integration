"""Wallpaper Engine sensors, buttons and the wallpaper_engine service."""
import aiohttp
import voluptuous as vol
from homeassistant.components.button import ButtonEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import config_validation as cv
from .entity import WhatEntity
from .const import DOMAIN
from .service_target import coordinator_for_device

OPERATIONS = ("pause", "stop", "play", "mute", "unmute", "openWallpaper", "nextWallpaper", "closeWallpaper", "openPlaylist", "openProfile", "applyProperties", "hideIcons", "showIcons", "getWallpaper")

class WallpaperEntity(WhatEntity):
    @property
    def module_state(self):
        return (self.coordinator.data or {}).get("wallpaper", {})
    @property
    def available(self):
        return self.coordinator.last_update_success and self.module_state.get("enabled", False)
    def _handle_coordinator_update(self):
        availability = bool(self.available)
        if ("wallpaper" in getattr(self.coordinator, "changed_modules", {"wallpaper"})
                or availability != getattr(self, "_previous_available", None)
                or getattr(self.coordinator, "force_entity_update", False)):
            self._previous_available = availability
            self.async_write_ha_state()

class WallpaperStatus(WallpaperEntity, SensorEntity):
    _attr_icon = "mdi:wallpaper"
    def __init__(self, coordinator):
        super().__init__(coordinator, "wallpaper_engine_status", "Wallpaper Engine")
    @property
    def native_value(self):
        return "Running" if self.module_state.get("running") else "Not running"
    @property
    def extra_state_attributes(self):
        return {key: self.module_state[key] for key in ("installed", "status", "current_wallpaper", "control_enabled") if key in self.module_state}

class WallpaperCurrent(WallpaperEntity, SensorEntity):
    _attr_icon = "mdi:wallpaper"
    def __init__(self, coordinator):
        super().__init__(
            coordinator,
            "wallpaper_engine_current",
            "Wallpaper Engine current wallpaper",
        )
    @property
    def available(self):
        return (
            super().available
            and self.module_state.get("share_path", False)
            and self.module_state.get("running", False)
        )
    @property
    def native_value(self):
        if not self.module_state.get("share_path", False):
            return None
        path = self.module_state.get("current_wallpaper", "")
        return path.replace("\\", "/").rsplit("/", 1)[-1][:255] or None
    @property
    def extra_state_attributes(self):
        return {"path": self.module_state.get("current_wallpaper")} if self.module_state.get("share_path", False) else {}

class WallpaperButton(WallpaperEntity, ButtonEntity):
    _attr_icon = "mdi:wallpaper"
    def __init__(self, coordinator, operation, name):
        super().__init__(coordinator, "wallpaper_engine_" + operation, "Wallpaper Engine " + name)
        self.operation = operation
    @property
    def available(self):
        return (
            super().available
            and self.module_state.get("control_enabled", False)
            and self.module_state.get("running", False)
            and not self.module_state.get("busy", False)
        )
    async def async_press(self):
        await send(self.coordinator, {"operation": self.operation})

async def send(coordinator, args):
    state = (coordinator.data or {}).get("wallpaper", {})
    if (
        not coordinator.last_update_success
        or not all(state.get(key) for key in ("enabled", "running", "control_enabled"))
    ):
        raise HomeAssistantError(
            "Enable Wallpaper Engine and Home Assistant control on the PC, and start Wallpaper Engine",
            translation_domain="what",
            translation_key="enable_wallpaper_engine_and_home_assistant_control_on_the",
        )
    try:
        return await coordinator.client.command("wallpaper", args, timeout=12)
    except (ConnectionError, OSError, TimeoutError, aiohttp.ClientError) as err:
        raise HomeAssistantError(str(err) or "Wallpaper command outcome unknown; not retried") from err

SCHEMA = vol.Schema({
    vol.Required("device_id"): vol.All(cv.ensure_list, [cv.string], vol.Length(min=1, max=1)),
    vol.Required("operation"): vol.In(OPERATIONS),
    vol.Optional("file"): cv.string,
    vol.Optional("playlist"): cv.string,
    vol.Optional("profile"): cv.string,
    vol.Optional("monitor"): vol.All(vol.Coerce(int), vol.Range(min=0)),
    vol.Optional("options", default=dict): dict,
})

def register_wallpaper_service(hass):
    async def execute(call):
        if call.context.user_id:
            user = await hass.auth.async_get_user(call.context.user_id)
            if user is None or not user.is_admin:
                raise Unauthorized()
        coordinator = coordinator_for_device(hass, call.data["device_id"][0])
        args = {**call.data["options"], "operation": call.data["operation"]}
        for key in ("file", "playlist", "profile", "monitor"):
            if key in call.data:
                args[key] = call.data[key]
        result = await send(coordinator, args)
        return {"result": result}
    hass.services.async_register(
        DOMAIN,
        "wallpaper_engine",
        execute,
        schema=SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

