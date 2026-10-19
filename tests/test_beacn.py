"""BEACN protocol, discovery, observed state, permission, and no-retry fixtures."""
import asyncio
import copy
import importlib
import sys
import types
from unittest.mock import AsyncMock
import pytest
import test_protocol

CONTROLS = [
    {
        "id": "volume_2",
        "name": "Game volume",
        "kind": "number",
        "value": 35.5,
        "available": True,
        "min": 0,
        "max": 100,
        "step": 1,
        "options": [],
    },
    {"id": "mute_2", "name": "Game mute", "kind": "switch", "value": False, "available": True},
    {
        "id": "profile",
        "name": "Profile",
        "kind": "select",
        "value": "Studio",
        "available": True,
        "options": ["Studio", "Gaming"],
    },
    {"id": "next_page", "name": "Next page", "kind": "button", "value": None, "available": True},
]
STATE = {
    "enabled": True,
    "ready": True,
    "connected": True,
    "control_enabled": True,
    "cloud": False,
    "controls": CONTROLS,
}

def test_valid_and_legacy_frames():
    frame = copy.deepcopy(test_protocol.SNAPSHOT)
    frame["beacn"] = copy.deepcopy(STATE)
    assert test_protocol.validate_message(frame, "snapshot")["beacn"]["controls"][0]["value"] == 35.5
    del frame["beacn"]
    test_protocol.validate_message(frame, "snapshot")

@pytest.mark.parametrize("bad", [
    [], {"enabled": 1}, {"controls": {}}, {"controls": [CONTROLS[0], CONTROLS[0]]},
    {"controls": [{**CONTROLS[0], "value": float("nan")}]},
    {"controls": [{**CONTROLS[0], "value": True}]},
    {"controls": [{**CONTROLS[0], "value": 101}]},
    {"controls": [{**CONTROLS[1], "value": 1}]},
    {"controls": [{**CONTROLS[2], "options": "Studio"}]},
    {"controls": [{**CONTROLS[0], "id": "../escape"}]},
    {"global": []}, {"channels": [{"id": True}]},
])
def test_malformed_state_rejected(bad):
    frame = copy.deepcopy(test_protocol.SNAPSHOT)
    frame["beacn"] = bad
    with pytest.raises(test_protocol.ProtocolError):
        test_protocol.validate_message(frame, "snapshot")

@pytest.fixture
def beacn(monkeypatch):
    class Error(Exception):
        def __init__(self, *args, **kwargs):
            super().__init__(*args)
            self.__dict__.update(kwargs)
    class Entity:
        def __init__(self, coordinator, *args):
            self.coordinator = coordinator
        def async_write_ha_state(self):
            pass
    modules = {
        "homeassistant": {},
        "homeassistant.components": {},
        "homeassistant.core": {"callback": lambda f: f},
        "homeassistant.exceptions": {"HomeAssistantError": Error},
        "what_under_test.entity": {"WhatEntity": Entity},
    }
    for kind in ("number", "select", "switch", "button", "sensor"):
        modules["homeassistant.components." + kind] = {kind.title() + "Entity": type(kind.title() + "Entity", (), {})}
    for name, values in modules.items():
        mod = types.ModuleType(name)
        mod.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.delitem(sys.modules, "what_under_test.beacn", raising=False)
    mod = importlib.import_module("what_under_test.beacn")
    yield mod
    sys.modules.pop("what_under_test.beacn", None)

def coordinator():
    return types.SimpleNamespace(
        data={"beacn": copy.deepcopy(STATE)},
        last_update_success=True,
        client=types.SimpleNamespace(command=AsyncMock(return_value={"ok": True})),
    )

def test_observed_state_and_once_only_dispatch(beacn):
    async def run():
        c = coordinator()
        number = beacn.BeacnNumber(c, CONTROLS[0])
        switch = beacn.BeacnSwitch(c, CONTROLS[1])
        select = beacn.BeacnSelect(c, CONTROLS[2])
        button = beacn.BeacnButton(c, CONTROLS[3])
        assert number.available and number.native_value == 36
        await number.async_set_native_value(42)
        c.client.command.assert_awaited_once_with(
            "beacn",
            {"control": "volume_2", "value": 42},
            timeout=12,
        )
        assert number.native_value == 36 # No optimistic claim that the device changed.
        c.data["beacn"]["controls"].reverse() # Visual reorder retains source identity.
        assert number.native_value == 36
        await switch.async_turn_on()
        await select.async_select_option("Gaming")
        await button.async_press()
        assert c.client.command.await_count == 4
        with pytest.raises(beacn.HomeAssistantError):
            await select.async_select_option("Deleted")
        with pytest.raises(beacn.HomeAssistantError):
            await number.async_set_native_value(float("nan"))
        assert c.client.command.await_count == 4
        c.client.command.side_effect = TimeoutError()
        with pytest.raises(beacn.HomeAssistantError):
            await button.async_press()
        assert c.client.command.await_count == 5
    asyncio.run(run())

