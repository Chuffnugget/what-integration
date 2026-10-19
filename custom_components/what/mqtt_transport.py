"""Authenticated MQTT message stream feeding the existing WHAT entities.

No discovery entities, retained commands, persisted queues, or command retries.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import inspect
import logging
import math
import time
import uuid
from types import SimpleNamespace
import aiohttp
from .protocol import MAX_MESSAGE, ProtocolError, decode_json
from .api import AuthenticationError, DecodedMessage

_LOGGER = logging.getLogger(__name__)

MAX_PACKET = 12 * 1024 * 1024


def encode(token, direction, epoch, session, seq, message):
    body = base64.b64encode(json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode()).decode()
    content = f"{direction}\n{epoch}\n{session}\n{seq}\n{body}".encode()
    return json.dumps(
        {
            "epoch": epoch,
            "session": session,
            "seq": seq,
            "body": body,
            "mac": hmac.new(token.encode(), content, hashlib.sha256).hexdigest(),
        },
        separators=(",", ":"),
    )


def decode(token, direction, payload):
    try:
        if len(payload) > MAX_PACKET:
            raise ValueError()
        obj = decode_json(payload, MAX_PACKET)
        if not isinstance(obj, dict) or set(obj) != {"epoch", "session", "seq", "body", "mac"}:
            raise ValueError()
        if any(not isinstance(obj[k], str) for k in ("epoch", "session", "body", "mac")):
            raise ValueError()
        if (
            len(obj["epoch"]) > 64
            or len(obj["session"]) > 64
            or type(obj["seq"]) is not int
            or not 0 <= obj["seq"] <= 9007199254740991
        ):
            raise ValueError()
        content = f"{direction}\n{obj['epoch']}\n{obj['session']}\n{obj['seq']}\n{obj['body']}".encode()
        expected = hmac.new(token.encode(), content, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(obj["mac"], expected):
            raise ValueError()
        message = decode_json(base64.b64decode(obj["body"], validate=True), MAX_MESSAGE)
        if not isinstance(message, dict):
            raise ValueError()
        return obj, message
    except (ValueError, TypeError, KeyError, UnicodeError, ProtocolError) as err:
        raise ProtocolError("Invalid authenticated MQTT frame") from err


class MqttSocket:
    def __init__(self, hass, device, token, epoch):
        self.hass, self.token, self.epoch = hass, token, epoch
        self.base = f"what/{device}"
        self.session = str(uuid.uuid4())
        self.sent = self.received = 0
        self.closed = False
        self.close_code = None
        self.queue = asyncio.Queue(maxsize=32)
        self.unsubscribe = None
        self.ping_task = None
        self.device = device
        self._last_rejection_log = 0

    async def start(self):
        from homeassistant.components import mqtt
        from homeassistant.core import callback

        @callback
        def received(message):
            if self.closed or message.retain:
                return
            try:
                obj, body = decode(self.token, "pc", message.payload)
                if (
                    obj["epoch"] != self.epoch
                    or obj["session"] != self.session
                    or obj["seq"] <= self.received
                ):
                    return
                if obj["seq"] != self.received + 1:
                    _LOGGER.debug("WHAT %s: MQTT sequence gap; replacing the session", self.device)
                    self._end()
                    return
                self.received = obj["seq"]
                if body.get("type") == "transport_close":
                    self._end()
                    return
                self.queue.put_nowait(DecodedMessage(body))
            except asyncio.QueueFull:
                _LOGGER.debug("WHAT %s: MQTT receive queue full; replacing the session", self.device)
                self._end()
            except ProtocolError:
                now = time.monotonic()
                if now - self._last_rejection_log >= 30:
                    self._last_rejection_log = now
                    _LOGGER.debug("WHAT %s: discarded invalid authenticated MQTT frame", self.device)
                return

        self.unsubscribe = await mqtt.async_subscribe(
            self.hass,
            self.base + "/out/" + self.session,
            received,
            qos=0,
            encoding=None,
        )
        ready = asyncio.Event()
        stop_monitor = mqtt.async_on_subscribe_done(
            self.hass,
            self.base + "/out/" + self.session,
            qos=0,
            on_subscribe_status=ready.set,
        )
        if inspect.isawaitable(stop_monitor):
            stop_monitor = await stop_monitor
        try:
            async with asyncio.timeout(15):
                await ready.wait()
        finally:
            stop_monitor()
        self.ping_task = asyncio.create_task(self._ping())

    async def _ping(self):
        while not self.closed:
            await asyncio.sleep(15)
            try:
                await self.send_json({"type": "ping"})
            except Exception as err:
                _LOGGER.debug("WHAT %s: MQTT keepalive failed (%s)", self.device, type(err).__name__)
                self._end()

    async def send_json(self, message):
        from homeassistant.components import mqtt
        if self.closed:
            raise ConnectionError("MQTT session closed")
        message = dict(message)
        message.pop("token", None)  # Pairing secret never leaves the host via the broker.
        self.sent += 1
        async with asyncio.timeout(10):
            await mqtt.async_publish(
                self.hass,
                self.base + "/in",
                encode(self.token, "ha", self.epoch, self.session, self.sent, message),
                qos=0,
                retain=False,
            )

    async def receive(self):
        if self.closed:
            return SimpleNamespace(type=aiohttp.WSMsgType.CLOSED, data=None)
        return await self.queue.get()

    def exception(self):
        return None

    def _end(self):
        self.closed = True
        while not self.queue.empty():
            self.queue.get_nowait()
        self.queue.put_nowait(SimpleNamespace(type=aiohttp.WSMsgType.CLOSED, data=None))

    async def close(self, *, notify=True):
        if notify and not self.closed:
            try:
                async with asyncio.timeout(2):
                    await self.send_json({"type": "transport_close"})
            except Exception:
                pass
        self._end()
        if self.unsubscribe:
            self.unsubscribe()
            self.unsubscribe = None
        if self.ping_task:
            self.ping_task.cancel()
            await asyncio.gather(self.ping_task, return_exceptions=True)
            self.ping_task = None


async def probe_preference(hass, device, token, epoch):
    """Confirm a live PC's preference using a fresh authenticated session nonce."""
    probe = MqttSocket(hass, device, token, epoch)
    try:
        async with asyncio.timeout(15):
            await probe.start()
            await probe.send_json({"type": "discovery_probe"})
            message = await probe.receive()
        if (not isinstance(message, DecodedMessage)
                or message.data.get("type") != "discovery_reply"
                or type(message.data.get("preferred")) is not bool):
            raise ProtocolError("Invalid MQTT discovery reply")
        return message.data["preferred"]
    finally:
        await probe.close(notify=False)


