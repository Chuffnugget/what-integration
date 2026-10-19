"""Race and timeout regressions without a broker or a running Windows app."""
import asyncio
import copy
import logging
from types import SimpleNamespace

import pytest
import test_protocol
from test_remote import modules, make_coordinator, IdleClient
from what_under_test.api import WhatClient
from what_under_test.protocol import ProtocolError, decode_json


@pytest.mark.parametrize("payload", [
    '{"type":"hello","type":"snapshot"}', '{"value":NaN}', '{"value":Infinity}',
    '[' * 40 + '0' + ']' * 40, b'\xff',
], ids=["duplicate-key", "nan", "infinity", "deep-nesting", "invalid-utf8"])
def test_reject_ambiguous_or_malformed_json(payload):
    with pytest.raises(ProtocolError):
        decode_json(payload)


def test_alternate_route_interrupts_stalled_direct_connect(modules):
    _, module = modules

    async def run():
        c, _ = make_coordinator(module)
        started = asyncio.Event()

        class Stalled(IdleClient):
            async def connect(self):
                started.set()
                await asyncio.Event().wait()

        old = Stalled()
        c._new_client = lambda: old
        task = asyncio.create_task(c._connect_or_offer())
        await started.wait()
        replacement = IdleClient()
        finished = asyncio.Event()
        offer = (replacement, {}, copy.deepcopy(test_protocol.SNAPSHOT), finished)
        c._remote_queue.put_nowait(offer)
        assert await asyncio.wait_for(task, .5) == offer
        assert old.closed and not replacement.closed
        await replacement.close()

    asyncio.run(run())


