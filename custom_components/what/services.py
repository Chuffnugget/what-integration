"""Register the WHAT services. Shell commands are defined here; see the other *_service modules."""
import aiohttp
import voluptuous as vol
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import config_validation as cv
from .const import DOMAIN
from .notification_service import register_notification_service
from .input_service import register_input_services
from .service_target import coordinator_for_device

SCHEMA = vol.Schema({
    vol.Required("device_id"): vol.All(cv.ensure_list, [cv.string], vol.Length(min=1, max=1)),
    vol.Required("shell"): vol.In(("powershell", "pwsh", "cmd", "wsl")),
    vol.Required("command"): vol.All(cv.string, vol.Length(min=1, max=2048)),
    vol.Optional("timeout", default=30): vol.All(vol.Coerce(int), vol.Range(min=1, max=300)),
    vol.Optional("distribution"): vol.All(cv.string, vol.Length(min=1, max=128)),
    vol.Optional("leave_running"): cv.boolean,
})


def register_services(hass):
    register_notification_service(hass)
    register_input_services(hass)
    async def run_command(call):
        # Automated/internal calls have no user; user-originated requests need admin access.
        if call.context.user_id:
            user = await hass.auth.async_get_user(call.context.user_id)
            if user is None or not user.is_admin:
                raise Unauthorized()
        coordinator = coordinator_for_device(hass, call.data["device_id"][0])
        if (
            not coordinator.last_update_success
            or not (coordinator.data or {}).get("controls", {}).get("shell_enabled", False)
        ):
            raise HomeAssistantError(
                "Enable remote shell commands in the PC's Windows module",
                translation_domain="what",
                translation_key="enable_remote_shell_commands_in_the_pc_s_windows",
            )
        args = {key: value for key, value in call.data.items() if key != "device_id"}
        if "distribution" in args and args["shell"] != "wsl":
            raise HomeAssistantError(
                "Distribution applies only to WSL",
                translation_domain="what",
                translation_key="distribution_applies_only_to_wsl",
            )
        try:
            response = await coordinator.client.command("run_command", args, timeout=args["timeout"] + 15)
        except (ConnectionError, OSError, TimeoutError, aiohttp.ClientError) as err:
            raise HomeAssistantError(
                str(err) or "Command connection timed out; outcome unknown. Not retried.",
            ) from err
        result = response.get("result")
        if result is None:
            raise HomeAssistantError(
                "Windows did not return a command result",
                translation_domain="what",
                translation_key="windows_did_not_return_a_command_result",
            )
        return result

    hass.services.async_register(
        DOMAIN,
        "run_command",
        run_command,
        schema=SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
