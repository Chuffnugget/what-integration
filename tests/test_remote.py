import asyncio
import copy
import importlib
import sys
import types
import time
import pytest
import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import test_protocol

@pytest.fixture
def modules(monkeypatch):
    class Error(Exception):
        def __init__(self, *args, **kwargs):
            super().__init__(*args)
            self.__dict__.update(kwargs)
    class Coordinator:
        @classmethod
        def __class_getitem__(cls, item):
            return cls
        def __init__(self, hass, *args, **kwargs):
            self.hass = hass
            kwargs["config_entry"].entry_id = "test-entry"
            self.data = None
            self.last_update_success = False
        def async_set_updated_data(self, data):
            self.data = data
            self.last_update_success = True
        def async_set_update_error(self, error):
            self.last_update_success = False
    defs = {
        'homeassistant': {}, 'homeassistant.components': {}, 'homeassistant.components.http': {'HomeAssistantView': object},
        'homeassistant.config_entries': {'ConfigEntry': object}, 'homeassistant.core': {'HomeAssistant': object, 'callback': lambda fn: fn},
        'homeassistant.exceptions': {k: Error for k in ['ConfigEntryAuthFailed', 'ConfigEntryNotReady', 'HomeAssistantError']},
        'homeassistant.helpers.issue_registry': {
            'async_create_issue': lambda *args, **kwargs: None,
            'async_delete_issue': lambda *args: None,
            'IssueSeverity': types.SimpleNamespace(ERROR='error'),
        },
        'homeassistant.helpers': {}, 'homeassistant.helpers.aiohttp_client': {'async_get_clientsession': lambda hass: hass.session},
        'homeassistant.helpers.update_coordinator': {'DataUpdateCoordinator': Coordinator, 'UpdateFailed': Error},
    }
    for name, values in defs.items():
        module = types.ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(sys.modules, 'what_under_test.connection_health', raising=False)
    names = ['what_under_test.remote', 'what_under_test.coordinator']
    for name in names:
        monkeypatch.delitem(sys.modules, name, raising=False)
    yield tuple(importlib.import_module(name) for name in names)
    for name in names:
        sys.modules.pop(name, None)

@pytest.mark.parametrize(
    'case',
    ['wrong_token', 'disabled', 'unknown_device', 'invalid_protocol', 'origin'],
)
def test_remote_rejects_unauthorized(modules, case):
    remote, _ = modules
    async def run():
        called = []
        async def offer(ws):
            called.append(True)
            await ws.close()
        c = types.SimpleNamespace(
            entry=types.SimpleNamespace(options={'remote_enabled': case != 'disabled'}, data={'token': 'a' * 64}),
            offer_remote=offer,
        )
        hass = types.SimpleNamespace(data={remote.REGISTRY: {'pc-1': c}})
        view = remote.RemoteView(hass)
        app = web.Application()
        app.router.add_get(view.url, view.get)
        async with TestServer(app) as server, aiohttp.ClientSession() as session:
            if case == 'origin':
                with pytest.raises(aiohttp.WSServerHandshakeError):
                    await session.ws_connect(
                        server.make_url(view.url),
                        headers={'Origin': 'https://evil.example'},
                    )
            else:
                async with session.ws_connect(server.make_url(view.url)) as ws:
                    await ws.send_json(
                        {
                            'type': 'remote_auth',
                            'protocol': 1 if case == 'invalid_protocol' else 2,
                            'device_id': 'missing' if case == 'unknown_device' else 'pc-1',
                            'token': 'b' * 64 if case == 'wrong_token' else 'a' * 64,
                        },
                    )
                    message = await ws.receive(timeout=2)
                    assert message.type == aiohttp.WSMsgType.CLOSE
                    assert message.data == 1008
            assert not called
            async with asyncio.timeout(2):
                while view.pending:
                    await asyncio.sleep(.01)
    asyncio.run(run())

