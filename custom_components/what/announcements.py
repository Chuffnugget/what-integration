"""The Announcements media player and the speak service."""
import aiohttp
import math
import voluptuous as vol
from yarl import URL

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    MediaPlayerEntity, MediaPlayerEntityFeature as Feature, MediaPlayerState,
    async_process_play_media_url,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.network import get_url, is_hass_url

from .const import DOMAIN
from .entity import WhatEntity
from .notification_service import bounded_text
from .service_target import coordinator_for_device


def permitted(coordinator):
    return coordinator.last_update_success and (coordinator.data or {}).get("announcements", {}).get("enabled", False)


async def send(coordinator, action, args):
    if not permitted(coordinator):
        raise HomeAssistantError(
            "Enable Speech and Audio from Home Assistant in WHAT's Windows module",
            translation_domain="what",
            translation_key="enable_speech_and_audio_from_home_assistant_in_what",
        )
    try:
        await coordinator.client.command(action, args, timeout=15)
    except (ConnectionError, OSError, TimeoutError, aiohttp.ClientError) as err:
        raise HomeAssistantError(str(err) or "Announcement failed; it was not retried") from err


def media_url(hass, coordinator, value):
    """Use a reachable HA base URL and HA's own short-lived media authentication."""
    parsed = URL(value)
    if not parsed.is_absolute() or is_hass_url(hass, value):
        remote = getattr(coordinator, "transport", "local") == "remote"
        base = get_url(hass, prefer_external=getattr(coordinator, "transport", "local") != "local",
                       allow_internal=not remote, require_ssl=remote)
        value = str(URL(base).join(URL(parsed.raw_path_qs)))
    value = async_process_play_media_url(hass, value)
    parsed = URL(value)
    if parsed.scheme not in ("http", "https") or not parsed.host or parsed.user or parsed.password:
        raise HomeAssistantError(
            "Audio requires an HTTP or HTTPS URL reachable from the PC",
            translation_domain="what",
            translation_key="audio_requires_an_http_or_https_url_reachable_from",
        )
    return value


class AnnouncementPlayer(WhatEntity, MediaPlayerEntity):
    _attr_icon = "mdi:bullhorn"
    _attr_supported_features = (Feature.PLAY_MEDIA | Feature.PLAY | Feature.PAUSE | Feature.STOP
                                | Feature.VOLUME_SET | Feature.VOLUME_MUTE)

    def __init__(self, coordinator):
        super().__init__(coordinator, "windows_announcements", "Announcements")

    @property
    def playback(self):
        return (self.coordinator.data or {}).get("announcements", {})

    @property
    def available(self):
        return permitted(self.coordinator)

    @property
    def state(self):
        return {"playing": MediaPlayerState.PLAYING, "paused": MediaPlayerState.PAUSED,
                "buffering": MediaPlayerState.BUFFERING}.get(self.playback.get("state"), MediaPlayerState.IDLE)

    @property
    def volume_level(self):
        return self.playback.get("volume")

    @property
    def is_volume_muted(self):
        return self.playback.get("muted")

    @property
    def extra_state_attributes(self):
        return {"last_error": self.playback.get("error", "")}

    async def async_play_media(self, media_type, media_id, **kwargs):
        if media_source.is_media_source_id(media_id):
            resolved = await media_source.async_resolve_media(self.hass, media_id, self.entity_id)
            media_id = resolved.url
        if kwargs.get("enqueue") not in (None, False, "play", "replace"):
            raise HomeAssistantError(
                "WHAT replaces the current announcement; queued playback is not supported",
                translation_domain="what",
                translation_key="what_replaces_the_current_announcement_queued_playback_is_not",
            )
        await send(self.coordinator, "play_audio", {"url": media_url(self.hass, self.coordinator, media_id)})

    async def async_media_play(self):
        await send(self.coordinator, "audio_control", {"operation": "play"})

    async def async_media_pause(self):
        await send(self.coordinator, "audio_control", {"operation": "pause"})

    async def async_media_stop(self):
        await send(self.coordinator, "audio_control", {"operation": "stop"})

    async def async_set_volume_level(self, volume):
        await send(self.coordinator, "audio_control", {"operation": "volume", "value": volume})

    async def async_mute_volume(self, mute):
        await send(self.coordinator, "audio_control", {"operation": "mute", "value": mute})


def finite_number(minimum, maximum):
    def validate(value):
        if type(value) is bool:
            raise vol.Invalid("Expected a number")
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as err:
            raise vol.Invalid("Expected a number") from err
        if not math.isfinite(number) or not minimum <= number <= maximum:
            raise vol.Invalid(f"Expected a finite number from {minimum} to {maximum}")
        return number
    return validate


SPEECH_SCHEMA = vol.Schema({
    vol.Required("device_id"): vol.All(cv.ensure_list, [cv.string], vol.Length(min=1, max=1)),
    vol.Required("message"): bounded_text(4000),
    vol.Optional("voice"): bounded_text(1024, True),
    vol.Optional("rate"): finite_number(0.5, 2),
    vol.Optional("volume"): finite_number(0, 1),
})


def register_speech_service(hass):
    async def speak(call):
        await send(coordinator_for_device(hass, call.data["device_id"][0]), "speak", {key: value for key, value in call.data.items() if key != "device_id"})

    hass.services.async_register(DOMAIN, "speak", speak, schema=SPEECH_SCHEMA)
