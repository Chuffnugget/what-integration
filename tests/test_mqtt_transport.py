import asyncio
import json
import logging
import os
import subprocess
import time
import types
from pathlib import Path
import pytest
import aiohttp
import test_protocol
from test_remote import modules
from what_under_test.mqtt_transport import encode, decode
from what_under_test.protocol import ProtocolError


@pytest.mark.parametrize("direction", ["pc", "ha"])
def test_signed_message_round_trip(direction):
    message = {"type": "snapshot", "value": 44.9, "label": "温度"}
    envelope = encode("secret", direction, "epoch", "session", 123, message)
    header, decoded = decode("secret", direction, envelope)
    assert decoded == message and header["seq"] == 123
    assert "secret" not in envelope
    with pytest.raises(ProtocolError):
        decode("other", direction, envelope)
    with pytest.raises(ProtocolError):
        decode("secret", "ha" if direction == "pc" else "pc", envelope)


@pytest.mark.parametrize(
    "field,value",
    [("seq", True), ("seq", -1), ("session", []), ("body", "bad"), ("mac", "0" * 64)],
)
def test_signed_message_rejects_tampering(field, value):
    packet = json.loads(encode("secret", "pc", "e", "s", 1, {"type": "snapshot"}))
    packet[field] = value
    with pytest.raises(ProtocolError):
        decode("secret", "pc", json.dumps(packet))


