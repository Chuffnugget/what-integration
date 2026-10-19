"""WebSocket client for one PC connection (certificate-pinned TLS). Has no Home Assistant imports."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import uuid
import time

import aiohttp
from yarl import URL

from .protocol import MAX_MESSAGE, StreamState, ProtocolError, decode_json, fingerprint_bytes, validate_message

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DecodedMessage:
    """Internal transport message whose JSON was already decoded and authenticated.

    This only saves a JSON round trip; WhatClient still validates the complete
    hello/snapshot/delta/command protocol before exposing anything to entities.
    """

    data: dict
    type = aiohttp.WSMsgType.TEXT


class AuthenticationError(Exception):
    """The app rejected its pairing token."""


class WhatClient:
    """One connection with a verified certificate and stable PC identity."""

    def __init__(self, session, host: str, port: int, token: str, fingerprint: str, device_id: str | None = None):
        self.session = session
        self.url = URL.build(scheme="wss", host=host.strip().strip("[]"), port=port)
        self.token = token.strip()
        self.pin = aiohttp.Fingerprint(fingerprint_bytes(fingerprint))
        self.device_id = device_id
        self.ws = None
        self.timeout = 30.0
        self.stream = StreamState()
        self.deltas = False
        self.commands = False
        self.ui_feedback = False
        self.transport_switch = False
        self.command_clock = None
        self.entity_resolver = None
        self.pending: dict[str, asyncio.Future] = {}

    async def connect(self) -> tuple[dict, dict]:
        """Authenticate, verify identity, and receive the first full snapshot."""
        await self.close()
        try:
            async with asyncio.timeout(15):
                self.ws = await self.session.ws_connect(
                    self.url, ssl=self.pin, heartbeat=20, max_msg_size=MAX_MESSAGE,
                )
                return await self.attach(self.ws)
        except BaseException:
            await self.close()
            raise

    async def attach(self, ws) -> tuple[dict, dict]:
        """Authenticate a TLS-accepted reverse socket using the same PC protocol."""
        self.ws = ws
        self.command_clock = None
        await self.ws.send_json(
            {
                "type": "auth",
                "protocol": 2,
                "token": self.token,
                "delta_v1": True,
                "commands_v1": True,
                "command_deadline_v1": True,
                "ui_feedback_v1": True,
                "transport_switch_v1": True,
            },
        )
        hello = validate_message(await self._receive(authenticating=True), "hello", self.device_id)
        self.device_id = hello["device_id"]
        self.deltas = hello.get("delta_v1") is True
        self.commands = hello.get("commands_v1") is True
        # Starting after hello conservatively includes handshake transit in the PC deadline.
        self.command_clock = time.monotonic() if hello.get("command_deadline_v1") is True else None
        self.ui_feedback = hello.get("ui_feedback_v1") is True
        self.transport_switch = hello.get("transport_switch_v1") is True
        self.stream = StreamState()
        data = await self.receive_snapshot()
        return hello, data

    async def _receive(self, authenticating: bool = False) -> dict:
        if self.ws is None:
            raise ConnectionError("Not connected")
        async with asyncio.timeout(self.timeout):
            message = await self.ws.receive()
        if message.type != aiohttp.WSMsgType.TEXT:
            if authenticating and (message.data == 1008 or self.ws.close_code == 1008):
                raise AuthenticationError("Pairing token rejected")
            if message.type == aiohttp.WSMsgType.ERROR:
                raise ConnectionError("WebSocket transport error") from self.ws.exception()
            raise ConnectionError("PC disconnected")
        if isinstance(message, DecodedMessage):
            return message.data
        return decode_json(message.data)

    async def receive_snapshot(self) -> dict:
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("No telemetry before the stream deadline")
            # Command replies must not keep stale telemetry available forever.
            async with asyncio.timeout(remaining):
                message = await self._receive()
            if isinstance(message, dict) and message.get("type") == "ui_probe":
                # A reply confirms HA processed the connection, including over
                # MQTT. Registry lookups are bounded and scoped to this PC.
                identifier = message.get("id")
                keys = message.get("keys")
                if (not self.ui_feedback or not isinstance(identifier, str)
                        or len(identifier) != 32
                        or any(c not in "0123456789abcdef" for c in identifier)
                        or not isinstance(keys, list) or len(keys) > 16
                        or any(not isinstance(key, str) or not 1 <= len(key) <= 256 for key in keys)):
                    raise ProtocolError("Invalid UI feedback request")
                entities = self.entity_resolver(keys) if self.entity_resolver else []
                async with asyncio.timeout(max(0, deadline - time.monotonic())):
                    await self.ws.send_json({"type": "ui_feedback", "id": identifier,
                                             "entities": entities[:32]})
                # UI requests cannot extend the telemetry freshness deadline.
                continue
            if isinstance(message, dict) and message.get("type") == "command_result":
                if (
                    not isinstance(message.get("id"), str)
                    or not message["id"]
                    or len(message["id"]) > 64
                    or type(message.get("ok")) is not bool
                    or not isinstance(message.get("message"), str)
                    or len(message["message"]) > 512
                ):
                    raise ProtocolError("Invalid command response")
                future = self.pending.get(message["id"])
                result = message.get("result")
                if result is not None and message["ok"]:
                    output_invalid = any(
                        not isinstance(result.get(key), str) or len(result[key]) > 65536
                        for key in ("stdout", "stderr")
                    ) if isinstance(result, dict) else True
                    flags_invalid = any(
                        type(result.get(key)) is not bool for key in ("timed_out", "cancelled", "truncated")
                    ) if isinstance(result, dict) else True
                    if (not isinstance(result, dict)
                            or set(result) != {"stdout", "stderr", "exit_code", "timed_out", "cancelled", "truncated"}
                            or output_invalid or flags_invalid
                            or type(result.get("exit_code")) is not int
                            or not 0 <= result["exit_code"] <= 4294967295):
                        raise ProtocolError("Invalid shell command result")
                if future is not None and not future.done():
                    future.set_result(message)
                continue
            data = self.stream.accept(message, self.device_id, self.deltas)
            if self.command_clock is not None and "session_ms" in data:
                # Use a fresh PC sample, rather than accumulating the frequency
                # difference between two monotonic clocks for the whole session.
                # Transit time reduces the deadline; it never extends it.
                self.command_clock = time.monotonic() - data["session_ms"] / 1000
            break
        self.timeout = max(30.0, data["publish_seconds"] * 3 + 10)
        return data

    async def activate_transport(self, data: dict) -> dict:
        """Commit a validated candidate before its coordinator releases the old route."""
        if not self.transport_switch or "connection_config" not in data:
            return data
        identifier = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[identifier] = future

        async def receive():
            nonlocal data
            while True:
                data = await self.receive_snapshot()

        reader = asyncio.create_task(receive())
        try:
            async with asyncio.timeout(5):
                await self.ws.send_json({"type": "transport_activate", "id": identifier,
                                         "revision": data["connection_config"]["revision"]})
                done, _ = await asyncio.wait((reader, future), return_when=asyncio.FIRST_COMPLETED)
                if reader in done:
                    reader.result()
                result = await future
                if not result["ok"]:
                    raise ConnectionError(result["message"])
            return data
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            self.pending.pop(identifier, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()

    async def command(self, action: str, args: dict | None = None, *, timeout: float = 12) -> dict:
        if not self.commands or self.ws is None or self.ws.closed:
            raise ConnectionError("Windows command support is unavailable")
        if len(self.pending) >= 8:
            raise ConnectionError("Too many pending Windows commands")
        identifier = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[identifier] = future
        started = time.monotonic()
        _LOGGER.debug("WHAT %s: sending %s command %s", self.device_id, action, identifier)
        try:
            async with asyncio.timeout(timeout):
                await self.ws.send_json(
                    {
                        "type": "command",
                        "id": identifier,
                        "action": action,
                        "args": args or {},
                        **({"deadline_ms": int((time.monotonic() - self.command_clock + min(timeout, 30)) * 1000)}
                           if self.command_clock is not None
                           else {"expires_at": int((time.time() + min(timeout, 30)) * 1000)}),
                    },
                )
                result = await future
            _LOGGER.debug("WHAT %s: %s command %s %s after %.3fs", self.device_id, action, identifier,
                          "accepted" if result["ok"] else "rejected", time.monotonic() - started)
            if not result["ok"]:
                raise ConnectionError(result["message"])
            return result
        except (ConnectionError, OSError, TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.debug("WHAT %s: %s command %s failed (%s), not retried", self.device_id,
                          action, identifier, type(err).__name__)
            raise
        finally:
            self.pending.pop(identifier, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                # A disconnect can arrive while send_json is still blocked.
                # Retrieve that failure even if the send itself raised first.
                future.exception()

    async def close(self) -> None:
        self.commands = False
        self.ui_feedback = False
        self.transport_switch = False
        self.command_clock = None
        for future in self.pending.values():
            if not future.done():
                future.set_exception(
                    ConnectionError(
                        "PC disconnected; command outcome may be unknown. Commands are never retried automatically.",
                    ),
                )
        if self.ws is not None:
            ws, self.ws = self.ws, None
            try:
                async with asyncio.timeout(5):
                    await ws.close()
            except TimeoutError:
                _LOGGER.debug("WHAT %s: transport close timed out", self.device_id)
