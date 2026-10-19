"""Coordinator for one PC: receives the data stream, switches routes and reconnects."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AuthenticationError, WhatClient
from .const import DOMAIN
from .protocol import ProtocolError, StreamFrame
from .connection_health import ConnectionHealth

_LOGGER = logging.getLogger(__name__)
TRANSPORT_ERRORS = (aiohttp.ClientError, OSError, TimeoutError, ConnectionError, ProtocolError)


class WhatCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry)
        self.entry = entry
        self._applied_options = dict(entry.options)
        self._applied_entry_data = dict(entry.data)
        self._connection_revision = ""
        self.health = ConnectionHealth(hass, entry)
        self.client = self._new_client()
        self.hello: dict = {}
        self.readings: dict[str, dict] = {}
        from .entity_maintenance import EntityMaintenance
        self.entity_maintenance = EntityMaintenance(self)
        self._task: asyncio.Task | None = None
        self._remote_queue = asyncio.Queue(maxsize=1)
        self._remote_done = None
        self._stopping = False
        self.transport = "local"
        self._remote_offering = False
        self._probe_task = None
        self._mqtt_task = None
        self._mqtt_preferred = entry.options.get("preferred_transport") == "mqtt"
        self._reauth_required = False
        self._stream_ended = False
        self.connection_started = None
        self.connected_since = None
        self.last_seen = None
        self.reading_counts = {"published_entities": 0, "unavailable_readings": 0, "beacn_unavailable_controls": 0}
        self._device_version = None
        self._last_debug_summary = 0
        self.parent_device_id = None
        # Per-frame change hints read by entities to skip unchanged state writes.
        self.changed_modules: set[str] = set()
        self.changed_ids: set[str] = set()
        self.added_ids: set[str] = set()
        self.force_entity_update = True
        self.installation_connections: dict[str, dict] = {}
        self.media_updated_at = None
        self.last_snapshot_received = None
        # None until the first frame sets a baseline; see _accept_notification_events.
        self._notification_events: dict[str, float] | None = None

    @callback
    def async_update_listeners(self) -> None:
        """Notify every entity, even if one of them raises."""
        for update_callback, _ in list(getattr(self, "_listeners", {}).values()):
            try:
                update_callback()
            except Exception:
                _LOGGER.exception("WHAT %s: entity update failed", self.entry.unique_id)

    def async_set_update_error(self, err):
        self.connection_started = None
        self.connected_since = None
        super().async_set_update_error(err)

    def _accept_notification_events(self, data):
        events = data.get("notifications", {}).get("events", [])
        seen = self._notification_events
        received_at = time.monotonic()
        if seen is None:
            # Loading/reloading HA establishes a baseline, so stored action frames
            # cannot replay an automation. Transport handovers keep this same cache.
            self._notification_events = {event["id"]: received_at for event in events}
            return
        # The event and frame timestamps use the same PC clock. Comparing
        # them with HA's clock silently drops valid clicks when clocks differ.
        now = data.get("sent_at", time.time())
        for event in events:
            if event["id"] in seen:
                continue
            seen[event["id"]] = received_at
            if (not data.get("controls", {}).get("notifications_enabled", False)
                    or not now - 60 <= event["created"] <= now + 5):
                continue
            self.hass.bus.async_fire("what_notification_action", {
                "device_id": self.parent_device_id,
                "pc_id": self.entry.unique_id,
                "event_id": event["id"],
                "notification_id": event["notification_id"],
                "action": event["action"],
                "reply": event["reply"],
            })
            _LOGGER.debug("WHAT %s: received a notification action", self.entry.unique_id)
        self._notification_events = dict(list(
            (key, received) for key, received in seen.items() if received >= received_at - 120
        )[-256:])

    def _accept(self, data: dict) -> None:
        if not self.last_update_success or self.health.active_route != self.transport:
            self.health.recovered(self.transport)
        self._accept_notification_events(data)
        if self.connection_started is None:
            self.connection_started = time.monotonic()
            self.connected_since = datetime.now(timezone.utc)
        old = self.data or {}
        self.changed_modules = {
            key for key in old.keys() | data.keys()
            if key != "sensors" and old.get(key) != data.get(key)
        }
        previous = (self.data or {}).get("controls", {}).get("values", {})
        current = data.get("controls", {}).get("values", {})
        if (
            current.get("win_media_position") != previous.get("win_media_position")
            or current.get("win_media_state") != previous.get("win_media_state")
        ):
            self.media_updated_at = datetime.now(timezone.utc)
        readings = data.readings if isinstance(data, StreamFrame) else {row["id"]: row for row in data["sensors"]}
        if (
            isinstance(data, StreamFrame)
            and data.baseline is self.readings
            and data.changed_ids is not None
        ):
            self.changed_ids = data.changed_ids
        else:
            # A new transport/full snapshot must compare against the current HA state.
            self.changed_ids = {
                key for key in readings.keys() | self.readings.keys()
                if readings.get(key) != self.readings.get(key)
            }
        self.added_ids = readings.keys() - self.readings.keys()
        self.force_entity_update = not self.last_update_success
        self.readings = readings
        self.installation_connections = {row["id"]: row for row in data.get("connections", [])}
        self.reading_counts = {
            "published_entities": len(readings),
            "unavailable_readings": sum(not row["available"] or row["value"] is None for row in readings.values()),
            "beacn_unavailable_controls": sum(not control.get("available", False)
                                             for control in data.get("beacn", {}).get("controls", [])),
        }
        self.last_snapshot_received = time.monotonic()
        self.last_seen = datetime.now(timezone.utc)
        self._update_device_version()
        self.async_set_updated_data(data)
        try:
            self.entity_maintenance.receive(data.get("entity_cleanup"))
        except Exception:
            _LOGGER.exception("WHAT %s: could not handle an entity cleanup request", self.entry.unique_id)
        self._sync_connection_options(data)
        if _LOGGER.isEnabledFor(logging.DEBUG) and self.last_snapshot_received - self._last_debug_summary >= 60:
            self._last_debug_summary = self.last_snapshot_received
            _LOGGER.debug("WHAT %s: %s stream healthy, %d readings, %d unavailable, %d changed",
                          self.entry.unique_id, self.transport, len(readings),
                          self.reading_counts["unavailable_readings"], len(self.changed_ids))

    def _update_device_version(self):
        """Refresh existing PC/module devices when an upgraded app reconnects."""
        version = self.hello.get("app_version")
        if not getattr(self, "parent_device_id", None) or not version or version == self._device_version:
            return
        from homeassistant.helpers import device_registry as dr
        registry = dr.async_get(self.hass)
        pc = self.entry.unique_id
        for device in dr.async_entries_for_config_entry(registry, self.entry.entry_id):
            if any(domain == DOMAIN and (identifier == pc or identifier.startswith(pc + "_module_"))
                   for domain, identifier in device.identifiers):
                if device.sw_version != version:
                    registry.async_update_device(device.id, sw_version=version)
        self._device_version = version

    def _sync_connection_options(self, data):
        config = data.get("connection_config")
        if config is not None:
            self._sync_transport_configuration(config)
            return
        choices = data.get("connection_options")
        if choices is None:
            return
        options = dict(self.entry.options)
        for key, enabled in choices.items():
            # Enabling another route requires the certificate-pinned local pairing.
            # An authenticated remote route can still revoke its existing permission.
            if (self.transport == "local" or not enabled) and options.get(key, False) != enabled:
                options[key] = enabled
        if options != self.entry.options:
            self.hass.config_entries.async_update_entry(self.entry, options=options)

    def _sync_transport_configuration(self, config):
        """Apply PC-selected routes over the existing authenticated pairing, without a reload."""
        # A manual re-pair or credential change must still reload the entry.
        # Do not let an in-flight frame overwrite it or suppress its listener.
        if not self.configuration_is_applied():
            return
        changed = self._connection_revision != config["revision"]
        self._connection_revision = config["revision"]
        options = dict(self.entry.options)
        options.update(remote_enabled=config["remote_enabled"], mqtt_enabled=config["mqtt_enabled"],
                       preferred_transport=config["preferred"])
        data = dict(self.entry.data)
        # Preserve a manually supplied direct address. MQTT-only pairing can learn
        # the PC's LAN endpoint and pinned certificate from its authenticated stream.
        if data.get("host") == "localhost" and config["direct_host"]:
            data.update(host=config["direct_host"], port=config["direct_port"], fingerprint=config["fingerprint"])
        options["direct_enabled"] = data.get("host") not in (None, "", "localhost")
        self._mqtt_preferred = config["preferred"] == "mqtt"
        self._applied_options = options
        self._applied_entry_data = data
        if options != self.entry.options or data != self.entry.data:
            self.hass.config_entries.async_update_entry(self.entry, options=options, data=data)
        if self._task is not None:
            self._start_route_watchers(retry_mqtt=changed)

    def configuration_is_applied(self):
        return (self.entry.options == self._applied_options
                and self.entry.data == self._applied_entry_data)

    def _start_route_watchers(self, retry_mqtt=False):
        if self.entry.options.get("direct_enabled", True) and (self._probe_task is None or self._probe_task.done()):
            self._probe_task = self.hass.async_create_background_task(self._probe_local(), "WHAT local preference")
        if self.entry.options.get("mqtt_enabled", False) and (self._mqtt_task is None or (retry_mqtt and self._mqtt_task.done())):
            from .mqtt_transport import watch_mqtt
            self._mqtt_task = self.hass.async_create_background_task(watch_mqtt(self), "WHAT MQTT handover")
            self._mqtt_task.add_done_callback(self._mqtt_watcher_finished)
        elif not self.entry.options.get("mqtt_enabled", False) and self._mqtt_task:
            self._mqtt_task.cancel()

    def _mqtt_watcher_finished(self, task):
        # A quick disable/enable can happen while cancellation is still closing
        # the subscription. Failed watchers retry only after a configuration edit.
        if task.cancelled() and self._mqtt_task is task and not self._stopping:
            self._mqtt_task = None
            self._start_route_watchers()

    async def async_command(self, action: str, permission: str, value=None) -> None:
        if (
            not self.last_update_success
            or not (self.data or {}).get("controls", {}).get("permissions", {}).get(permission)
        ):
            raise HomeAssistantError(
                "Enable this remote control on the PC's Modules page first",
                translation_domain="what",
                translation_key="enable_this_remote_control_on_the_pc_s_modules",
            )
        try:
            await self.client.command(action, {} if value is None else {"value": value})
        except (ConnectionError, TimeoutError, aiohttp.ClientError) as err:
            raise HomeAssistantError(
                str(err) or "Command timed out; outcome is unknown. It was not retried.",
            ) from err

    async def async_app_command(self, action: str, value=None) -> None:
        if (not self.last_update_success or not self.client.commands
                or not (self.data or {}).get("app", {}).get("control_enabled", False)):
            raise HomeAssistantError(
                "Allow Home Assistant to Manage WHAT in the PC app's Pairing and Security settings",
                translation_domain="what",
                translation_key="allow_home_assistant_to_manage_what_in_the_pc",
            )
        if action not in {"app_logging", "app_publish_interval", "app_restart"}:
            raise HomeAssistantError(
                "Unknown WHAT app command",
                translation_domain="what",
                translation_key="unknown_what_app_command",
            )
        try:
            await self.client.command(action, {} if value is None else {"value": value})
        except (ConnectionError, TimeoutError, aiohttp.ClientError) as err:
            raise HomeAssistantError(
                str(err) or "Command timed out; outcome is unknown. It was not retried.",
            ) from err

    def _wait_for_authenticated_connection(self):
        self.hello = {"name": self.entry.title, "app_version": "unknown", "device_id": self.entry.unique_id}
        self.data = {"sensors": [], "controls": {"permissions": {}, "values": {}},
                     "device_id": self.entry.unique_id}
        self.async_set_update_error(UpdateFailed("Waiting for an authenticated WHAT connection"))

    async def async_connect(self) -> None:
        if self.entry.options.get("direct_enabled", True) is False or self._mqtt_preferred:
            self._wait_for_authenticated_connection()
            return
        _LOGGER.debug("WHAT %s: opening paired direct connection", self.entry.unique_id)
        try:
            self.hello, data = await self.client.connect()
            data = await self.client.activate_transport(data)
        except AuthenticationError as err:
            self.health.failed("local", err)
            raise ConfigEntryAuthFailed(
                "Update the WHAT pairing token",
                translation_domain="what",
                translation_key="update_the_what_pairing_token",
            ) from err
        except TRANSPORT_ERRORS as err:
            self.health.failed("local", err)
            if (
                not self.entry.options.get("remote_enabled", False)
                and not self.entry.options.get("mqtt_enabled", False)
            ):
                raise ConfigEntryNotReady(
                    "Cannot connect to the paired PC; check app, address and fingerprint",
                    translation_domain="what",
                    translation_key="cannot_connect_to_the_paired_pc_check_app_address",
                ) from err
            self._wait_for_authenticated_connection()
            return
        self._accept(data)

    def start(self) -> None:
        self._task = self.hass.async_create_background_task(self._run(), "WHAT stream")
        self._start_route_watchers()

    def _new_client(self):
        client = WhatClient(
            async_get_clientsession(self.hass),
            **self.entry.data,
            device_id=self.entry.unique_id,
        )
        client.entity_resolver = self._entity_ids
        return client

    def _entity_ids(self, keys):
        """Return current registry IDs, including names changed by the user."""
        if not keys:
            return []
        from homeassistant.helpers import entity_registry as er

        registry = er.async_get(self.hass)
        found = set()
        for key in keys:
            unique_id = f"{self.entry.unique_id}_{key}"
            for domain in ("sensor", "binary_sensor", "number", "select", "switch",
                           "button", "light", "text", "media_player", "update"):
                entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
                if entity_id:
                    entity = registry.async_get(entity_id)
                    if entity and entity.config_entry_id == self.entry.entry_id:
                        found.add(entity_id)
        return sorted(found)

    async def offer_remote(self, ws):
        """Keep the offered socket alive until the stream loop releases it."""
        if (
            self._stopping
            or self._reauth_required
            or self._stream_ended
            or self._remote_offering
            or (self._remote_done is not None and (self.transport != "mqtt" or self._mqtt_preferred))
            or self._remote_queue.full()
        ):
            await ws.close(code=1008, message=b"Connection already active")
            return
        self._remote_offering = True
        remote = self._new_client()
        done = asyncio.Event()
        try:
            async with asyncio.timeout(15):
                hello, data = await remote.attach(ws)
            if self._stopping or self._reauth_required or self._stream_ended:
                return
            self._remote_queue.put_nowait((remote, hello, data, done))
            await done.wait()
        except (AuthenticationError, *TRANSPORT_ERRORS) as err:
            self.health.failed("remote", err)
            raise
        finally:
            self._remote_offering = False
            await remote.close()

    async def _drop_current(self):
        """Close the active client, then release its waiting remote handler."""
        await self.client.close()
        if self._remote_done:
            self._remote_done.set()
            self._remote_done = None

    async def _finish_pending_commands(self):
        """Give existing replies a short grace period before a planned handover."""
        pending = [future for future in getattr(self.client, "pending", {}).values() if not future.done()]
        if not pending:
            return

        async def receive():
            while True:
                self._accept(await self.client.receive_snapshot())

        reader = asyncio.create_task(receive())
        # Only replies are drained. Replaying an unanswered command on the next
        # route could repeat an action that the PC has already performed.
        replies = asyncio.gather(*(asyncio.shield(future) for future in pending), return_exceptions=True)
        try:
            await asyncio.wait((reader, replies), timeout=3, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (reader, replies):
                if not task.done():
                    task.cancel()
            await asyncio.gather(reader, replies, return_exceptions=True)
        remaining = sum(not future.done() for future in pending)
        if remaining:
            _LOGGER.debug("WHAT %s: handover grace period ended with %d command replies outstanding; not retried",
                          self.entry.unique_id, remaining)

    async def _connect_or_offer(self):
        """A slow direct connection must not hold up a prepared alternate route."""
        if self.entry.options.get("direct_enabled", True) is False or self._mqtt_preferred:
            return await self._remote_queue.get()
        candidate = self._new_client()
        connecting = asyncio.create_task(candidate.connect())
        offered = asyncio.create_task(self._remote_queue.get())
        replacement = None
        try:
            await asyncio.wait((connecting, offered), return_when=asyncio.FIRST_COMPLETED)
            if connecting.done() and isinstance(connecting.exception(), AuthenticationError):
                connecting.result()
            if not offered.done():
                offered.cancel()
                await asyncio.gather(offered, return_exceptions=True)
            if not offered.cancelled():
                replacement = offered.result()
            else:
                hello, data = connecting.result()
                replacement = (candidate, hello, data, None)
            for task in (connecting, offered):
                if not task.done():
                    task.cancel()
            await asyncio.gather(connecting, offered, return_exceptions=True)
            if replacement[0] is not candidate:
                await candidate.close()
            return replacement
        except BaseException:
            for task in (connecting, offered):
                if not task.done():
                    task.cancel()
            await asyncio.gather(connecting, offered, return_exceptions=True)
            if not offered.cancelled() and offered.exception() is None:
                client, _, _, finished = offered.result()
                if finished:
                    finished.set()
                await client.close()
            await candidate.close()
            raise

    def _log_connection(self, previous):
        _LOGGER.debug("WHAT %s: active route %s -> %s, app %s, %d readings",
                      self.entry.unique_id, previous, self.transport,
                      self.hello.get("app_version", "unknown"), len(self.readings))

    async def _run(self):
        delay = 1
        try:
            while True:
                offered = asyncio.create_task(self._remote_queue.get())
                reading = asyncio.create_task(self.client.receive_snapshot()) if self.client.ws is not None else None
                offer_transferred = False
                candidate = None
                candidate_done = None
                try:
                    done, _ = await asyncio.wait(
                        [offered] + ([reading] if reading else []),
                        timeout=None if reading else delay,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    # Stop the queue consumer before any socket-close await. An
                    # offer arriving during that await must stay in the queue.
                    if offered not in done:
                        offered.cancel()
                        await asyncio.gather(offered, return_exceptions=True)
                    if not offered.cancelled():
                        remote, hello, data, finished = offered.result()
                        if reading:
                            reading.cancel()
                            await asyncio.gather(reading, return_exceptions=True)
                        await self._finish_pending_commands()
                        if remote.ws is None or getattr(remote.ws, "closed", False):
                            _LOGGER.debug("WHAT %s: replacement closed before handover; keeping current route",
                                          self.entry.unique_id)
                            continue
                        try:
                            if hasattr(remote, "activate_transport"):
                                data = await remote.activate_transport(data)
                        except TRANSPORT_ERRORS as err:
                            _LOGGER.debug("WHAT %s: replacement activation failed (%s); keeping current route",
                                          self.entry.unique_id, type(err).__name__)
                            continue
                        await self._drop_current()
                        self.client, self.hello, self._remote_done = remote, hello, finished
                        offer_transferred = True
                        previous = self.transport
                        self.transport = getattr(remote, "transport", "remote" if finished else "local")
                        self._accept(data)
                        self._log_connection(previous)
                        delay = 1
                        continue
                    if reading and reading in done:
                        try:
                            self._accept(reading.result())
                            delay = 1
                            continue
                        except TRANSPORT_ERRORS as err:
                            self.health.failed(self.transport, err)
                            _LOGGER.debug("WHAT %s: %s stream disconnected (%s)",
                                          self.entry.unique_id, self.transport,
                                          str(err) if isinstance(err, ProtocolError) else type(err).__name__)
                            await self._drop_current()
                            self.async_set_update_error(
                                UpdateFailed("PC disconnected; waiting for reconnect"),
                            )
                    # Probe local only after idle timeout/disconnection. Never retry a command.
                    if not reading or reading not in done:
                        try:
                            candidate, hello, data, candidate_done = await self._connect_or_offer()
                            if hasattr(candidate, "activate_transport"):
                                data = await candidate.activate_transport(data)
                        except AuthenticationError as err:
                            self.health.failed("local", err)
                            self._reauth_required = True
                            self.async_set_update_error(
                                UpdateFailed(
                                    "Pairing token rejected; update the WHAT pairing token",
                                ),
                            )
                            self.entry.async_start_reauth(self.hass)
                            return
                        except TRANSPORT_ERRORS as err:
                            self.health.failed("local", err)
                            delay = min(delay * 2, 30)
                            _LOGGER.debug("WHAT %s: direct reconnect failed (%s), retry in %ss; alternate routes remain open",
                                          self.entry.unique_id, type(err).__name__, delay)
                        else:
                            if reading:
                                reading.cancel()
                                await asyncio.gather(reading, return_exceptions=True)
                            await self._drop_current()
                            self.client, self.hello, self._remote_done = candidate, hello, candidate_done
                            previous = self.transport
                            self.transport = getattr(candidate, "transport", "remote" if candidate_done else "local")
                            self._accept(data)
                            self._log_connection(previous)
                            delay = 1
                except Exception:
                    # A bug while handling one frame should cost a reconnect, not
                    # leave the PC offline until the integration is reloaded.
                    _LOGGER.exception("WHAT %s: error while handling the PC stream; reconnecting",
                                      self.entry.unique_id)
                    await self._drop_current()
                    self.async_set_update_error(UpdateFailed("PC stream error; reconnecting"))
                    delay = min(delay * 2, 30)
                finally:
                    for task in (offered, reading):
                        if task and not task.done():
                            task.cancel()
                    await asyncio.gather(
                        *[task for task in (offered, reading) if task],
                        return_exceptions=True,
                    )
                    # A dequeued offer is ours until installed as self.client.
                    # Cancellation during _drop_current must release it too.
                    if (
                        not offer_transferred
                        and not offered.cancelled()
                        and offered.exception() is None
                    ):
                        remote, _, _, finished = offered.result()
                        if finished:
                            finished.set()
                        await remote.close()
                    if candidate is not None and candidate is not self.client:
                        if candidate_done:
                            candidate_done.set()
                        await candidate.close()
        except Exception:
            _LOGGER.exception("WHAT stream stopped unexpectedly")
            self.async_set_update_error(
                UpdateFailed("PC stream failed; reload the WHAT integration"),
            )
        finally:
            self._stream_ended = True
            # Never leave cached readings available after the receiving task ends.
            if self.last_update_success:
                if self._stopping:
                    self.last_update_success = False
                else:
                    self.async_set_update_error(UpdateFailed("PC stream stopped"))
            await self._drop_current()
            await self._drain_offers()

    async def _probe_local(self):
        while True:
            await asyncio.sleep(30)
            if self._reauth_required or (self._task is not None and self._task.done()):
                return
            if (
                self._mqtt_preferred
                or not self._remote_done
                or self._stopping
                or self._remote_queue.full()
            ):
                continue
            candidate = self._new_client()
            try:
                hello, data = await candidate.connect()
                if (
                    self._stopping
                    or self._stream_ended
                    or self._reauth_required
                    or self._remote_queue.full()
                ):
                    await candidate.close()
                else:
                    self._remote_queue.put_nowait((candidate, hello, data, None))
            except asyncio.CancelledError:
                await candidate.close()
                raise
            except AuthenticationError as err:
                self.health.failed("local", err)
                await candidate.close()
                # Preserve a working remote stream. Once it disconnects, the
                # main reconnect loop will request reauth if rejection persists.
                return
            except TRANSPORT_ERRORS as err:
                self.health.failed("local", err)
                await candidate.close()

    async def async_stop(self):
        """Stop route producers before closing clients and draining queued offers."""
        _LOGGER.debug("WHAT %s: stopping transport tasks", self.entry.unique_id)
        self.entity_maintenance.stop()
        self._stopping = True
        if self._mqtt_task:
            self._mqtt_task.cancel()
            await asyncio.gather(self._mqtt_task, return_exceptions=True)
            self._mqtt_task = None
        if self._probe_task:
            self._probe_task.cancel()
            await asyncio.gather(self._probe_task, return_exceptions=True)
            self._probe_task = None
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        await self._drop_current()
        await self._drain_offers()

    async def _drain_offers(self):
        while not self._remote_queue.empty():
            client, _, _, done = self._remote_queue.get_nowait()
            if done:
                done.set()
            await client.close()