@pytest.mark.skipif(not os.getenv("WHAT_MQTT_TEST_EXE"), reason="Opt-in broker + Qt fixture")
def test_live_mqtt_handover_scopes_and_commands(modules, monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.DEBUG, logger="what_under_test")
    import ssl
    import socket
    from datetime import datetime, timedelta, timezone
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from amqtt.broker import Broker
    import paho.mqtt.client as paho
    _, coord_module = modules
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(
        x509.random_serial_number(),
    )
          .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1)).not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
          .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(key, hashes.SHA256()))
    ca = tmp_path / "ca.pem"
    ca.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private = tmp_path / "key.pem"
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    async def run():
        broker = Broker(
            {
                "listeners": {
                    "default": {
                        "type": "tcp",
                        "bind": f"127.0.0.1:{port}",
                        "ssl": True,
                        "certfile": str(ca),
                        "keyfile": str(private),
                    },
                },
                "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}},
            },
        )
        await broker.start()
        loop = asyncio.get_running_loop()
        connected = loop.create_future()
        subscriptions = {}
        subscription_done = {}
        topic_ready = {}
        bus = paho.Client(paho.CallbackAPIVersion.VERSION2)
        bus.tls_set(ca_certs=str(ca))
        bus.on_connect = lambda client, userdata, flags, reason, properties: loop.call_soon_threadsafe(connected.set_result, reason)
        def on_message(client, userdata, message):
            for callback in list(subscriptions.get(message.topic, [])):
                loop.call_soon_threadsafe(
                    callback,
                    types.SimpleNamespace(payload=message.payload, retain=message.retain),
                )
        bus.on_message = on_message
        def on_subscribe(client, userdata, mid, reason, properties):
            def finish():
                future = subscription_done.get(mid)
                if future and not future.done():
                    future.set_result(True)
            loop.call_soon_threadsafe(finish)
        bus.on_subscribe = on_subscribe
        await asyncio.to_thread(bus.connect, "localhost", port)
        bus.loop_start()
        await asyncio.wait_for(connected, 5)
        async def subscribe(hass, topic, callback, **kwargs):
            subscriptions.setdefault(topic, []).append(callback)
            result, mid = bus.subscribe(topic, qos=0)
            future = subscription_done[mid] = loop.create_future()
            topic_ready[topic] = future
            def unsubscribe():
                subscriptions[topic].remove(callback)
                if not subscriptions[topic]:
                    bus.unsubscribe(topic)
            return unsubscribe
        async def publish(hass, topic, payload, **kwargs):
            result = bus.publish(topic, payload, qos=0, retain=False)
            await asyncio.to_thread(result.wait_for_publish, 5)
        mqtt = types.ModuleType("homeassistant.components.mqtt")
        mqtt.async_subscribe = subscribe
        mqtt.async_publish = publish
        def on_subscribed(hass, topic, qos, on_subscribe_status):
            async def acknowledge():
                await asyncio.shield(topic_ready[topic])
                on_subscribe_status()
            task = asyncio.create_task(acknowledge())
            return task.cancel
        mqtt.async_on_subscribe_done = on_subscribed
        async def ready(hass):
            return True
        mqtt.async_wait_for_mqtt_client = ready
        monkeypatch.setitem(__import__('sys').modules, "homeassistant.components.mqtt", mqtt)
        stem = tmp_path / "fixture"
        env = os.environ | {"WHAT_MQTT_FIXTURE": str(stem), "WHAT_MQTT_PORT": str(port), "WHAT_MQTT_CA": str(ca)}
        import ctypes
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002)
        process_log = (tmp_path / "process.txt").open("w")
        proc = subprocess.Popen(
            [os.environ["WHAT_MQTT_TEST_EXE"], "mqttLiveFixture", "-o", str(tmp_path / "qt.txt") + ",txt"],
            env=env,
            stdout=process_log,
            stderr=subprocess.STDOUT,
        )
        c = None
        async def until(predicate, seconds=20):
            async with asyncio.timeout(seconds):
                while not predicate():
                    await asyncio.sleep(.05)
        try:
            await until(lambda: stem.with_suffix('.json').exists() or proc.poll() is not None)
            assert proc.poll() is None, (proc.returncode, env.get("PATH"), env.get("Path"), (tmp_path / "process.txt").read_text(errors="replace"))
            info = json.loads(stem.with_suffix('.json').read_text())
            async with aiohttp.ClientSession() as http:
                entry = types.SimpleNamespace(
                    data={
                        "host": "127.0.0.1",
                        "port": info['port'],
                        "token": info['token'],
                        "fingerprint": info['fingerprint'],
                    },
                    unique_id=info['device_id'],
                    options={"mqtt_enabled": False},
                    title="Fixture",
                )
                hass = types.SimpleNamespace(
                    session=http,
                    async_create_background_task=lambda coro, name: asyncio.create_task(coro),
                    config_entries=types.SimpleNamespace(
                        async_update_entry=lambda item, **changes: [setattr(item, key, value) for key, value in changes.items()]),
                )
                c = coord_module.WhatCoordinator(hass, entry)
                await c.async_connect()
                c.start()
                assert "mqtt_local" in c.readings and "mqtt_cloud" not in c.readings, (c.hello, c.data)
                identity = c.hello['device_id']
                direct = c.client
                stem.with_suffix('.command').write_text('mqtt')
                await until(lambda: c.transport == 'mqtt')
                assert c.hello['device_id'] == identity and direct.ws is None
                assert 'mqtt_cloud' in c.readings and 'mqtt_local' not in c.readings
                assert c.readings['mqtt_full']['value'] == 44.9
                with pytest.raises(ConnectionError):
                    await c.client.command('unsupported', {})
                stem.with_suffix('.command').write_text('revoke')
                await until(lambda: 'mqtt_full' not in c.readings)
                mqtt_client = c.client
                # An invalid PC certificate must not replace a working MQTT session.
                entry.data['fingerprint'] = '00' * 32
                stem.with_suffix('.command').write_text('direct')
                await asyncio.sleep(3)
                assert c.transport == 'mqtt' and c.client is mqtt_client and c.last_update_success
                entry.data['fingerprint'] = info['fingerprint']
                await until(lambda: c.transport == 'local')
                assert mqtt_client.ws is None
                assert c.hello['device_id'] == identity
                assert 'mqtt_local' in c.readings and 'mqtt_cloud' not in c.readings
                assert c.configuration_is_applied()
                await c.async_stop()
                c = None
        finally:
            if c:
                await c.async_stop()
            stem.with_suffix('.command').write_text('done')
            try:
                await asyncio.to_thread(proc.wait, 10)
            except subprocess.TimeoutExpired:
                proc.terminate()
                await asyncio.to_thread(proc.wait, 5)
            process_log.close()
            bus.disconnect()
            await asyncio.to_thread(bus.loop_stop)
            await broker.shutdown()
        assert proc.returncode == 0, (tmp_path / 'qt.txt').read_text(errors='replace')[-3000:]
    asyncio.run(run())

