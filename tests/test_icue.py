"""iCUE wire validation, availability and LED routing without physical hardware."""
import asyncio
import copy
import importlib
import sys
import types
from unittest.mock import AsyncMock
import pytest
from test_protocol import SNAPSHOT
from what_under_test.protocol import ProtocolError, StreamState, validate_message

ICUE = {"enabled": True, "connected": True, "control_enabled": True, "status": "Connected", "devices": [
    {
        "id": "fixture",
        "model": "Keyboard",
        "type": 1,
        "channels": 0,
        "leds": [{"id": 11, "rgb": [255, 0, 0]}, {"id": 22, "rgb": [0, 0, 0]}],
        "properties": [{"id": 9, "name": "Battery level", "value": 87, "writable": False}],
    }]}

def test_icue_metadata_and_delta_retention():
    stream = StreamState()
    first = copy.deepcopy(SNAPSHOT) | {"sequence": 1, "icue": copy.deepcopy(ICUE)}
    assert stream.accept(first, None, True)["icue"] == ICUE
    delta = copy.deepcopy(SNAPSHOT) | {"type": "delta", "sequence": 2, "base_sequence": 1, "sensors": [], "removed": []}
    assert stream.accept(delta, None, True)["icue"] == ICUE
    delta |= {
        "sequence": 3,
        "base_sequence": 2,
        "icue": {
            "enabled": False,
            "connected": False,
            "control_enabled": False,
            "status": "Disabled",
            "devices": [],
        },
    }
    assert stream.accept(delta, None, True)["icue"]["devices"] == []

@pytest.mark.parametrize("change", ["rgb", "duplicate_led", "writable", "enabled"])
def test_invalid_icue_data_is_rejected(change):
    data = copy.deepcopy(SNAPSHOT) | {"icue": copy.deepcopy(ICUE)}
    device = data["icue"]["devices"][0]
    if change == "rgb":
        device["leds"][0]["rgb"] = [256, 0, 0]
    if change == "duplicate_led":
        device["leds"].append(device["leds"][0])
    if change == "writable":
        device["properties"][0]["writable"] = True
    if change == "enabled":
        data["icue"]["enabled"] = "yes"
    with pytest.raises(ProtocolError):
        validate_message(data, "snapshot")

def test_individual_light_commands_and_availability(monkeypatch):
    # Stub only HA entity boundaries; execute the actual light/command adapter.
    class Base:
        def __init__(self, coordinator, identifier, name):
            self.coordinator = coordinator
    class Light:
        pass
    class Error(Exception):
        def __init__(self, *args, **kwargs):
            super().__init__(*args)
            self.__dict__.update(kwargs)
    for name, values in {
        "homeassistant": {}, "homeassistant.components": {},
        "homeassistant.components.light": {
            "LightEntity": Light,
            "ColorMode": types.SimpleNamespace(RGB="rgb", ONOFF="onoff"),
            "LightEntityFeature": __import__("enum").IntFlag("LightEntityFeature", {"EFFECT": 4}),
        },
        "homeassistant.exceptions": {"HomeAssistantError": Error},
        "what_under_test.entity": {"WhatEntity": Base},
    }.items():
        module = types.ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    for name in ("what_under_test.icue", "what_under_test.light"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    module = importlib.import_module("what_under_test.light")
    try:
        async def run():
            coordinator = types.SimpleNamespace(parent_device_id="ha-parent-id", data={"icue": copy.deepcopy(ICUE)}, last_update_success=True,
                entry=types.SimpleNamespace(unique_id="pc"), hello={"name": "DESKTOP-PC"}, client=types.SimpleNamespace(command=AsyncMock()))
            light = module.IcueLight(coordinator, ICUE["devices"][0], 22)
            assert light.available and not light._attr_entity_registry_enabled_default
            await light.async_turn_on(rgb_color=(255, 0, 0), brightness=128)
            coordinator.client.command.assert_awaited_once_with(
                "icue_colors",
                {"device": "fixture", "rgb": [128, 0, 0], "leds": [22]},
            )
            coordinator.data["icue"]["control_enabled"] = False
            assert not light.available
            with pytest.raises(Error):
                await light.async_turn_off()
            assert coordinator.client.command.await_count == 1
        asyncio.run(run())
    finally:
        sys.modules.pop("what_under_test.light", None)
        sys.modules.pop("what_under_test.icue", None)
