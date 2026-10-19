"""The send_notification service: Windows toast notifications from Home Assistant."""
from urllib.parse import urlsplit
import aiohttp
import voluptuous as vol
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from .const import DOMAIN
from .service_target import coordinator_for_device


def bounded_text(maximum, single_line=False):
    def validate(value):
        value = cv.string(value)
        if not value.strip() or "\0" in value or single_line and ("\n" in value or "\r" in value):
            raise vol.Invalid("Use non-empty plain text; the title must be on one line")
        try:
            length = len(value.encode("utf-16-le")) // 2
        except UnicodeEncodeError as err:
            raise vol.Invalid("Invalid Unicode text") from err
        if length > maximum:
            raise vol.Invalid(f"Maximum {maximum} Windows text units (emoji can count as two)")
        return value
    return validate


def web_url(value):
    value = bounded_text(4096)(value)
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise vol.Invalid("Use an HTTP or HTTPS URL without embedded credentials")
    return value


ACTION = vol.Schema({
    vol.Required("id"): bounded_text(64, True),
    vol.Required("title"): bounded_text(40, True),
    vol.Optional("url"): web_url,
})


def actions(value):
    result = vol.All([ACTION], vol.Length(max=4))(value)
    ids = [action["id"] for action in result]
    if len(set(ids)) != len(ids) or "OPEN" in ids or any(":" in identifier for identifier in ids):
        raise vol.Invalid("Action IDs must be unique, cannot contain a colon, and cannot be OPEN")
    return result


SCHEMA = vol.Schema({
    vol.Required("device_id"): vol.All(cv.ensure_list, [cv.string], vol.Length(min=1, max=1)),
    vol.Optional("title", default="Home Assistant"): bounded_text(128, True),
    vol.Required("message"): bounded_text(2000),
    vol.Optional("level", default="information"): vol.In(("information", "warning", "error")),
    vol.Optional("notification_id"): bounded_text(64, True),
    vol.Optional("image"): web_url,
    vol.Optional("actions"): actions,
    vol.Optional("reply"): bool,
    vol.Optional("silent"): bool,
    vol.Optional("ttl"): vol.All(int, vol.Range(min=1, max=3600)),
})


def register_notification_service(hass):
    async def send_notification(call):
        coordinator = coordinator_for_device(hass, call.data["device_id"][0])
        if (
            not coordinator.last_update_success
            or not (coordinator.data or {}).get("controls", {}).get("notifications_enabled", False)
        ):
            raise HomeAssistantError(
                "Enable Modules > Windows > Notifications from Home Assistant on the PC",
                translation_domain="what",
                translation_key="enable_modules_windows_notifications_from_ha_on_the_pc",
            )
        args = {key: value for key, value in call.data.items() if key != "device_id"}
        if args.get("reply") and not any("url" not in action for action in args.get("actions", [])):
            raise HomeAssistantError(
                "Add an action without a URL to submit the reply to Home Assistant",
                translation_domain="what",
                translation_key="add_an_action_without_a_url_to_submit_the",
            )
        rich = (set(args) - {"title", "message", "level"}
                or len(args["title"].encode("utf-16-le")) > 126
                or len(args["message"].encode("utf-16-le")) > 510)
        if rich and (coordinator.data or {}).get("notifications", {}).get("version") != 1:
            raise HomeAssistantError(
                "Update the installed WHAT app to use rich notifications",
                translation_domain="what",
                translation_key="update_the_installed_what_app_to_use_rich_notifications",
            )
        try:
            # Image retrieval has a ten-second PC-side limit; leave time for
            # decoding, Windows submission and the transport reply as well.
            await coordinator.client.command("notify", args, timeout=15)
        except (ConnectionError, OSError, TimeoutError, aiohttp.ClientError) as err:
            raise HomeAssistantError(str(err) or "Notification request failed; not retried") from err
        return {
            "submitted": True,
            "message": "Submitted to Windows; visibility depends on Windows notification settings and Do not disturb.",
        }

    hass.services.async_register(
        DOMAIN,
        "send_notification",
        send_notification,
        schema=SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
