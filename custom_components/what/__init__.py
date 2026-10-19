"""Set up the WHAT integration: one config entry per paired Windows PC."""
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import WhatCoordinator
from .services import register_services
from .remote import setup_remote, REGISTRY

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.NUMBER, Platform.SENSOR, Platform.BINARY_SENSOR, Platform.MEDIA_PLAYER, Platform.BUTTON, Platform.SELECT, Platform.TEXT, Platform.LIGHT, Platform.SWITCH, Platform.UPDATE]


async def async_setup(hass: HomeAssistant, config) -> bool:
    from .frontend import async_setup_frontend
    await async_setup_frontend(hass)
    setup_remote(hass)
    from .display_service import register_display_service
    register_display_service(hass)
    register_services(hass)
    from .announcements import register_speech_service
    register_speech_service(hass)
    from .wallpaper import register_wallpaper_service
    register_wallpaper_service(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = WhatCoordinator(hass, entry)
    await coordinator.async_connect()
    entry.runtime_data = coordinator
    from homeassistant.helpers import device_registry as dr
    from .device_groups import device_name
    try:
        coordinator.parent_device_id = dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.unique_id)},
            name=device_name(coordinator.hello["name"]), manufacturer="WHAT", model="Windows PC",
            sw_version=coordinator.hello["app_version"],
        ).id
        hass.data[REGISTRY][entry.unique_id] = coordinator
        entry.async_on_unload(entry.add_update_listener(_options_updated))
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        hass.data[REGISTRY].pop(entry.unique_id, None)
        await coordinator.async_stop()
        raise
    coordinator.start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[REGISTRY].pop(entry.unique_id, None)
        await entry.runtime_data.async_stop()
        return True
    return False


async def _options_updated(hass, entry):
    if entry.runtime_data.configuration_is_applied():
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove outstanding repair notices when the user removes this PC."""
    from .connection_health import clear_repairs
    clear_repairs(hass, entry)