@pytest.mark.parametrize('async_subscription_api', [False, True])
@pytest.mark.parametrize(
    'retained,sequence,accepted',
    [(False, 1, True), (True, 1, False), (False, 0, False), (False, 2, False)],
)
def test_mqtt_socket_rejects_retained_and_old_frames(monkeypatch, retained, sequence, accepted, async_subscription_api):
    import sys
    from what_under_test.mqtt_transport import MqttSocket
    async def run():
        callbacks = []
        sent = []
        unsubscribed = []
        async def subscribe(hass, topic, callback, **kwargs):
            callbacks.append(callback)
            return lambda: unsubscribed.append(True)
        async def publish(hass, topic, payload, **kwargs):
            sent.append((payload, kwargs))
        mqtt = types.ModuleType('homeassistant.components.mqtt')
        mqtt.async_subscribe = subscribe
        mqtt.async_publish = publish
        mqtt.async_on_subscribe_done = lambda hass, topic, qos, on_subscribe_status: (on_subscribe_status() or (lambda: None))
        if async_subscription_api:
            async def subscription_api(hass, topic, qos, on_subscribe_status):
                on_subscribe_status()
                return lambda: None
            mqtt.async_on_subscribe_done = subscription_api
        parent = types.ModuleType('homeassistant.components')
        parent.mqtt = mqtt
        core = types.ModuleType('homeassistant.core')
        core.callback = lambda fn: fn
        monkeypatch.setitem(sys.modules, 'homeassistant.core', core)
        monkeypatch.setitem(sys.modules, 'homeassistant.components', parent)
        monkeypatch.setitem(sys.modules, 'homeassistant.components.mqtt', mqtt)
        socket = MqttSocket(None, 'pc', 'secret', 'epoch')
        await socket.start()
        await socket.send_json({'type': 'auth', 'token': 'secret', 'protocol': 2})
        _, auth = decode('secret', 'ha', sent[0][0])
        assert 'token' not in auth
        assert sent[0][1] == {'qos': 0, 'retain': False}
        frame = encode('secret', 'pc', 'epoch', socket.session, sequence, {'type': 'snapshot'})
        callbacks[0](types.SimpleNamespace(payload=frame, retain=retained))
        if sequence == 2:
            assert socket.closed
            assert (await socket.receive()).type == aiohttp.WSMsgType.CLOSED
        else:
            assert (not socket.queue.empty()) == accepted
        if accepted:
            await socket.receive()
            callbacks[0](types.SimpleNamespace(payload=frame, retain=False))
            assert socket.queue.empty()
            for next_sequence in range(2, 35):
                packet = encode('secret', 'pc', 'epoch', socket.session, next_sequence, {'type': 'snapshot'})
                callbacks[0](types.SimpleNamespace(payload=packet, retain=False))
            assert socket.closed  # Overflow must discard the whole incomplete baseline.
            assert (await socket.receive()).type == aiohttp.WSMsgType.CLOSED
        await socket.close()
        assert unsubscribed and socket.ping_task is None
    asyncio.run(run())


def test_failed_mqtt_candidate_keeps_existing_transport(monkeypatch):
    import sys
    import what_under_test.mqtt_transport as transport
    async def run():
        callback = None
        closed = []
        async def subscribe(hass, topic, fn, **kwargs):
            nonlocal callback
            callback = fn
            return lambda: None
        async def ready(hass):
            return True
        mqtt = types.ModuleType('homeassistant.components.mqtt')
        mqtt.async_subscribe = subscribe
        mqtt.async_wait_for_mqtt_client = ready
        mqtt.async_on_subscribe_done = lambda *args, **kwargs: None
        parent = types.ModuleType('homeassistant.components')
        parent.mqtt = mqtt
        core = types.ModuleType('homeassistant.core')
        core.callback = lambda fn: fn
        monkeypatch.setitem(sys.modules, 'homeassistant.core', core)
        monkeypatch.setitem(sys.modules, 'homeassistant.components', parent)
        monkeypatch.setitem(sys.modules, 'homeassistant.components.mqtt', mqtt)
        class Socket:
            def __init__(self, *args):
                pass
            async def start(self):
                pass
        class Candidate:
            token = 'secret'
            async def attach(self, socket):
                raise ConnectionError('Fixture rejected handshake')
            async def close(self):
                closed.append(True)
        monkeypatch.setattr(transport, 'MqttSocket', Socket)
        existing = types.SimpleNamespace(token='secret')
        c = types.SimpleNamespace(
            hass=None,
            client=existing,
            entry=types.SimpleNamespace(unique_id='pc'),
            _stopping=False,
            _reauth_required=False,
            _stream_ended=False,
            _remote_queue=asyncio.Queue(1),
            _new_client=Candidate,
        )
        task = asyncio.create_task(transport.watch_mqtt(c))
        try:
            await asyncio.sleep(0)
            payload = encode('secret', 'pc', 'epoch', '', 0, {'preferred': True, 'time': time.time()})
            callback(types.SimpleNamespace(payload=payload, retain=False))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert closed and c.client is existing and c._remote_queue.empty()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize('terminal_flag', ['_stream_ended', '_reauth_required'])