def test_reverse_stream_commands_and_disconnect(modules):
    remote, coordinator = modules
    async def run():
        async with aiohttp.ClientSession() as session:
            entry = types.SimpleNamespace(
                data={'host': '127.0.0.1', 'port': 1, 'token': 'a' * 64, 'fingerprint': '00' * 32},
                unique_id='pc-1',
                options={'remote_enabled': True},
                title='Laptop',
            )
            hass = types.SimpleNamespace(
                session=session,
                data={},
                async_create_background_task=lambda coro, name: asyncio.create_task(coro),
            )
            c = coordinator.WhatCoordinator(hass, entry)
            hass.data[remote.REGISTRY] = {'pc-1': c}
            await c.async_connect()
            assert not c.last_update_success
            c.start()
            view = remote.RemoteView(hass)
            app = web.Application()
            app.router.add_get(view.url, view.get)
            try:
                async with TestServer(app) as server:
                    ws = await session.ws_connect(server.make_url(view.url))
                    await ws.send_json(
                        {'type': 'remote_auth', 'protocol': 2, 'device_id': 'pc-1', 'token': 'a' * 64},
                    )
                    auth = await ws.receive_json(timeout=2)
                    assert auth['token'] == 'a' * 64
                    await ws.send_json(
                        {
                            'type': 'hello',
                            'protocol': 2,
                            'device_id': 'pc-1',
                            'name': 'Laptop',
                            'app_version': '0.6.0',
                            'commands_v1': True,
                            'delta_v1': False,
                        },
                    )
                    await ws.send_json(copy.deepcopy(test_protocol.SNAPSHOT))
                    async with asyncio.timeout(2):
                        while not c.last_update_success:
                            await asyncio.sleep(.01)
                    assert c.transport == 'remote'
                    assert c.readings['1_0_1']['value'] == 55.5
                    command = asyncio.create_task(c.client.command('keyboard', {'keys': ['A']}))
                    message = await ws.receive_json(timeout=2)
                    assert message['action'] == 'keyboard'
                    assert time.time() * 1000 < message['expires_at'] < time.time() * 1000 + 31000
                    await ws.send_json(
                        {'type': 'command_result', 'id': message['id'], 'ok': True, 'message': 'Submitted'},
                    )
                    assert (await asyncio.wait_for(command, 2))['ok']
                    await ws.close()
                    async with asyncio.timeout(2):
                        while c.last_update_success:
                            await asyncio.sleep(.01)
            finally:
                await c.async_stop()
    asyncio.run(run())


def make_coordinator(module):
    requests = []
    entry = types.SimpleNamespace(
        data={'host': '127.0.0.1', 'port': 1, 'token': 'a' * 64, 'fingerprint': '00' * 32},
        unique_id='pc-1', options={}, title='Test PC',
        async_start_reauth=lambda hass: requests.append(hass))
    hass = types.SimpleNamespace(session=None)
    return module.WhatCoordinator(hass, entry), requests


class IdleClient:
    def __init__(self):
        self.ws = object()
        self.closed = False
    async def close(self):
        self.closed = True
        self.ws = None
    async def receive_snapshot(self):
        await asyncio.Event().wait()


def test_malformed_display_frame_recovers(modules):
    _, module = modules
    async def run():
        c, _ = make_coordinator(module)
        bad = copy.deepcopy(test_protocol.SNAPSHOT)
        bad['displays'] = dict(enabled=False, control_enabled=False, available=False,
            busy=False, pending=False, status='Disabled', monitors=[], layouts=[], active_layout=[])
        class BadClient(IdleClient):
            async def receive_snapshot(self):
                return test_protocol.validate_message(bad, 'snapshot', 'pc-1')
        connected = asyncio.Event()
        class GoodClient(IdleClient):
            async def connect(self):
                connected.set()
                return {'name': 'Test PC'}, copy.deepcopy(test_protocol.SNAPSHOT)
        old = c.client = BadClient()
        good = GoodClient()
        c._new_client = lambda: good
        c._accept(copy.deepcopy(test_protocol.SNAPSHOT))
        task = c._task = asyncio.create_task(c._run())
        try:
            async with asyncio.timeout(2):
                while not old.closed:
                    await asyncio.sleep(.01)
            assert not c.last_update_success
            await asyncio.wait_for(connected.wait(), 5)
            async with asyncio.timeout(2):
                while not c.last_update_success:
                    await asyncio.sleep(.01)
            assert not task.done()
            assert c.readings['1_0_1']['value'] == 55.5
        finally:
            await c.async_stop()
    asyncio.run(run())


