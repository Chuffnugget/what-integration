"""Desktop negotiation on both supported transports with the real HA state machine."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import aiohttp
import pytest
from homeassistant.core import callback

from custom_components.what.api import DecodedMessage, WhatClient
from custom_components.what.desktop_bridge import DesktopBridge
from custom_components.what.desktop_catalog import DESKTOP_ACCESS, VIEW
from custom_components.what.mqtt_transport import decode, encode
from custom_components.what.protocol import ProtocolError

pytestmark = pytest.mark.asyncio
HELLO = {"type":"hello", "protocol":2, "device_id":"paired-pc", "name":"PC", "app_version":"0.0.1",
         "desktop_v1":True}
SNAPSHOT = {"type":"snapshot", "protocol":2, "device_id":"paired-pc", "healthy":True,
            "hwinfo_enabled":False, "windows_healthy":True, "status":"Connected", "sample_time":1,
            "sample_age":0, "publish_seconds":5, "license":"unknown", "sensors":[]}


class DesktopSocket:
    def __init__(self, transport):
        self.mqtt = transport == "mqtt"
        self.incoming = asyncio.Queue()
        self.outgoing = []
        self.close = AsyncMock()
        self.closed = False

    def push(self, frame):
        if self.mqtt:
            packet = encode("local-token", "pc", "boot", "session", 1, frame)
            _, authenticated = decode("local-token", "pc", packet)
            data = DecodedMessage(authenticated)
        else:
            data = SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(frame))
        self.incoming.put_nowait(data)

    async def receive(self):
        return await self.incoming.get()

    async def send_json(self, frame):
        self.outgoing.append(frame)


@pytest.mark.parametrize("transport", ["api", "mqtt"])
async def test_negotiated_route_and_shutdown(flow_hass, paired_entry, transport):
    hass = flow_hass
    hass.states.async_set("sensor.desk", "21", {"unit_of_measurement":"°C"})
    hass.config_entries.async_update_entry(paired_entry, options={VIEW:["sensor.desk"]})
    client = WhatClient(None, "localhost", 443, "local-token", "00"*32, "paired-pc")
    client.desktop_factory = lambda ws: DesktopBridge(hass, paired_entry, ws)
    sock = DesktopSocket(transport)
    sock.push(HELLO)
    sock.push(SNAPSHOT)
    await client.attach(sock)
    assert sock.outgoing[0]["desktop_v1"] and client.desktop_bridge is None
    identifier = uuid.uuid4().hex
    search = {"type":"desktop_request", "id":identifier, "op":"search", "kind":"numeric"}
    sock.push(search)
    with pytest.raises(ProtocolError, match="not been activated"):
        await client.receive_snapshot()
    client.desktop_active = True
    sock.push(search)
    sock.push(SNAPSHOT)
    await client.receive_snapshot()
    await hass.async_block_till_done()
    assert sock.outgoing[1]["type"] == "desktop_ready"
    assert sock.outgoing[2]["id"] == identifier
    assert sock.outgoing[2]["rows"][0]["state"] == "21"
    bridge = client.desktop_bridge
    await client.close()
    assert bridge.closed and sock.close.await_count == 1
    assert not client.desktop_enabled and not client.desktop_active


@pytest.mark.parametrize("transport", ["api", "mqtt"])
async def test_paired_route_controls_thermostat_and_honours_revocation(flow_hass, paired_entry, transport):
    hass = flow_hass
    entity_id = "climate.air_con"
    binding = {"entity_id": entity_id}
    hass.states.async_set(entity_id, "cool", {"supported_features": 1, "min_temp": 16, "max_temp": 30})
    calls = []

    @callback
    def set_temperature(call):
        calls.append(dict(call.data))

    hass.services.async_register("climate", "set_temperature", set_temperature)
    client = WhatClient(None, "localhost", 443, "local-token", "00"*32, "paired-pc")
    client.desktop_factory = lambda ws: DesktopBridge(hass, paired_entry, ws)
    sock = DesktopSocket(transport)
    sock.push(HELLO)
    sock.push(SNAPSHOT)
    await client.attach(sock)
    client.desktop_active = True

    async def exchange(op, **params):
        identifier = uuid.uuid4().hex
        sock.push({"type": "desktop_request", "id": identifier, "op": op, **params})
        sock.push(SNAPSHOT)
        await client.receive_snapshot()
        await hass.async_block_till_done()
        return next(frame for frame in sock.outgoing if frame.get("id") == identifier)

    try:
        # The entry fixture only has transport options, as an existing untouched pairing would.
        row, = (await exchange("search", domain="climate"))["rows"]
        assert row["entity_id"] == entity_id
        row, = (await exchange("subscribe", bindings=[binding]))["rows"]
        assert "climate.set_temperature" in {action["id"] for action in row["actions"]}
        change = {"binding": binding, "action": "climate.set_temperature", "data": {"temperature": 23}}
        assert (await exchange("action", **change))["ok"]
        hass.config_entries.async_update_entry(paired_entry, options={DESKTOP_ACCESS: "view"})
        assert not (await exchange("action", **change))["ok"]
        row, = (await exchange("subscribe", bindings=[binding]))["rows"]
        assert row["state"] == "cool" and row["actions"] == []
        assert calls == [{"entity_id": entity_id, "temperature": 23}]
    finally:
        await client.close()


async def test_legacy_peer_does_not_receive_desktop_frames(flow_hass, paired_entry):
    client = WhatClient(None, "localhost", 443, "local-token", "00"*32, "paired-pc")
    bridge = AsyncMock()
    client.desktop_factory = lambda _: bridge
    sock = DesktopSocket("api")
    hello = dict(HELLO)
    del hello["desktop_v1"]
    sock.push(hello)
    sock.push(SNAPSHOT)
    await client.attach(sock)
    client.desktop_active = True
    sock.push(SNAPSHOT)
    await client.receive_snapshot()
    assert not client.desktop_enabled and len(sock.outgoing) == 1
    bridge.start.assert_not_awaited()
    await client.close()


async def test_retained_route_retries_an_interrupted_ready(flow_hass, paired_entry):
    client = WhatClient(None, "localhost", 443, "local-token", "00"*32, "paired-pc")
    entered = asyncio.Event()

    async def stall():
        entered.set()
        await asyncio.Future()

    interrupted = AsyncMock()
    interrupted.start.side_effect = stall
    retried = AsyncMock()
    sessions = iter((interrupted, retried))
    client.desktop_factory = lambda _: next(sessions)
    sock = DesktopSocket("api")
    sock.push(HELLO)
    sock.push(SNAPSHOT)
    await client.attach(sock)
    client.desktop_active = True
    reader = asyncio.create_task(client.receive_snapshot())
    await asyncio.wait_for(entered.wait(), 1)
    reader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reader
    interrupted.close.assert_awaited_once()
    sock.push(SNAPSHOT)
    await client.receive_snapshot()
    retried.start.assert_awaited_once()
    await client.close()