def test_late_mqtt_candidate_is_closed_after_stream_exit(monkeypatch, terminal_flag):
    import sys
    import what_under_test.mqtt_transport as transport

    async def run():
        announced = None
        closed = asyncio.Event()

        async def subscribe(hass, topic, callback, **kwargs):
            nonlocal announced
            announced = callback
            return lambda: None

        async def ready(hass):
            return True

        mqtt = types.ModuleType('homeassistant.components.mqtt')
        mqtt.async_subscribe = subscribe
        mqtt.async_wait_for_mqtt_client = ready
        mqtt.async_on_subscribe_done = lambda *args, **kwargs: None
        parent = types.ModuleType('homeassistant.components')
        parent.mqtt = mqtt
        core = types.ModuleType('homeassistant.core')
        core.callback = lambda fn: fn
        monkeypatch.setitem(sys.modules, 'homeassistant.core', core)
        monkeypatch.setitem(sys.modules, 'homeassistant.components', parent)
        monkeypatch.setitem(sys.modules, 'homeassistant.components.mqtt', mqtt)

        class Socket:
            def __init__(self, *args):
                pass
            async def start(self):
                pass

        class Candidate:
            token = 'secret'
            async def attach(self, socket):
                setattr(coordinator, terminal_flag, True)
                return {}, {}
            async def close(self):
                closed.set()

        monkeypatch.setattr(transport, 'MqttSocket', Socket)
        existing = types.SimpleNamespace(token='secret')
        coordinator = types.SimpleNamespace(hass=None, client=existing,
            entry=types.SimpleNamespace(unique_id='pc'), _stopping=False,
            _reauth_required=False, _stream_ended=False, _remote_queue=asyncio.Queue(1),
            _new_client=Candidate)
        task = asyncio.create_task(transport.watch_mqtt(coordinator))
        try:
            await asyncio.sleep(0)
            announced(types.SimpleNamespace(retain=False, payload=encode('secret', 'pc', 'epoch', '', 0,
                {'preferred': True, 'time': time.time()})))
            await asyncio.wait_for(closed.wait(), 1)
            assert coordinator._remote_queue.empty()
            assert coordinator.client is existing
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())



def test_authenticated_mqtt_avoids_json_round_trip_but_validates_protocol(modules, monkeypatch):
    import sys
    from what_under_test.api import WhatClient, DecodedMessage
    from what_under_test.mqtt_transport import MqttSocket
    async def run():
        callbacks = []
        mqtt = types.ModuleType("homeassistant.components.mqtt")
        async def subscribe(hass, topic, callback, **kwargs):
            callbacks.append(callback)
            return lambda: None
        async def publish(*args, **kwargs):
            pass
        mqtt.async_subscribe = subscribe
        mqtt.async_publish = publish
        def subscribed(hass, topic, qos, on_subscribe_status):
            on_subscribe_status()
            return lambda: None
        mqtt.async_on_subscribe_done = subscribed
        monkeypatch.setitem(sys.modules, "homeassistant.components.mqtt", mqtt)
        socket = MqttSocket(None, "pc-1", "secret", "epoch")
        client = WhatClient(None, "localhost", 1234, "secret", "ab" * 32, "pc-1")
        client.ws = socket
        await socket.start()
        try:
            body = test_protocol.SNAPSHOT | {"sensors": []}
            packet = encode("secret", "pc", "epoch", socket.session, 1, body)
            callbacks[0](types.SimpleNamespace(payload=packet, retain=False))
            assert isinstance(socket.queue._queue[0], DecodedMessage)
            # The socket hands the already-decoded dictionary directly to the
            # client. No redundant JSON serialization or parsing is involved.
            assert (await client.receive_snapshot())["sensors"] == []
            invalid = body | {"publish_seconds": 0}
            packet = encode("secret", "pc", "epoch", socket.session, 2, invalid)
            callbacks[0](types.SimpleNamespace(payload=packet, retain=False))
            with pytest.raises(ProtocolError):
                await client.receive_snapshot()
            # Authentication and replay protection still run before queueing.
            callbacks[0](types.SimpleNamespace(payload=packet, retain=False))
            callbacks[0](types.SimpleNamespace(
                payload=encode("wrong", "pc", "epoch", socket.session, 3, body),
                retain=False,
            ))
            assert socket.queue.empty()
        finally:
            await client.close()
    asyncio.run(run())


