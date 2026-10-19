"""The apply_display_layout service used by the display layout card."""
import aiohttp
import voluptuous as vol
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import entity_registry as er
from .const import DOMAIN
from .displays_protocol import validate_layout


def layout(value):
    try:
        return validate_layout(value)
    except ValueError as err:
        raise vol.Invalid(str(err)) from err

SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): vol.Match(r"^sensor\.[a-z0-9_]+$"),
        vol.Required("layout"): layout,
        vol.Required("expected"): layout,
    },
)


def register_display_service(hass):
    async def apply(call):
        if call.context.user_id:
            user = await hass.auth.async_get_user(call.context.user_id)
            if user is None or not user.is_admin:
                raise Unauthorized()
        entity = er.async_get(hass).async_get(call.data["entity_id"])
        if (
            entity is None
            or entity.platform != DOMAIN
            or not entity.unique_id.endswith("_windows_displays_arrangement")
        ):
            raise HomeAssistantError(
                "Choose the WHAT Display arrangement sensor",
                translation_domain="what",
                translation_key="choose_the_what_display_arrangement_sensor",
            )
        entry = hass.config_entries.async_get_entry(entity.config_entry_id)
        coordinator = getattr(entry, "runtime_data", None)
        if entry is None or entry.domain != DOMAIN or coordinator is None:
            raise HomeAssistantError(
                "This WHAT PC is not loaded",
                translation_domain="what",
                translation_key="this_what_pc_is_not_loaded",
            )
        module = (coordinator.data or {}).get("displays", {})
        if (
            not coordinator.last_update_success
            or not all(module.get(k) for k in ("enabled", "available", "control_enabled", "layout_editor"))
            or module.get("busy")
            or module.get("pending")
        ):
            raise HomeAssistantError(
                "Enable Displays and Home Assistant display control, update WHAT, and resolve any pending recovery first",
                translation_domain="what",
                translation_key="enable_displays_and_ha_display_control_update_what_and",
            )
        connected = {m["id"] for m in module.get("monitors", [])}
        if any(row["id"] not in connected for row in call.data["layout"]):
            raise HomeAssistantError(
                "A display is no longer connected; reload the preview",
                translation_domain="what",
                translation_key="a_display_is_no_longer_connected_reload_the_preview",
            )
        try:
            response = await coordinator.client.command(
                "display",
                {
                    "operation": "apply",
                    "layout": call.data["layout"],
                    "expected": call.data["expected"],
                },
                timeout=30,
            )
        except (ConnectionError, OSError, TimeoutError, aiohttp.ClientError) as err:
            raise HomeAssistantError(
                (
                    str(err)
                    or "Display change outcome unknown. Reload the preview; no automatic retry was sent."
                ),
            ) from err
        return {"result": response}
    hass.services.async_register(
        DOMAIN,
        "apply_display_layout",
        apply,
        schema=SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