@pytest.mark.parametrize("flag", ["enabled", "ready", "control_enabled"])
def test_permission_and_disconnect(beacn, flag):
    async def run():
        c = coordinator()
        c.data["beacn"][flag] = False
        assert not beacn.BeacnNumber(c, CONTROLS[0]).available
        with pytest.raises(beacn.HomeAssistantError):
            await beacn.send(c, "volume_2", 50)
        c.client.command.assert_not_awaited()
    asyncio.run(run())

def test_discovery_reconnect_and_cleanup(beacn):
    c = coordinator()
    listeners = []
    cleanup = []
    added = []
    c.async_add_listener = lambda fn: listeners.append(fn) or "unsubscribe"
    entry = types.SimpleNamespace(runtime_data=c, async_on_unload=cleanup.append)
    beacn.discover_beacn(entry, added.extend, "number")
    assert len(added) == 1 and cleanup == ["unsubscribe"]
    listeners[0]()
    assert len(added) == 1
    c.data["beacn"] = {"enabled": False}
    listeners[0]()
    assert not added[0].available
    c.data["beacn"] = copy.deepcopy(STATE)
    listeners[0]()
    assert len(added) == 1 and added[0].available
    c.data["beacn"]["controls"].append({**CONTROLS[0], "id": "volume_3"})
    listeners[0]()
    assert len(added) == 2

def test_beacn_sensor_catalogue_readings():
    frame = copy.deepcopy(test_protocol.SNAPSHOT)
    frame["sensors"] = [{
        "id": "beacn_2_personal_volume",
        "source": "beacn",
        "group": "Channels / Game",
        "name": "Personal volume",
        "kind": "number",
        "unit": "%",
        "type": 0,
        "available": True,
        "value": 35.5,
        "state_class": "",
    },
                      {
                          "id": "beacn_2_audience_muted",
                          "source": "beacn",
                          "group": "Channels / Game",
                          "name": "Audience muted",
                          "kind": "binary",
                          "unit": "",
                          "type": 0,
                          "available": True,
                          "value": False,
                          "state_class": "",
                      }]
    assert len(test_protocol.validate_message(frame, "snapshot")["sensors"]) == 2
    frame["sensors"][0]["available"] = False
    frame["sensors"][0]["value"] = None
    test_protocol.validate_message(frame, "snapshot")


@pytest.mark.parametrize("value,step,expected", [
    (62.9999876022339, 1, 63),
    (0.0000001, 1, 0),
    (99.999999, 1, 100),
    (62.4999876, 0.5, 62.5),
    (62.3499999, 0.01, 62.35),
    (None, 1, None),
])
def test_number_readback_precision(beacn, value, step, expected):
    c = coordinator()
    control = c.data["beacn"]["controls"][0]
    control.update(value=value, step=step)
    number = beacn.BeacnNumber(c, control)
    assert number.native_value == expected
    assert control["value"] == value
    c.client.command.assert_not_awaited()


def test_busy_keeps_observed_controls_available_but_rejects_commands(beacn):
    c = coordinator()
    number = beacn.BeacnNumber(c, CONTROLS[0])
    c.data["beacn"]["busy"] = True
    assert number.available and number.native_value == 36
    with pytest.raises(beacn.HomeAssistantError):
        asyncio.run(number.async_set_native_value(42))
    c.client.command.assert_not_awaited()
    c.data["beacn"]["ready"] = False
    assert not number.available
    c.data["beacn"]["ready"] = True
    c.last_update_success = False
    assert not number.available

@pytest.mark.parametrize(
    "bad",
    [None, "volume_5", ["volume_5", "volume_5"], [1], ["../bad"], ["volume_2"]],
)
def test_invalid_unsupported_controls(bad):
    frame = copy.deepcopy(test_protocol.SNAPSHOT)
    frame["beacn"] = copy.deepcopy(STATE) | {"unsupported_controls": bad}
    with pytest.raises(test_protocol.ProtocolError):
        test_protocol.validate_message(frame, "snapshot")
