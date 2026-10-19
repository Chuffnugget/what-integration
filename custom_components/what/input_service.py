"""The send_keyboard and send_mouse services."""
import aiohttp
import voluptuous as vol
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import config_validation as cv
from .const import DOMAIN
from .notification_service import bounded_text
from .service_target import coordinator_for_device

KEYS = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") + ["CTRL", "ALT", "SHIFT", "WIN", "ENTER", "TAB", "ESC", "SPACE", "BACKSPACE", "DELETE", "INSERT", "HOME", "END", "PAGEUP", "PAGEDOWN", "LEFT", "RIGHT", "UP", "DOWN", "PRINTSCREEN"] + [f"F{i}" for i in range(1, 25)]
def key_list(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 6:
        raise vol.Invalid("Provide 1-6 keys")
    result = [vol.In(KEYS)(cv.string(k).upper()) for k in value]
    if len(set(result)) != len(result):
        raise vol.Invalid("Duplicate keys")
    return result

def coordinate(value):
    if type(value) not in (int, float) or not -100000 <= value <= 100000 or int(value) != value:
        raise vol.Invalid("Use an integer desktop coordinate")
    return int(value)

def keyboard_fields(data):
    if ("text" in data) == ("keys" in data):
        raise vol.Invalid("Provide either keys or text")
    return data

def mouse_fields(data):
    if data["button"] == "none" and "clicks" in data:
        raise vol.Invalid("Clicks require a button")
    return data

DEVICE = {vol.Required("device_id"): vol.All(cv.ensure_list, [cv.string], vol.Length(min=1, max=1))}
KEYBOARD_SCHEMA = vol.All(
    vol.Schema({**DEVICE, vol.Optional("keys"): key_list, vol.Optional("text"): bounded_text(1024)}),
    keyboard_fields,
)
MOUSE_SCHEMA = vol.All(
    vol.Schema(
        {
            **DEVICE,
            vol.Required("x"): coordinate,
            vol.Required("y"): coordinate,
            vol.Optional("button", default="none"): vol.In(("none", "left", "right", "middle")),
            vol.Optional("clicks"): vol.All(coordinate, vol.Range(min=1, max=2)),
        },
    ),
    mouse_fields,
)

def register_input_services(hass):
    async def send(call):
        if call.context.user_id:
            user = await hass.auth.async_get_user(call.context.user_id)
            if user is None or not user.is_admin:
                raise Unauthorized()
        coordinator = coordinator_for_device(hass, call.data["device_id"][0])
        if (
            not coordinator.last_update_success
            or not (coordinator.data or {}).get("controls", {}).get("input_enabled", False)
        ):
            raise HomeAssistantError(
                "Enable Modules > Windows > Keyboard and Mouse from Home Assistant on the PC",
                translation_domain="what",
                translation_key="enable_modules_windows_keyboard_and_mouse_from_ha_on",
            )
        args = {key: value for key, value in call.data.items() if key != "device_id"}
        try:
            await coordinator.client.command(
                "keyboard" if call.service == "send_keyboard" else "mouse",
                args,
            )
        except (ConnectionError, OSError, TimeoutError, aiohttp.ClientError) as err:
            raise HomeAssistantError(str(err) or "Input request failed; not retried") from err
        return {"submitted": True}
    for service, schema in (("send_keyboard", KEYBOARD_SCHEMA), ("send_mouse", MOUSE_SCHEMA)):
        hass.services.async_register(
            DOMAIN,
            service,
            send,
            schema=schema,
            supports_response=SupportsResponse.OPTIONAL,
        )