def test_unexpected_stream_failure_reconnects(modules, caplog):
    _, module = modules
    async def run():
        c, _ = make_coordinator(module)
        class BrokenClient(IdleClient):
            async def receive_snapshot(self):
                raise RuntimeError('fixture failure')
        broken = BrokenClient()
        c.client = broken
        c._accept(copy.deepcopy(test_protocol.SNAPSHOT))
        connected = asyncio.Event()
        class GoodClient(IdleClient):
            async def connect(self):
                connected.set()
                return {'name': 'Test PC'}, copy.deepcopy(test_protocol.SNAPSHOT)
        c._new_client = GoodClient
        task = c._task = asyncio.create_task(c._run())
        try:
            # A bug while handling a frame costs a reconnect, not the whole stream.
            await asyncio.wait_for(connected.wait(), 5)
            assert broken.closed
            assert 'error while handling the PC stream' in caplog.text
            async with asyncio.timeout(2):
                while not c.last_update_success:
                    await asyncio.sleep(.01)
            assert not task.done()
        finally:
            await c.async_stop()
    asyncio.run(run())


def test_offers_after_stop_are_closed(modules):
    _, module = modules
    async def run():
        c, _ = make_coordinator(module)
        c.client = IdleClient()
        c._accept(copy.deepcopy(test_protocol.SNAPSHOT))
        c._task = asyncio.create_task(c._run())
        await c.async_stop()
        # Offers after the stream ends must not wait forever in an unconsumed queue.
        class Socket:
            async def close(self, **kwargs):
                self.closed = True
        ws = Socket()
        await asyncio.wait_for(c.offer_remote(ws), 1)
        assert ws.closed
    asyncio.run(run())


def test_reconnect_rejection_requests_reauth_once_and_drains_offers(modules):
    _, module = modules
    async def run():
        c, requests = make_coordinator(module)
        c.client = IdleClient()
        await c.client.close()
        attempts = []
        queued = IdleClient()
        finished = asyncio.Event()
        class RejectedClient(IdleClient):
            async def connect(self):
                attempts.append(True)
                c._remote_queue.put_nowait((queued, {}, {}, finished))
                raise module.AuthenticationError('rejected')
        candidate = RejectedClient()
        c._new_client = lambda: candidate
        await asyncio.wait_for(c._run(), 3)
        assert len(attempts) == len(requests) == 1
        assert not c.last_update_success
        assert candidate.closed and queued.closed and finished.is_set()
        assert c._remote_queue.empty()
    asyncio.run(run())


def test_local_probe_rejection_preserves_remote_stream(modules, monkeypatch):
    _, module = modules
    async def run():
        c, requests = make_coordinator(module)
        c.client = IdleClient()
        c._remote_done = asyncio.Event()
        c._accept(copy.deepcopy(test_protocol.SNAPSHOT))
        class RejectedClient(IdleClient):
            async def connect(self):
                raise module.AuthenticationError('rejected')
        candidate = RejectedClient()
        c._new_client = lambda: candidate
        original_sleep = asyncio.sleep
        async def fast_sleep(delay):
            await original_sleep(0 if delay == 30 else delay)
        monkeypatch.setattr(module.asyncio, 'sleep', fast_sleep)
        await asyncio.wait_for(c._probe_local(), 1)
        assert c.last_update_success and not c.client.closed
        assert candidate.closed and not requests
        await c.async_stop()
    asyncio.run(run())


