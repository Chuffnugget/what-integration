"""Live Qt publishing to independent Home Assistant installations and brokers."""
import asyncio
import json
import os
import socket
import subprocess
import types
import uuid

import aiohttp
import pytest
import test_protocol
from test_remote import modules


@pytest.mark.skipif(not os.getenv("WHAT_MQTT_TEST_EXE"), reason="Opt-in multiple broker + Qt fixture")
def test_multiple_installations_share_and_isolate_brokers(modules, monkeypatch, tmp_path, caplog):
    import logging
    caplog.set_level(logging.DEBUG, logger="what_under_test")
    from amqtt.broker import Broker
    import paho.mqtt.client as paho

    _, coordinator_module = modules

    async def until(predicate, timeout=20):
        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(.05)

    async def run():
        ports = []
        for _ in range(2):
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                ports.append(listener.getsockname()[1])
        brokers = [Broker({"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}},
                           "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}}})
                   for port in ports]
        buses = []
        coordinators = []
        process = None
        loop = asyncio.get_running_loop()
        subscriptions = [{}, {}]
        acknowledgements = [{}, {}]
        readiness = [{}, {}]
        stem = tmp_path / "multiple"
        try:
            for index, broker in enumerate(brokers):
                await broker.start()
                bus = paho.Client(paho.CallbackAPIVersion.VERSION2)
                connected = asyncio.Event()
                bus.on_connect = lambda *args, ready=connected: loop.call_soon_threadsafe(ready.set)

                def message(client, userdata, packet, index=index):
                    for callback in list(subscriptions[index].get(packet.topic, [])):
                        loop.call_soon_threadsafe(callback, types.SimpleNamespace(payload=packet.payload, retain=packet.retain))

                def subscribed(client, userdata, mid, reason, properties, index=index):
                    def finish():
                        event = acknowledgements[index].get(mid)
                        if event:
                            event.set()
                    loop.call_soon_threadsafe(finish)

                bus.on_message = message
                bus.on_subscribe = subscribed
                await asyncio.to_thread(bus.connect, "127.0.0.1", ports[index])
                bus.loop_start()
                buses.append(bus)
                await asyncio.wait_for(connected.wait(), 5)

            async def subscribe(hass, topic, callback, **kwargs):
                index = hass.broker
                subscriptions[index].setdefault(topic, []).append(callback)
                _, mid = buses[index].subscribe(topic, qos=0)
                ready = acknowledgements[index][mid] = asyncio.Event()
                readiness[index][topic] = ready

                def unsubscribe():
                    subscriptions[index][topic].remove(callback)
                    if not subscriptions[index][topic]:
                        buses[index].unsubscribe(topic)
                return unsubscribe

            async def publish(hass, topic, payload, **kwargs):
                result = buses[hass.broker].publish(topic, payload, qos=0, retain=False)
                await asyncio.to_thread(result.wait_for_publish, 5)

            def subscribed(hass, topic, qos, on_subscribe_status):
                async def acknowledge():
                    await readiness[hass.broker][topic].wait()
                    on_subscribe_status()
                return asyncio.create_task(acknowledge()).cancel

            async def ready(hass):
                return True

            mqtt = types.ModuleType("homeassistant.components.mqtt")
            mqtt.async_subscribe = subscribe
            mqtt.async_publish = publish
            mqtt.async_on_subscribe_done = subscribed
            mqtt.async_wait_for_mqtt_client = ready
            monkeypatch.setitem(__import__("sys").modules, "homeassistant.components.mqtt", mqtt)
            seen = []

            class CameraFixture:
                """A camera provider for the real paired MQTT clients and Windows bridge."""

                def __init__(self, coordinator, stream):
                    self.coordinator = coordinator
                    self.stream = stream
                    self.tasks = []

                async def start(self):
                    await self.stream.send_json({"type": "desktop_ready"})

                def submit(self, request):
                    self.tasks.append(asyncio.create_task(self.respond(request)))

                async def close(self):
                    if self.tasks:
                        await asyncio.gather(*self.tasks)

                async def respond(self, request):
                    seen.append((self.coordinator.entry.title, request["op"]))
                    reply = {"type": "desktop_result", "id": request["id"], "ok": True}
                    if request["op"] == "subscribe":
                        reply["rows"] = [
                            {**ref, "state": "streaming"} for ref in request["bindings"]
                        ]
                    elif request["op"] == "detail":
                        assert request["kind"] == "camera"
                        assert request["binding"] == {"entity_id": "camera.fixture"}
                        from what_under_test.mqtt_transport import encode

                        # Identical request, session, pairing secret and sequence.
                        # An old epoch must lose even when it arrives first.
                        stale = encode(self.stream.token, "ha", str(uuid.uuid4()), self.stream.session,
                                       self.stream.sent + 1, {**reply, "data": {"image": "stale"}})
                        await publish(self.coordinator.hass, self.stream.base + "/in", stale)
                        reply["data"] = {"image": "data:image/jpeg;base64," + "a" * 131072}
                    await self.stream.send_json(reply)

            monkeypatch.setattr(coordinator_module.WhatCoordinator, "_desktop_session",
                                lambda coordinator, stream: CameraFixture(coordinator, stream))
            env = os.environ | {"WHAT_MULTI_MQTT_FIXTURE": str(stem),
                                "WHAT_MULTI_MQTT_PORT1": str(ports[0]), "WHAT_MULTI_MQTT_PORT2": str(ports[1])}
            with (tmp_path / "process.txt").open("w") as output:
                process = subprocess.Popen([env["WHAT_MQTT_TEST_EXE"], "multiInstallationMqttFixture", "-o",
                                            str(tmp_path / "qt.txt") + ",txt"], env=env, stdout=output,
                                           stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
            await until(lambda: stem.with_suffix(".ready").exists() or process.poll() is not None)
            assert process.poll() is None
            async with aiohttp.ClientSession() as session:
                for index in range(3):
                    entry = types.SimpleNamespace(data={"host": "localhost", "port": 1,
                        "token": chr(ord("a") + index) * 64, "fingerprint": "00" * 32},
                        unique_id="multi-mqtt-fixture", title=f"House {index}",
                        options={"direct_enabled": False, "mqtt_enabled": True})
                    hass = types.SimpleNamespace(session=session, broker=1 if index == 1 else 0,
                        async_create_background_task=lambda coro, name: asyncio.create_task(coro),
                        config_entries=types.SimpleNamespace(async_update_entry=lambda entry, **changes: [setattr(entry, key, value) for key, value in changes.items()]))
                    coordinator = coordinator_module.WhatCoordinator(hass, entry)
                    await coordinator.async_connect()
                    coordinator.start()
                    coordinators.append(coordinator)
                await until(lambda: all(c.last_update_success and c.transport == "mqtt" for c in coordinators))
                assert all(c.readings["fixture"]["value"] == 1 for c in coordinators)
                await until(lambda: all(len(c.installation_connections) == 3
                    and all(row["status"] == "connected" for row in c.installation_connections.values())
                    for c in coordinators))
                for c in coordinators:
                    assert all(row["connected_since"] > 0 for row in c.installation_connections.values())
                # Two installations use the same broker and PC topic namespace.
                # Their distinct pairing tokens must keep their sessions separate.
                sessions = [c.client.ws.session for c in coordinators]
                assert len(set(sessions)) == 3
                stem.with_suffix(".command").write_text("desktop-camera")
                await until(lambda: stem.with_suffix(".desktop-verified").exists() or process.poll() is not None)
                assert process.poll() is None, (tmp_path / "qt.txt").read_text(errors="replace")
                assert ("House 1", "detail") in seen
                assert all(name == "House 1" for name, _ in seen)
                stem.with_suffix(".command").write_text("disable-first")
                await until(lambda: all(c.readings["fixture"]["value"] == 2 for c in coordinators[1:]))
                await until(lambda: all(c.installation_connections["a" * 32]["status"] == "disabled"
                    for c in coordinators[1:]))
                assert coordinators[0].readings["fixture"]["value"] == 1
                assert all(c.last_update_success for c in coordinators[1:])
                assert [c.client.ws.session for c in coordinators[1:]] == sessions[1:]
                for c in coordinators:
                    await c.async_stop()
                coordinators.clear()
            stem.with_suffix(".command").write_text("done")
            assert await asyncio.to_thread(process.wait, 10) == 0
        finally:
            for c in coordinators:
                await c.async_stop()
            if process and process.poll() is None:
                stem.with_suffix(".command").write_text("done")
                try:
                    await asyncio.to_thread(process.wait, 10)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    await asyncio.to_thread(process.wait)
            for bus in buses:
                bus.disconnect()
                bus.loop_stop()
            for broker in brokers:
                await broker.shutdown()

    asyncio.run(run())
