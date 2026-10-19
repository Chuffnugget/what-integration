"""Resolve the WHAT PC a service call is aimed at."""
from .const import DOMAIN


def coordinator_for_device(hass, device_id):
    """Return the loaded coordinator for a device picked in a service call.

    Imports happen here rather than at module level so the service modules can be
    reloaded independently in tests.
    """
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers import device_registry as dr

    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise HomeAssistantError(
            "Choose a WHAT PC",
            translation_domain=DOMAIN,
            translation_key="choose_a_what_pc",
        )
    entries = [hass.config_entries.async_get_entry(identifier) for identifier in device.config_entries]
    coordinators = [entry.runtime_data for entry in entries if (
        entry is not None
        and entry.domain == DOMAIN
        and getattr(entry, "runtime_data", None) is not None
    )]
    if len(coordinators) != 1:
        raise HomeAssistantError(
            "The selected PC is not loaded or is ambiguous",
            translation_domain=DOMAIN,
            translation_key="the_selected_pc_is_not_loaded_or_is_ambiguous",
        )
    return coordinators[0]