def test_local_probe_finishing_after_stream_exit_is_closed(modules, monkeypatch):
    _, module = modules
    async def run():
        c, _ = make_coordinator(module)
        c.client = IdleClient()
        c._remote_done = asyncio.Event()
        class LateClient(IdleClient):
            async def connect(self):
                c._stream_ended = True
                c._reauth_required = True
                return {}, copy.deepcopy(test_protocol.SNAPSHOT)
        candidate = LateClient()
        c._new_client = lambda: candidate
        original_sleep = asyncio.sleep
        async def fast_sleep(delay):
            await original_sleep(0 if delay == 30 else delay)
        monkeypatch.setattr(module.asyncio, 'sleep', fast_sleep)
        await asyncio.wait_for(c._probe_local(), 1)
        assert candidate.closed and c._remote_queue.empty()
        await c.async_stop()
    asyncio.run(run())


def test_connection_uptime_resets_only_after_disconnect(modules, monkeypatch):
    _, module = modules
    c, _ = make_coordinator(module)
    monkeypatch.setattr(module.time, "monotonic", lambda: 100)
    c._accept(copy.deepcopy(test_protocol.SNAPSHOT))
    assert c.connection_started == 100 and c.connected_since is not None
    monkeypatch.setattr(module.time, "monotonic", lambda: 120)
    c.transport = "mqtt"
    c._accept(copy.deepcopy(test_protocol.SNAPSHOT))
    assert c.connection_started == 100
    c.async_set_update_error(module.UpdateFailed("Disconnected"))
    assert c.connection_started is None and c.connected_since is None
    c._accept(copy.deepcopy(test_protocol.SNAPSHOT))
    assert c.connection_started == 120


def test_offer_arriving_while_failed_stream_closes_is_not_lost(modules):
    _, module = modules

    async def run():
        coordinator, _ = make_coordinator(module)
        closing = asyncio.Event()
        release = asyncio.Event()

        class FailedClient(IdleClient):
            async def receive_snapshot(self):
                raise ConnectionError("Connection lost")

            async def close(self):
                closing.set()
                await release.wait()
                await super().close()

        coordinator.client = FailedClient()
        replacement = IdleClient()
        finished = asyncio.Event()
        coordinator._task = asyncio.create_task(coordinator._run())
        try:
            await asyncio.wait_for(closing.wait(), 1)
            coordinator._remote_queue.put_nowait(
                (replacement, {}, copy.deepcopy(test_protocol.SNAPSHOT), finished),
            )
            await asyncio.sleep(0)
            release.set()
            async with asyncio.timeout(.5):
                while coordinator.client is not replacement:
                    await asyncio.sleep(.01)
            assert not replacement.closed
        finally:
            release.set()
            await coordinator.async_stop()
        assert replacement.closed and finished.is_set()

    asyncio.run(run())


def test_stop_during_handover_closes_claimed_offer(modules):
    _, module = modules

    async def run():
        coordinator, _ = make_coordinator(module)
        closing = asyncio.Event()

        class ClosingClient(IdleClient):
            async def close(self):
                if not closing.is_set():
                    closing.set()
                    await asyncio.Event().wait()
                await super().close()

        coordinator.client = ClosingClient()
        replacement = IdleClient()
        finished = asyncio.Event()
        coordinator._remote_queue.put_nowait(
            (replacement, {}, copy.deepcopy(test_protocol.SNAPSHOT), finished),
        )
        coordinator._task = asyncio.create_task(coordinator._run())
        await asyncio.wait_for(closing.wait(), 1)
        await asyncio.wait_for(coordinator.async_stop(), 1)
        assert replacement.closed, "The dequeued replacement socket was left open"
        assert finished.is_set(), "The remote request handler was left waiting"

    asyncio.run(run())



@pytest.mark.parametrize("transport", ["local", "remote", "mqtt"])
def test_only_pinned_local_connection_can_enable_other_routes(modules, transport):
    from unittest.mock import Mock
    _, module = modules
    c, _ = make_coordinator(module)
    c.transport = transport
    c.entry.options = {"custom": "keep"}
    update = Mock()
    c.hass.config_entries = types.SimpleNamespace(async_update_entry=update)
    c._accept(copy.deepcopy(test_protocol.SNAPSHOT) | {
        "connection_options": {"remote_enabled": True, "mqtt_enabled": True},
    })
    if transport == "local":
        update.assert_called_once_with(c.entry, options={"custom": "keep", "remote_enabled": True, "mqtt_enabled": True})
    else:
        update.assert_not_called()