@pytest.mark.parametrize("preferred", [False, True])
def test_discovery_probe_requires_fresh_signed_reply(modules, monkeypatch, preferred):
    import sys
    from what_under_test.mqtt_transport import probe_preference

    async def run():
        callbacks = []
        sent = []
        unsubscribed = []

        async def subscribe(hass, topic, callback, **kwargs):
            callbacks.append(callback)
            return lambda: unsubscribed.append(topic)

        async def publish(hass, topic, payload, **kwargs):
            header, body = decode("secret", "ha", payload)
            sent.append(body["type"])
            assert body["type"] == "discovery_probe"
            response = {"type": "discovery_reply", "preferred": preferred}
            # Each of these must be ignored before a valid response arrives.
            for token, epoch, session, retained in [
                ("secret", "old-epoch", header["session"], False),
                ("secret", "epoch", "old-session", False),
                ("wrong", "epoch", header["session"], False),
                ("secret", "epoch", header["session"], True),
            ]:
                callbacks[0](types.SimpleNamespace(retain=retained,
                    payload=encode(token, "pc", epoch, session, 1, response | {"preferred": not preferred})))
            callbacks[0](types.SimpleNamespace(retain=False,
                payload=encode("secret", "pc", "epoch", header["session"], 1, response)))

        mqtt = types.ModuleType("homeassistant.components.mqtt")
        monkeypatch.setitem(sys.modules, "homeassistant.components.mqtt", mqtt)
        monkeypatch.setattr(mqtt, "async_subscribe", subscribe, raising=False)
        monkeypatch.setattr(mqtt, "async_publish", publish, raising=False)
        monkeypatch.setattr(mqtt, "async_on_subscribe_done",
            lambda hass, topic, qos, on_subscribe_status: (on_subscribe_status() or (lambda: None)), raising=False)
        assert await probe_preference(None, "pc", "secret", "epoch") is preferred
        assert sent == ["discovery_probe"]  # Closing a probe must not disconnect the live route.
        assert unsubscribed

    asyncio.run(run())


@pytest.mark.parametrize("probe_supported", [False, True])
@pytest.mark.parametrize("probe_fails", [False, True])
def test_discovery_clock_drift_requires_live_confirmation(modules, monkeypatch, probe_supported, probe_fails):
    import sys
    import what_under_test.mqtt_transport as transport
    from unittest.mock import AsyncMock

    async def run():
        announced = None
        async def subscribe(hass, topic, callback, **kwargs):
            nonlocal announced
            announced = callback
            return lambda: None
        mqtt = types.ModuleType("homeassistant.components.mqtt")
        monkeypatch.setitem(sys.modules, "homeassistant.components.mqtt", mqtt)
        monkeypatch.setattr(mqtt, "async_subscribe", subscribe, raising=False)
        monkeypatch.setattr(mqtt, "async_wait_for_mqtt_client", AsyncMock(return_value=True), raising=False)
        monkeypatch.setattr(mqtt, "async_on_subscribe_done", lambda *args, **kwargs: None, raising=False)
        probe = AsyncMock(return_value=True, side_effect=TimeoutError if probe_fails else None)
        monkeypatch.setattr(transport, "probe_preference", probe)
        candidate = types.SimpleNamespace(token="secret", attach=AsyncMock(return_value=({}, {})), close=AsyncMock())
        monkeypatch.setattr(transport, "MqttSocket", lambda *args: types.SimpleNamespace(start=AsyncMock()))
        existing = types.SimpleNamespace(token="secret", transport="local")
        coordinator = types.SimpleNamespace(hass=None, client=existing,
            entry=types.SimpleNamespace(unique_id="pc"), _stopping=False, _stream_ended=False,
            _reauth_required=False, _remote_queue=asyncio.Queue(1), _new_client=lambda: candidate)
        task = asyncio.create_task(transport.watch_mqtt(coordinator))
        try:
            await asyncio.sleep(0)
            announced(types.SimpleNamespace(retain=False, payload=encode("secret", "pc", "epoch", "", 0,
                {"time": time.time() - 3600, "preferred": True, "discovery_probe_v1": probe_supported})))
            for _ in range(5):
                await asyncio.sleep(0)
            assert probe.await_count == int(probe_supported)
            assert coordinator._remote_queue.empty() is (not probe_supported or probe_fails)
            assert coordinator.client is existing
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
