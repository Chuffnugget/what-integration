"""Command routing, timeouts/disconnects and untrusted control metadata."""
import asyncio
import copy
import pytest
from test_protocol import SNAPSHOT
from what_under_test.api import WhatClient
from what_under_test.protocol import validate_message, ProtocolError


def controls():
    return {
        "version": 1,
        "permissions": dict.fromkeys(("media", "power", "power_plan", "urls", "monitor"), False),
        "values": {},
        "power": {},
    }


def test_controls_are_optional_for_older_apps():
    assert validate_message(copy.deepcopy(SNAPSHOT), "snapshot")
    data = copy.deepcopy(SNAPSHOT)
    data["controls"] = controls()
    assert validate_message(data, "snapshot")["controls"] == controls()


@pytest.mark.parametrize(
    "change",
    [{"permissions": {"power": 1}}, {"values": {"win_media_position": float("nan")}}, {"power": {"plans": [{}]}}, {"values": {"win_audio_volume": {}}}],
)
def test_reject_bad_controls(change):
    data = copy.deepcopy(SNAPSHOT)
    data["controls"] = controls() | change
    with pytest.raises(ProtocolError):
        validate_message(data, "snapshot")


def test_unknown_permissions_are_ignored_and_missing_permissions_stay_off():
    data = copy.deepcopy(SNAPSHOT)
    data["controls"] = controls() | {"permissions": {"power": True, "future_control": True}}
    permissions = validate_message(data, "snapshot")["controls"]["permissions"]
    assert permissions == controls()["permissions"] | {"power": True}


def test_command_ack_demultiplexes_from_telemetry():
    async def run():
        client = WhatClient(None, "localhost", 48765, "token", "00" * 32)
        incoming = asyncio.Queue()
        sent = []
        class Socket:
            closed = False
            async def send_json(self, data):
                sent.append(data)
            async def close(self):
                self.closed = True
        client.ws = Socket()
        client.commands = True
        async def receive():
            return await incoming.get()
        client._receive = receive
        reader = asyncio.create_task(client.receive_snapshot())
        command = asyncio.create_task(client.command("volume", {"value": .4}))
        await asyncio.sleep(0)
        assert len(sent) == 1 and sent[0]["args"] == {"value": .4}
        output = {
            "stdout": "hello",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "cancelled": False,
            "truncated": False,
        }
        await incoming.put(
            {
                "type": "command_result",
                "id": sent[0]["id"],
                "ok": True,
                "message": "Completed",
                "result": output,
            },
        )
        assert (await command)["result"] == output
        await incoming.put(copy.deepcopy(SNAPSHOT))
        assert (await reader)["sensors"] == SNAPSHOT["sensors"]
        assert not client.pending
        await client.close()
    asyncio.run(run())


def test_disconnect_never_retries_a_command():
    async def run():
        client = WhatClient(None, "localhost", 48765, "token", "00" * 32)
        sent = []
        class Socket:
            closed = False
            async def send_json(self, data):
                sent.append(data)
            async def close(self):
                self.closed = True
        client.ws = Socket()
        client.commands = True
        task = asyncio.create_task(client.command("shutdown"))
        await asyncio.sleep(0)
        await client.close()
        with pytest.raises(ConnectionError, match="never retried"):
            await task
        assert len(sent) == 1 and not client.pending
    asyncio.run(run())


@pytest.mark.parametrize("result", [
    {
        "stdout": "x" * 65537,
        "stderr": "",
        "exit_code": 0,
        "timed_out": False,
        "cancelled": False,
        "truncated": True,
    },
    {
        "stdout": "ok",
        "stderr": "",
        "exit_code": True,
        "timed_out": False,
        "cancelled": False,
        "truncated": False,
    },
    {"stdout": "ok"},
])
def test_rejects_malformed_shell_output(result):
    async def run():
        client = WhatClient(None, "localhost", 48765, "token", "00" * 32)
        async def receive():
            return {
                "type": "command_result",
                "id": "test",
                "ok": True,
                "message": "Completed",
                "result": result,
            }
        client._receive = receive
        with pytest.raises(ProtocolError, match="shell command result"):
            await client.receive_snapshot()
    asyncio.run(run())


def test_shell_permission_is_optional_but_strict():
    data = copy.deepcopy(SNAPSHOT)
    data["controls"] = controls() | {"shell_enabled": True}
    assert validate_message(data, "snapshot")["controls"]["shell_enabled"]
    data["controls"]["shell_enabled"] = "true"
    with pytest.raises(ProtocolError):
        validate_message(data, "snapshot")


def test_relative_command_deadline_ignores_wall_clock(monkeypatch):
    import what_under_test.api as api
    async def run():
        client = WhatClient(None, "localhost", 48765, "token", "00" * 32)
        client.commands = True
        client.command_clock = 50.0
        sent = []
        class Socket:
            closed = False
            async def send_json(self, data):
                sent.append(data)
                client.pending[data["id"]].set_result({"ok": True})
        client.ws = Socket()
        monkeypatch.setattr(api.time, "monotonic", lambda: 55.0)
        monkeypatch.setattr(api.time, "time", lambda: -1000000000.0)
        await client.command("volume", {"value": .5})
        assert sent[0]["deadline_ms"] == 17000
        assert "expires_at" not in sent[0]
        client.command_clock = None
        await client.command("volume", {"value": .5})
        assert "expires_at" in sent[1] and "deadline_ms" not in sent[1]
    asyncio.run(run())


@pytest.mark.parametrize("pc_elapsed", [1000, 20 * 86400 * 1000 + 60000])
def test_command_deadline_reanchors_from_telemetry(monkeypatch, pc_elapsed):
    import what_under_test.api as api
    async def run():
        client = WhatClient(None, "localhost", 48765, "token", "00" * 32)
        client.commands = True
        client.command_clock = 10.0
        frame = copy.deepcopy(SNAPSHOT) | {"session_ms": pc_elapsed, "sent_at": 1700000000}
        async def receive():
            return frame
        client._receive = receive
        sent = []
        class Socket:
            closed = False
            async def send_json(self, data):
                sent.append(data)
                client.pending[data["id"]].set_result({"ok": True})
        client.ws = Socket()
        monkeypatch.setattr(api.time, "monotonic", lambda: 500.0)
        await client.receive_snapshot()
        await client.command("volume", {"value": .5})
        assert abs(sent[0]["deadline_ms"] - (pc_elapsed + 12000)) <= 1
    asyncio.run(run())


@pytest.mark.parametrize("field", ["session_ms", "sent_at"])
@pytest.mark.parametrize("value", [-1, True, "100", float("nan"), float("inf"), 2 ** 53])
def test_invalid_peer_timing_is_rejected(field, value):
    with pytest.raises(ProtocolError):
        validate_message(copy.deepcopy(SNAPSHOT) | {field: value}, "snapshot")