def test_connection_choice_sync_preserves_defaults_and_allows_remote_revocation(modules):
    from unittest.mock import Mock
    _, module = modules
    c, _ = make_coordinator(module)
    update = Mock()
    c.hass.config_entries = types.SimpleNamespace(async_update_entry=update)
    frame = copy.deepcopy(test_protocol.SNAPSHOT) | {
        "connection_options": {"remote_enabled": False, "mqtt_enabled": False},
    }
    c._accept(frame)
    update.assert_not_called()
    c.transport = "remote"
    c.entry.options = {"remote_enabled": True, "mqtt_enabled": True, "custom": "keep"}
    c._accept(frame)
    update.assert_called_once_with(c.entry, options={"remote_enabled": False, "mqtt_enabled": False, "custom": "keep"})


@pytest.mark.parametrize("transport", ["local", "remote", "mqtt"])
def test_app_commands_permission_disconnect_and_no_retry(modules, transport):
    from unittest.mock import AsyncMock
    _, module = modules
    c, _ = make_coordinator(module)
    c.transport = transport
    c.client = types.SimpleNamespace(commands=True, command=AsyncMock())
    c._accept(copy.deepcopy(test_protocol.SNAPSHOT) | {"app": copy.deepcopy(test_protocol.APP)})
    seen = c.last_seen
    assert seen.tzinfo is not None

    async def run():
        with pytest.raises(module.HomeAssistantError, match="Manage WHAT"):
            await c.async_app_command("app_logging", True)
        c.client.command.assert_not_awaited()
        c.data["app"]["control_enabled"] = True
        await c.async_app_command("app_logging", False)
        c.client.command.assert_awaited_once_with("app_logging", {"value": False})
        c.client.command.reset_mock()
        c.client.command.side_effect = TimeoutError()
        with pytest.raises(module.HomeAssistantError, match="not retried"):
            await c.async_app_command("app_restart")
        c.client.command.assert_awaited_once_with("app_restart", {})
        c.async_set_update_error(module.UpdateFailed("Offline"))
        assert c.last_seen == seen
        c.client.command.reset_mock()
        with pytest.raises(module.HomeAssistantError, match="Manage WHAT"):
            await c.async_app_command("app_restart")
        c.client.command.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize("drift", [-86400, 0, 12, 86400])
def test_notification_actions_use_pc_time_and_keep_deduplication(modules, monkeypatch, drift):
    _, coordinator = modules
    now = 1700000000.0
    monotonic = [100.0]
    monkeypatch.setattr(coordinator.time, "time", lambda: now)
    monkeypatch.setattr(coordinator.time, "monotonic", lambda: monotonic[0])
    fired = []
    owner = types.SimpleNamespace(
        _notification_events=None, parent_device_id="device",
        entry=types.SimpleNamespace(unique_id="pc"),
        hass=types.SimpleNamespace(bus=types.SimpleNamespace(async_fire=lambda *args: fired.append(args))),
    )
    accept = coordinator.WhatCoordinator._accept_notification_events
    event = {"id": "a" * 32, "created": now + drift, "notification_id": "test", "action": "confirm", "reply": ""}
    frame = {"sent_at": now + drift, "controls": {"notifications_enabled": True}, "notifications": {"events": [event]}}
    accept(owner, frame)  # Initial connection never replays queued actions.
    assert not fired
    event["id"] = "b" * 32
    accept(owner, frame)
    accept(owner, frame)
    assert len(fired) == 1
    # A clock change on HA cannot age out the deduplication entry.
    monotonic[0] += 10
    monkeypatch.setattr(coordinator.time, "time", lambda: now + 100000)
    accept(owner, frame)
    assert len(fired) == 1
    event["id"] = "c" * 32
    event["created"] -= 61
    accept(owner, frame)
    assert len(fired) == 1  # Truly stale clicks are still rejected.
    event["id"] = "d" * 32
    event["created"] = now + drift
    frame["controls"]["notifications_enabled"] = False
    accept(owner, frame)
    assert len(fired) == 1
