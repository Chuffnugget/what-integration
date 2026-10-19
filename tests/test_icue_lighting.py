import asyncio
import copy
import importlib
import sys
import types
from enum import IntFlag
from unittest.mock import AsyncMock
import pytest
from test_protocol import SNAPSHOT
from test_icue_automation import AUTO
from what_under_test.protocol import validate_message, ProtocolError

LIGHTING = {"available": True, "effect": "ColorWave", "parameters": {"Speed": 1, "Direction": 2, "RandomColor": False, "Color1": "#204060", "Color2": "#604020"},
 "effects": [{
     "id": "ColorWave",
     "name": "Colour wave",
     "parameters": ["Speed", "Direction", "RandomColor", "Color1", "Color2"],
 },
             {"id": "StaticColor", "name": "Static colour", "parameters": ["Color1"]},
             {"id": "RainbowWave", "name": "Rainbow wave", "parameters": ["Speed", "Direction"]}]}

@pytest.mark.parametrize(
    "bad",
    [None, "speed", "direction", "rotation", "colour", "random", "duplicate", "unknown_effect", "unknown_param", "capabilities", "none_params", "availability"],
)
def test_lighting_protocol(bad):
    lighting = copy.deepcopy(LIGHTING)
    if bad == "speed":
        lighting["parameters"]["Speed"] = 3
    if bad == "direction":
        lighting["parameters"]["Direction"] = 4
    if bad == "rotation":
        lighting["parameters"]["Rotation"] = 1
    if bad == "colour":
        lighting["parameters"]["Color1"] = -14663584
    if bad == "random":
        lighting["parameters"]["RandomColor"] = 1
    if bad == "duplicate":
        lighting["effects"].append(lighting["effects"][0])
    if bad == "unknown_effect":
        lighting["effect"] = "arbitrary"
    if bad == "unknown_param":
        lighting["parameters"]["command"] = "anything"
    if bad == "capabilities":
        lighting["effects"][0]["parameters"].append("anything")
    if bad == "none_params":
        lighting["effect"] = None
    if bad == "availability":
        lighting["available"] = 1
    frame = copy.deepcopy(SNAPSHOT) | {"icue_automation": copy.deepcopy(AUTO) | {"lighting": lighting}}
    if bad:
        with pytest.raises(ProtocolError):
            validate_message(frame, "snapshot")
    else:
        assert validate_message(frame, "snapshot")["icue_automation"]["lighting"] == lighting


def test_lighting_entities_and_discovery(monkeypatch):
    class Base:
        def __init__(self, c, identifier, name):
            self.coordinator = c
            self.unique_id = identifier
        def async_write_ha_state(self):
            pass
    class Light:
        pass
    class Select:
        pass
    class Error(Exception):
        def __init__(self, *args, **kwargs):
            super().__init__(*args)
            self.__dict__.update(kwargs)
    class Feature(IntFlag):
        EFFECT = 4
    for name, values in {
        "homeassistant": {}, "homeassistant.components": {},
        "homeassistant.components.light": {
            "LightEntity": Light,
            "ColorMode": types.SimpleNamespace(RGB="rgb", ONOFF="onoff"),
            "LightEntityFeature": Feature,
        },
        "homeassistant.components.select": {"SelectEntity": Select}, "homeassistant.exceptions": {"HomeAssistantError": Error},
        "what_under_test.entity": {"WhatEntity": Base},
    }.items():
        module = types.ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    modules = ["what_under_test.icue_lighting", "what_under_test.icue_lighting_light", "what_under_test.icue_lighting_select"]
    for name in modules:
        monkeypatch.delitem(sys.modules, name, raising=False)
    try:
        base, lights, selects = [importlib.import_module(name) for name in modules]
        async def run():
            callbacks = []
            entities = []
            coordinator = types.SimpleNamespace(data={"icue_automation": copy.deepcopy(AUTO) | {"lighting": copy.deepcopy(LIGHTING)}}, last_update_success=True,
                client=types.SimpleNamespace(command=AsyncMock()), async_add_listener=lambda cb: callbacks.append(cb))
            entry = types.SimpleNamespace(runtime_data=coordinator, async_on_unload=lambda cb: None)
            base.discover_lighting(
                entry,
                entities.extend,
                lambda c: [lights.LightingLinkLight(c), lights.LightingLinkLight(c, 2)],
            )
            assert len(entities) == 2
            light, secondary = entities
            assert light.available and light.is_on and light.effect == "Colour wave"
            assert light.brightness == 96 and light.raw_rgb == (32, 64, 96)
            await light.async_turn_on(rgb_color=(255, 0, 0), brightness=128)
            coordinator.client.command.assert_awaited_with(
                "icue_lighting",
                {
                    "effect": "ColorWave",
                    "expected_effect": "ColorWave",
                    "parameters": {"Color1": "#800000"},
                },
            )
            assert light.raw_rgb == (32, 64, 96) # No optimistic state.
            await secondary.async_turn_on(rgb_color=(0, 255, 0), brightness=64)
            coordinator.client.command.assert_awaited_with(
                "icue_lighting",
                {
                    "effect": "ColorWave",
                    "expected_effect": "ColorWave",
                    "parameters": {"Color2": "#004000"},
                },
            )
            speed = selects.LightingLinkSelect(coordinator, "Speed")
            assert speed.available and speed.options == ["0", "1", "2"] and speed.current_option == "1"
            await speed.async_select_option("2")
            coordinator.client.command.assert_awaited_with(
                "icue_lighting",
                {"expected_effect": "ColorWave", "parameters": {"Speed": 2}},
            )
            with pytest.raises(Error):
                await speed.async_select_option("3")
            await light.async_turn_on(effect="Rainbow wave")
            coordinator.client.command.assert_awaited_with("icue_lighting", {"effect": "RainbowWave"})
            coordinator.data["icue_automation"]["lighting"] |= {"effect": "RainbowWave", "parameters": {"Speed": 0, "Direction": 1}}
            assert not secondary.available and light.supported_color_modes == {"onoff"}
            with pytest.raises(Error):
                await secondary.async_turn_on(rgb_color=(0, 0, 0))
            await light.async_turn_on(rgb_color=(255, 255, 255), brightness=255)
            coordinator.client.command.assert_awaited_with(
                "icue_lighting",
                {"effect": "StaticColor", "parameters": {"Color1": "#ffffff"}},
            )
            await light.async_turn_off()
            coordinator.client.command.assert_awaited_with("icue_lighting", {"stop": True})
            coordinator.data["icue_automation"]["lighting"] |= {"effect": None, "parameters": {}}
            assert not light.is_on and not speed.available
            coordinator.data["icue_automation"]["control_enabled"] = False
            with pytest.raises(Error):
                await light.async_turn_off()
            coordinator.data["icue_automation"]["lighting"] = {}
            callbacks[0]()
            coordinator.data["icue_automation"]["lighting"] = copy.deepcopy(LIGHTING)
            callbacks[0]()
            assert len(entities) == 2
        asyncio.run(run())
    finally:
        for name in modules:
            sys.modules.pop(name, None)