async def watch_mqtt(coordinator):
    """Prepare the replacement before offering it to the existing stream owner."""
    from homeassistant.components import mqtt
    from homeassistant.core import callback
    if not hasattr(mqtt, "async_on_subscribe_done"):
        _LOGGER.error(
            "WHAT MQTT subscription API is unavailable; use a supported Home Assistant version (2026.9 or later)",
        )
        return
    if not await mqtt.async_wait_for_mqtt_client(coordinator.hass):
        _LOGGER.warning(
            "WHAT cannot start its MQTT connection: configure or repair Home Assistant's "
            "MQTT integration, then reload this WHAT entry",
        )
        return
    queue = asyncio.Queue(maxsize=1)
    @callback
    def announce(message):
        if message.retain:
            return
        try:
            obj, body = decode(coordinator.client.token, "pc", message.payload)
            if obj["session"] or obj["seq"] != 0 or type(body.get("preferred")) is not bool:
                return
            if (
                type(body.get("time")) not in (int, float)
                or not math.isfinite(body["time"])
                or (body.get("discovery_probe_v1") is not True and abs(time.time() - body["time"]) > 30)
            ):
                return
            if not queue.empty():
                queue.get_nowait()
            queue.put_nowait((obj["epoch"], body["preferred"], body.get("discovery_probe_v1") is True))
        except (ProtocolError, ValueError):
            return
    unsubscribe = await mqtt.async_subscribe(
        coordinator.hass,
        f"what/{coordinator.entry.unique_id}/announce",
        announce,
        qos=0,
        encoding=None,
    )
    candidate = None
    last_probe = float("-inf")
    try:
        while not coordinator._stopping:
            epoch, preferred, needs_probe = await queue.get()
            active = getattr(coordinator.client, "transport", None) == "mqtt"
            if (
                coordinator._reauth_required
                or coordinator._stream_ended
                or coordinator._remote_queue.full()
            ):
                continue
            if preferred and active and coordinator.client.ws and coordinator.client.ws.epoch == epoch:
                continue
            if not preferred and not active:
                continue
            if needs_probe:
                if time.monotonic() - last_probe < 2:
                    continue
                last_probe = time.monotonic()
                try:
                    preferred = await probe_preference(coordinator.hass, coordinator.entry.unique_id,
                                                       coordinator.client.token, epoch)
                except (ConnectionError, OSError, TimeoutError, ProtocolError, aiohttp.ClientError) as err:
                    _LOGGER.debug("WHAT %s: MQTT discovery probe failed (%s); keeping current route",
                                  coordinator.entry.unique_id, type(err).__name__)
                    continue
                if coordinator._stopping or coordinator._stream_ended or coordinator._reauth_required:
                    continue
                active = getattr(coordinator.client, "transport", None) == "mqtt"
                if preferred and active and coordinator.client.ws and coordinator.client.ws.epoch == epoch:
                    continue
            coordinator._mqtt_preferred = preferred
            if not preferred and (not active or getattr(coordinator.entry, "options", {}).get("direct_enabled") is False):
                continue
            candidate = coordinator._new_client()
            _LOGGER.debug("WHAT %s: preparing %s replacement", coordinator.entry.unique_id,
                          "MQTT" if preferred else "direct")
            try:
                if preferred:
                    socket = MqttSocket(coordinator.hass, coordinator.entry.unique_id, candidate.token, epoch)
                    candidate.ws = socket
                    await socket.start()
                    async with asyncio.timeout(15):
                        hello, data = await candidate.attach(socket)
                    candidate.transport = "mqtt"
                else:
                    hello, data = await candidate.connect()
                if (
                    coordinator._stopping
                    or coordinator._stream_ended
                    or coordinator._reauth_required
                    or coordinator._remote_queue.full()
                ):
                    await candidate.close()
                    candidate = None
                    continue
                coordinator._remote_queue.put_nowait(
                    (candidate, hello, data, asyncio.Event() if preferred else None),
                )
                candidate = None
            except (aiohttp.ClientError, ConnectionError, OSError, TimeoutError, ProtocolError, AuthenticationError) as err:
                coordinator.health.failed("mqtt" if preferred else "local", err)
                _LOGGER.debug("WHAT %s: replacement failed (%s); keeping current route",
                              coordinator.entry.unique_id, type(err).__name__)
                await candidate.close()
                candidate = None
                # Existing route remains active; the next announcement retries setup.
    except Exception:
        _LOGGER.exception(
            "WHAT MQTT transport stopped; direct connection remains available. Check the Home Assistant MQTT integration and reload WHAT",
        )
    finally:
        unsubscribe()
        if candidate:
            await candidate.close()
