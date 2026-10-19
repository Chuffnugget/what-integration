"""Confirmed PC feedback and bounded, read-only HA entity lookup."""
import asyncio
import copy
import sys
from types import SimpleNamespace

import pytest
from test_protocol import SNAPSHOT
from test_remote import modules, make_coordinator
from what_under_test.api import WhatClient
from what_under_test.protocol import ProtocolError


def probe(**changes):
    return {"type": "ui_probe", "id": "a" * 32, "keys": ["1_0_1"], **changes}


def test_feedback_does_not_replace_telemetry_or_send_commands():
    async def run():
        client = WhatClient(None, "localhost", 48765, "token", "00" * 32)
        client.ui_feedback = True
        client.entity_resolver = lambda keys: ["sensor.renamed_temperature"] if keys == ["1_0_1"] else []
        incoming = iter([probe(), copy.deepcopy(SNAPSHOT)])
        sent = []
        async def receive():
            return next(incoming)
        async def send(data):
            sent.append(data)
        client._receive = receive
        client.ws = SimpleNamespace(send_json=send)
        assert (await client.receive_snapshot())["sensors"] == SNAPSHOT["sensors"]
        assert sent == [{"type": "ui_feedback", "id": "a" * 32,
                         "entities": ["sensor.renamed_temperature"]}]
        assert not client.commands and not client.pending
    asyncio.run(run())


@pytest.mark.parametrize("supported,frame", [
    (False, probe()), (True, probe(id="bad")), (True, probe(keys=["x"] * 17)),
    (True, probe(keys=[None])), (True, probe(keys=["x" * 257])),
])
def test_feedback_requires_negotiation_and_bounded_keys(supported, frame):
    async def run():
        client = WhatClient(None, "localhost", 48765, "token", "00" * 32)
        client.ui_feedback = supported
        async def receive():
            return frame
        client._receive = receive
        with pytest.raises(ProtocolError):
            await client.receive_snapshot()
    asyncio.run(run())


def test_ui_probes_cannot_keep_stale_telemetry_alive():
    async def run():
        client = WhatClient(None, "localhost", 48765, "token", "00" * 32)
        client.ui_feedback = True
        client.timeout = .04
        async def receive():
            await asyncio.sleep(.005)
            return probe(keys=[])
        async def send(data):
            pass
        client._receive = receive
        client.ws = SimpleNamespace(send_json=send)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(client.receive_snapshot(), .2)
    asyncio.run(run())


def test_lookup_uses_current_registry_names_and_only_this_pc(modules, monkeypatch):
    _, module = modules
    async def run():
        coordinator, _ = make_coordinator(module)
        own = coordinator.entry.unique_id + "_1_0_1"
        names = {("sensor", "what", own): "sensor.my_temperature"}
        registry = SimpleNamespace(
            async_get_entity_id=lambda domain, platform, unique: names.get((domain, platform, unique)),
            async_get=lambda entity_id: SimpleNamespace(config_entry_id=coordinator.entry.entry_id),
        )
        monkeypatch.setitem(sys.modules, "homeassistant.helpers.entity_registry",
                            SimpleNamespace(async_get=lambda hass: registry))
        assert coordinator.client.entity_resolver(["1_0_1"]) == ["sensor.my_temperature"]
        names[("sensor", "what", own)] = "sensor.new_name"
        assert coordinator._new_client().entity_resolver(["1_0_1"]) == ["sensor.new_name"]
        assert coordinator._entity_ids(["another_pc_1_0_1"]) == []
        registry.async_get = lambda entity_id: SimpleNamespace(config_entry_id="other-entry")
        assert coordinator._entity_ids(["1_0_1"]) == []
    asyncio.run(run())
