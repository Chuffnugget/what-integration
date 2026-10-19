"""Config flow: pairing (manual or discovered), reconfigure and reauth."""
from __future__ import annotations

import aiohttp
import logging
import re
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import AuthenticationError, WhatClient
from .const import CONF_FINGERPRINT, CONF_TOKEN, DEFAULT_PORT, DOMAIN, PROTOCOL_VERSION
from .protocol import ProtocolError, fingerprint_bytes
from .connection_health import clear_repairs

_LOGGER = logging.getLogger(__name__)


def pairing_schema(defaults, *, api):
    fields = {
        vol.Required("device_id", default=defaults.get("device_id", "")): str,
        vol.Required("name", default=defaults.get("name", "")): str,
        vol.Required(CONF_TOKEN): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
        vol.Required(CONF_FINGERPRINT, default=defaults.get(CONF_FINGERPRINT, "")): str,
    }
    if api:
        fields.update({
            # A cleared optional address must stay empty, even when reconfiguring
            # a local entry. A schema default would silently restore the old host.
            vol.Optional(CONF_HOST, description={"suggested_value": defaults.get(CONF_HOST, "")}): str,
            vol.Optional(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535),
            ),
        })
    return vol.Schema(fields)


class WhatConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self._defaults = {}
        self._discovered_id = None
        self._pairing_source = "user"

    async def async_step_user(self, user_input=None):
        return await self._choose_connection("user", user_input)

    async def async_step_zeroconf(self, discovery_info):
        device_id = discovery_info.properties.get("id")
        if not isinstance(device_id, str) or not device_id or len(device_id) > 128:
            return self.async_abort(reason="invalid_discovery")
        if (discovery_info.properties.get("version") != str(PROTOCOL_VERSION)
                or discovery_info.properties.get("tls") != "1"):
            return self.async_abort(reason="incompatible_discovery")
        self._discovered_id = device_id
        entry = await self.async_set_unique_id(device_id)
        if entry:
            await self._update_discovered_address(entry, discovery_info)
            return self.async_abort(reason="already_configured")
        # Newer apps advertise the PC's host name; older ones only the service name.
        name = discovery_info.properties.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(ord(ch) < 32 for ch in name):
            name = discovery_info.name.removesuffix(f"._{DOMAIN}._tcp.local.")
        self._defaults = {
            CONF_HOST: discovery_info.host, CONF_PORT: discovery_info.port,
            "device_id": device_id, "name": name,
        }
        self.context["title_placeholders"] = {"name": name}
        return await self.async_step_user()

    async def _update_discovered_address(self, entry, discovery_info):
        """Trust a new address only after the existing pin, token and ID verify it."""
        before = dict(entry.data)
        address = {CONF_HOST: discovery_info.host, CONF_PORT: discovery_info.port}
        if entry.options.get("direct_enabled", True) is False or entry.source == config_entries.SOURCE_IGNORE or all(before.get(k) == v for k, v in address.items()):
            return
        client = None
        try:
            client = WhatClient(async_get_clientsession(self.hass), **(before | address), device_id=entry.unique_id)
            await client.connect()
        except (AuthenticationError, ProtocolError, aiohttp.ClientError, OSError, TimeoutError, ValueError):
            _LOGGER.debug("Rediscovered address could not be verified; keeping the paired address")
            return
        finally:
            if client:
                await client.close()
        # Pairing may have changed while the network verification was in progress.
        if dict(entry.data) != before:
            return
        self.hass.config_entries.async_update_entry(entry, data=before | address)
        clear_repairs(self.hass, entry, "local")
        if not entry.update_listeners and entry.state in (
            config_entries.ConfigEntryState.LOADED, config_entries.ConfigEntryState.SETUP_RETRY,
        ):
            self.hass.config_entries.async_schedule_reload(entry.entry_id)

    def _update_pairing(self, entry, data, options, reason):
        changed = self.hass.config_entries.async_update_entry(
            entry, data=dict(entry.data) | data, options=dict(entry.options) | options,
        )
        clear_repairs(self.hass, entry)
        # Loaded entries reload through their update listener. Schedule explicitly
        # for failed entries or unchanged credentials the user wants to retry.
        if not changed or not entry.update_listeners:
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return self.async_abort(reason=reason)

    async def async_step_reconfigure(self, user_input=None):
        self._pairing_source = "reconfigure"
        self._defaults = self._entry_defaults(self._get_reconfigure_entry())
        return await self._choose_connection("reconfigure", user_input)

    async def async_step_reauth(self, entry_data):
        self._pairing_source = "reauth"
        self._defaults = self._entry_defaults(self._get_reauth_entry())
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        return await self._choose_connection("reauth_confirm", user_input)

    @staticmethod
    def _entry_defaults(entry):
        defaults = dict(entry.data)
        direct = entry.options.get("direct_enabled", True)
        mqtt = entry.options.get("preferred_transport") == "mqtt" or (
            not direct and entry.options.get("mqtt_enabled", False)
            and not entry.options.get("remote_enabled", False)
        )
        defaults.update(connection="mqtt" if mqtt else "api", device_id=entry.unique_id, name=entry.title)
        if not direct or defaults.get(CONF_HOST) == "localhost":
            defaults[CONF_HOST] = ""
        return defaults

    async def _choose_connection(self, step, user_input):
        if user_input is not None:
            if user_input["connection"] == "mqtt":
                return await self.async_step_mqtt()
            return await self.async_step_api()
        return self.async_show_form(step_id=step, data_schema=vol.Schema({
            vol.Required("connection", default=self._defaults.get("connection", "api")): SelectSelector(
                SelectSelectorConfig(options=["api", "mqtt"], translation_key="connection"),
            ),
        }))

    async def async_step_api(self, user_input=None):
        return await self._pair("api", user_input)

    async def async_step_mqtt(self, user_input=None):
        return await self._pair("mqtt", user_input)

    async def _pair(self, step, user_input):
        errors = {}
        if step == "mqtt" and "mqtt" not in self.hass.config.components:
            errors["base"] = "mqtt_not_configured"
        if user_input is not None and not errors:
            device_id = user_input["device_id"].strip()
            name = user_input["name"].strip()
            host = user_input.get(CONF_HOST, "").strip() if step == "api" else ""
            # "localhost" is stored internally to mean "no direct address", so a
            # user who really means this machine gets the loopback address instead.
            if host.lower() == "localhost":
                host = "127.0.0.1"
            data = {
                CONF_HOST: host or "localhost",
                CONF_PORT: user_input.get(CONF_PORT, DEFAULT_PORT) if host else DEFAULT_PORT,
                CONF_TOKEN: user_input[CONF_TOKEN].strip(),
                CONF_FINGERPRINT: user_input[CONF_FINGERPRINT],
            }
            expected = self._discovered_id
            if self._pairing_source == "reconfigure":
                expected = self._get_reconfigure_entry().unique_id
            elif self._pairing_source == "reauth":
                expected = self._get_reauth_entry().unique_id
            client = None
            try:
                data[CONF_FINGERPRINT] = fingerprint_bytes(data[CONF_FINGERPRINT]).hex()
                if (not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", device_id)
                        or not re.fullmatch(r"[0-9a-fA-F]{64}", data[CONF_TOKEN])
                        or not name or len(name) > 80 or any(ord(ch) < 32 for ch in name)):
                    raise ValueError("Invalid pairing details")
                if expected and device_id != expected:
                    return self.async_abort(reason="unique_id_mismatch")
                if host:
                    client = WhatClient(async_get_clientsession(self.hass), **data, device_id=device_id)
                    hello, _ = await client.connect()
                    if hello["device_id"] != device_id:
                        raise ProtocolError("Different PC")
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except aiohttp.ServerFingerprintMismatch:
                errors["base"] = "fingerprint_mismatch"
            except ValueError:
                errors["base"] = "invalid_input"
            except ProtocolError:
                errors["base"] = "invalid_protocol"
            except (aiohttp.ClientError, OSError, TimeoutError, ConnectionError):
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error pairing WHAT")
                errors["base"] = "unknown"
            finally:
                if client:
                    await client.close()
            if not errors:
                # With no local address, setup waits for an authenticated incoming
                # API or MQTT session. The copied ID is not proof of connection.
                options = {
                    "direct_enabled": bool(host), "remote_enabled": step == "api",
                    "mqtt_enabled": step == "mqtt",
                    "preferred_transport": "mqtt" if step == "mqtt" else "local",
                }
                await self.async_set_unique_id(device_id)
                if self._pairing_source == "reconfigure":
                    self._abort_if_unique_id_mismatch()
                    return self._update_pairing(self._get_reconfigure_entry(), data, options, "reconfigure_successful")
                if self._pairing_source == "reauth":
                    self._abort_if_unique_id_mismatch()
                    return self._update_pairing(self._get_reauth_entry(), data, options, "reauth_successful")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=name, data=data, options=options)
            # Keep entered address/fingerprint after a failed attempt; never echo the token.
            self._defaults = {k: v for k, v in user_input.items() if k != CONF_TOKEN}
        return self.async_show_form(
            step_id=step, data_schema=pairing_schema(self._defaults, api=step == "api"), errors=errors,
        )
