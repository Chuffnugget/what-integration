import asyncio
import copy
import importlib
import sys
import types
from unittest.mock import AsyncMock
import pytest
from test_protocol import SNAPSHOT
from what_under_test.protocol import ProtocolError, StreamState, validate_message

AUTO = {"enabled": True, "connected": True, "control_enabled": True, "status": "Connected", "controls": [
    {"id": "profile", "name": "iCUE profile", "device": "iCUE", "confirmed": True,
     "current": "a", "options": [{"id": "a", "name": "Default"}, {"id": "b", "name": "Gaming"}]}]}


def test_automation_delta_retention_and_disable():
    stream = StreamState()
    first = copy.deepcopy(SNAPSHOT) | {"sequence": 1, "icue_automation": copy.deepcopy(AUTO)}
    assert stream.accept(first, None, True)["icue_automation"] == AUTO
    delta = copy.deepcopy(SNAPSHOT) | {"type": "delta", "sequence": 2, "base_sequence": 1, "sensors": [], "removed": []}
    assert stream.accept(delta, None, True)["icue_automation"] == AUTO
    disabled = {
        "enabled": False,
        "connected": False,
        "control_enabled": False,
        "status": "Disabled",
        "controls": [],
    }
    delta |= {"sequence": 3, "base_sequence": 2, "icue_automation": disabled}
    assert stream.accept(delta, None, True)["icue_automation"] == disabled


@pytest.mark.parametrize(
    "bad",
    ["permission", "duplicate_control", "duplicate_option", "unknown_current", "unconfirmed_current", "raw_method"],
)
def test_invalid_automation(bad):
    data = copy.deepcopy(AUTO)
    c = data["controls"][0]
    if bad == "permission":
        data["control_enabled"] = "yes"
    if bad == "duplicate_control":
        data["controls"].append(copy.deepcopy(c))
    if bad == "duplicate_option":
        c["options"].append(c["options"][0])
    if bad == "unknown_current":
        c["current"] = "missing"
    if bad == "unconfirmed_current":
        c["confirmed"] = False
    if bad == "raw_method":
        c["id"] = "com.corsair.icue.anything"
    with pytest.raises(ProtocolError):
        validate_message(copy.deepcopy(SNAPSHOT) | {"icue_automation": data}, "snapshot")


def test_dropdown_discovery_commands_and_permissions(monkeypatch):
    class Base:
        def __init__(self, coordinator, identifier, name):
            self.coordinator = coordinator
            self.unique_id = identifier
        def async_write_ha_state(self):
            pass
    class Select:
        pass
    class Error(Exception):
        def __init__(self, *args, **kwargs):
            super().__init__(*args)
            self.__dict__.update(kwargs)
    for name, values in {
        "homeassistant": {}, "homeassistant.components": {},
        "homeassistant.components.select": {"SelectEntity": Select},
        "homeassistant.exceptions": {"HomeAssistantError": Error},
        "what_under_test.entity": {"WhatEntity": Base},
    }.items():
        module = types.ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    name = "what_under_test.icue_automation"
    monkeypatch.delitem(sys.modules, name, raising=False)
    module = importlib.import_module(name)
    try:
        async def run():
            listeners, added = [], []
            coordinator = types.SimpleNamespace(data={"icue_automation": copy.deepcopy(AUTO)}, last_update_success=True,
                client=types.SimpleNamespace(command=AsyncMock()), async_add_listener=lambda cb: listeners.append(cb))
            entry = types.SimpleNamespace(runtime_data=coordinator, async_on_unload=lambda cb: None)
            module.discover_automation(entry, added.extend)
            assert len(added) == 1
            entity = added[0]
            assert entity.available and entity.current_option == "Default"
            await entity.async_select_option("Gaming")
            coordinator.client.command.assert_awaited_once_with(
                "icue_automation",
                {"control": "profile", "option": "b"},
            )
            assert entity.current_option == "Default"  # Wait for actual PC feedback.
            coordinator.data["icue_automation"]["control_enabled"] = False
            assert not entity.available
            with pytest.raises(Error):
                await entity.async_select_option("Gaming")
            coordinator.data["icue_automation"] = copy.deepcopy(AUTO)
            coordinator.data["icue_automation"]["controls"][0]["options"][1]["name"] = "Default"
            assert len(entity.options) == 2 and len(set(entity.options)) == 2
            coordinator.data["icue_automation"]["controls"] = []
            assert not entity.available
            listeners[0]()
            coordinator.data["icue_automation"] = copy.deepcopy(AUTO)
            listeners[0]()
            assert len(added) == 1  # Disabling/re-enabling does not duplicate entities.
            with pytest.raises(Error):
                await entity.async_select_option("Missing")
        asyncio.run(run())
    finally:
        sys.modules.pop(name, None)