def test_cancel_connect_race_releases_all_sockets(modules):
    _, module = modules

    async def run():
        c, _ = make_coordinator(module)
        closing = asyncio.Event()

        class Stalled(IdleClient):
            async def connect(self):
                await asyncio.Event().wait()
            async def close(self):
                if not closing.is_set():
                    closing.set()
                    await asyncio.Event().wait()
                await super().close()

        candidate = Stalled()
        c._new_client = lambda: candidate
        replacement = IdleClient()
        finished = asyncio.Event()
        c._remote_queue.put_nowait((replacement, {}, {}, finished))
        task = asyncio.create_task(c._connect_or_offer())
        await asyncio.wait_for(closing.wait(), 1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert candidate.closed and replacement.closed and finished.is_set()

    asyncio.run(run())


def test_planned_handover_preserves_availability_and_clears_withheld_readings(modules):
    _, module = modules

    async def run():
        c, _ = make_coordinator(module)
        c.client = IdleClient()
        c._accept(copy.deepcopy(test_protocol.SNAPSHOT))
        uptime = c.connection_started
        changes = []
        set_error = c.async_set_update_error
        c.async_set_update_error = lambda error: (changes.append(error), set_error(error))
        c._task = asyncio.create_task(c._run())
        try:
            for route in ("mqtt", "remote", "local", "mqtt"):
                replacement = IdleClient()
                replacement.transport = route
                data = copy.deepcopy(test_protocol.SNAPSHOT)
                if route == "mqtt":
                    data["sensors"] = []  # This route does not share the previous reading.
                c._remote_queue.put_nowait((replacement, {"name": "PC"}, data, asyncio.Event()))
                async with asyncio.timeout(1):
                    while c.client is not replacement:
                        await asyncio.sleep(0)
                assert c.last_update_success and c.connection_started == uptime
                assert bool(c.readings) == (route != "mqtt")
            assert not changes
        finally:
            await c.async_stop()

    asyncio.run(run())


def test_diagnostics_use_counts_without_rescanning_catalogue(modules):
    from what_under_test.connection_info import connection_info
    _, module = modules
    c, _ = make_coordinator(module)
    c._accept(copy.deepcopy(test_protocol.SNAPSHOT))

    class NoScan(dict):
        def values(self):
            raise AssertionError("Diagnostics rescanned the sensor catalogue")

    c.readings = NoScan(c.readings)
    for _ in range(10):
        assert connection_info(c)["published_entities"] == 1
        assert connection_info(c)["unavailable_readings"] == 0


def test_command_send_is_bounded_and_debug_log_excludes_payload(caplog):
    caplog.set_level(logging.DEBUG, logger="what_under_test.api")

    async def run():
        client = WhatClient(None, "localhost", 1, "private-token", "00" * 32, "test-pc")

        class Socket:
            closed = False
            async def send_json(self, message):
                await asyncio.Event().wait()
            async def close(self):
                self.closed = True

        client.ws, client.commands = Socket(), True
        with pytest.raises(TimeoutError):
            await client.command("shell", {"command": "private-command"}, timeout=.02)
        assert not client.pending
        await client.close()

    asyncio.run(run())
    assert "not retried" in caplog.text
    assert "private-token" not in caplog.text and "private-command" not in caplog.text


def test_command_replies_cannot_hide_missing_telemetry():
    async def run():
        client = WhatClient(None, "localhost", 1, "token", "00" * 32)
        client.timeout = .03

        async def reply():
            await asyncio.sleep(.005)
            return {"type": "command_result", "id": "late", "ok": True, "message": "Done"}

        client._receive = reply
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(client.receive_snapshot(), .5)

    asyncio.run(run())


@pytest.mark.parametrize("replacement_fails", [False, True])
def test_planned_handover_receives_existing_command_reply(modules, replacement_fails):
    _, module = modules

    async def run():
        c, _ = make_coordinator(module)
        old = c.client
        incoming = asyncio.Queue()
        sent = []

        class Socket:
            closed = False
            async def send_json(self, message):
                sent.append(message)
            async def close(self):
                self.closed = True

        old.ws, old.commands = Socket(), True
        old._receive = incoming.get
        c._accept(copy.deepcopy(test_protocol.SNAPSHOT))
        c._task = asyncio.create_task(c._run())
        command = asyncio.create_task(old.command("volume", {"value": 50}))
        try:
            await asyncio.sleep(0)
            replacement = IdleClient()
            c._remote_queue.put_nowait((replacement, {}, copy.deepcopy(test_protocol.SNAPSHOT), asyncio.Event()))
            await asyncio.sleep(.02)
            assert c.client is old and old.ws is not None
            if replacement_fails:
                replacement.ws = SimpleNamespace(closed=True)
            await incoming.put({"type": "command_result", "id": sent[0]["id"], "ok": True, "message": "Done"})
            assert (await asyncio.wait_for(command, 1))["ok"]
            async with asyncio.timeout(1):
                while True:
                    if replacement_fails and replacement.closed:
                        break
                    if not replacement_fails and c.client is replacement:
                        break
                    await asyncio.sleep(0)
            assert len(sent) == 1 and c.last_update_success
            if replacement_fails:
                assert c.client is old
        finally:
            await c.async_stop()
            await asyncio.gather(command, return_exceptions=True)

    asyncio.run(run())


def test_remote_only_waits_without_direct_retries(modules):
    _, module = modules

    async def run():
        c, _ = make_coordinator(module)
        c.entry.options = {"remote_enabled": True, "direct_enabled": False}

        def unexpected_client():
            raise AssertionError("Remote-only pairing must not probe an address")

        c._new_client = unexpected_client
        await c.async_connect()
        assert not c.last_update_success
        assert c.data["sensors"] == []
        task = asyncio.create_task(c._connect_or_offer())
        await asyncio.sleep(.01)
        assert not task.done()
        offer = (IdleClient(), {}, copy.deepcopy(test_protocol.SNAPSHOT), None)
        c._remote_queue.put_nowait(offer)
        assert await asyncio.wait_for(task, .5) == offer

    asyncio.run(run())


def test_connection_sync_preserves_pending_manual_pairing_change(modules):
    _, module = modules
    c, _ = make_coordinator(module)
    c.entry.data = dict(c.entry.data) | {"token": "replacement-token"}
    before = dict(c.entry.options)
    c._sync_transport_configuration(test_protocol.CONNECTION_CONFIG)
    assert not c.configuration_is_applied()
    assert c.entry.options == before
    assert c.entry.data["token"] == "replacement-token"
