import asyncio
import importlib
import sys
import types
from unittest.mock import AsyncMock
import pytest
from test_protocol import lighting_snapshot


def test_dynamic_lighting_entities(monkeypatch):
    class Base:
        def __init__(self, coordinator, identifier, name):
            self.coordinator = coordinator
            self.unique_id = identifier
        def async_write_ha_state(self):
            pass
    class Light:
        pass
    class Error(Exception):
        def __init__(self, *args, **kwargs):
            super().__init__(*args)
            self.__dict__.update(kwargs)
    for name, values in {
        "homeassistant": {}, "homeassistant.components": {},
        "homeassistant.components.light": {"LightEntity": Light, "ColorMode": types.SimpleNamespace(RGB="rgb")},
        "homeassistant.exceptions": {"HomeAssistantError": Error},
        "what_under_test.entity": {"WhatEntity": Base},
    }.items():
        module = types.ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    name = "what_under_test.dynamic_lighting_light"
    monkeypatch.delitem(sys.modules, name, raising=False)
    try:
        lighting = importlib.import_module(name)
        async def run():
            callbacks, entities = [], []
            coordinator = types.SimpleNamespace(data=lighting_snapshot(), last_update_success=True,
                client=types.SimpleNamespace(command=AsyncMock()), async_add_listener=lambda cb: callbacks.append(cb))
            entry = types.SimpleNamespace(runtime_data=coordinator, async_on_unload=lambda cb: None)
            lighting.discover_dynamic_lighting(entry, entities.extend)
            assert len(entities) == 1
            light = entities[0]
            assert light.available and light.is_on
            assert light.rgb_color == (1, 2, 255) and light.brightness == 191
            callbacks[0]()
            assert len(entities) == 1
            await light.async_turn_on(rgb_color=(255, 10, 20), brightness=128)
            coordinator.client.command.assert_awaited_with("dynamic_lighting", {
                "device": light.lighting_id, "operation": "set", "rgb": [255, 10, 20], "brightness": 50.2})
            assert light.rgb_color == (1, 2, 255)  # Wait for confirmed state, never optimistic.
            await light.async_turn_off()
            assert coordinator.client.command.call_args.args[1]["brightness"] == 0
            coordinator.data["dynamic_lighting"]["devices"][0].update(rgb=None, brightness=None)
            assert light.is_on is None and light.rgb_color is None
            coordinator.data["dynamic_lighting"]["control_enabled"] = False
            assert not light.available
            with pytest.raises(Error):
                await light.async_turn_on()
            coordinator.data["dynamic_lighting"]["devices"] = []
            assert not light.available
        asyncio.run(run())
    finally:
        sys.modules.pop(name, None)
