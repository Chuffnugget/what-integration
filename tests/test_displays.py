import asyncio
import copy
import importlib
import sys
import types
from unittest.mock import AsyncMock
import pytest
from test_protocol import SNAPSHOT
from what_under_test.protocol import validate_message, ProtocolError

A, B = "a" * 24, "b" * 24
LAYOUT = "12345678-1234-1234-1234-123456789abc"

def monitor(identifier, x=0):
    return {"id": identifier, "name": "Monitor " + identifier[-6:], "group": identifier, "connected": True, "enabled": True,
            "primary": x == 0, "mode": "extended", "width": 1920, "height": 1080, "x": x, "y": 0, "rotation": 1, "refresh": 60.0}

DISPLAYS = {"enabled": True, "available": True, "control_enabled": True, "busy": False, "pending": False, "status": "Connected",
            "monitors": [monitor(A), monitor(B, 1920)], "layouts": [{"id": LAYOUT, "name": "Desk", "available": True}], "active_layout": LAYOUT}

@pytest.mark.parametrize(
    "bad",
    [None, "boolean", "duplicate", "identity", "mode", "refresh", "geometry", "layout", "active", "name"],
)
def test_display_metadata(bad):
    data = copy.deepcopy(DISPLAYS)
    if bad == "boolean":
        data["enabled"] = 1
    if bad == "duplicate":
        data["monitors"].append(data["monitors"][0])
    if bad == "identity":
        data["monitors"][0]["id"] = "DISPLAY1"
    if bad == "mode":
        data["monitors"][0]["mode"] = "arbitrary"
    if bad == "refresh":
        data["monitors"][0]["refresh"] = float("nan")
    if bad == "geometry":
        data["monitors"][0]["x"] = False
    if bad == "layout":
        data["layouts"][0]["available"] = "false"
    if bad == "active":
        data["active_layout"] = "missing"
    if bad == "name":
        data["layouts"][0]["name"] = "x" * 65
    if bad:
        with pytest.raises(ProtocolError):
            validate_message(copy.deepcopy(SNAPSHOT) | {"displays": data}, "snapshot")
    else:
        assert validate_message(copy.deepcopy(SNAPSHOT) | {"displays": data}, "snapshot")["displays"] == data

@pytest.mark.parametrize("value", [[], {}, None, True, 12, [LAYOUT]])
def test_active_layout_requires_string(value):
    data = copy.deepcopy(DISPLAYS)
    data["active_layout"] = value
    with pytest.raises(ProtocolError):
        validate_message(copy.deepcopy(SNAPSHOT) | {"displays": data}, "snapshot")


def test_display_entities(monkeypatch):
    class Base:
        def __init__(self, c, identifier, name):
            self.coordinator = c
            self.unique_id = identifier
        def async_write_ha_state(self):
            pass
    class Select:
        pass
    class Button:
        pass
    class Error(Exception):
        def __init__(self, *args, **kwargs):
            super().__init__(*args)
            self.__dict__.update(kwargs)
    for name, values in {"homeassistant": {}, "homeassistant.components": {},
        "homeassistant.components.select": {"SelectEntity": Select}, "homeassistant.components.button": {"ButtonEntity": Button},
        "homeassistant.exceptions": {"HomeAssistantError": Error}, "what_under_test.entity": {"WhatEntity": Base}}.items():
        m = types.ModuleType(name)
        m.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, m)
    name = "what_under_test.displays"
    monkeypatch.delitem(sys.modules, name, raising=False)
    try:
        d = importlib.import_module(name)
        async def run():
            listeners = []
            entities = []
            c = types.SimpleNamespace(data={"displays": copy.deepcopy(DISPLAYS)}, last_update_success=True,
                client=types.SimpleNamespace(command=AsyncMock()), async_add_listener=lambda cb: listeners.append(cb))
            entry = types.SimpleNamespace(runtime_data=c, async_on_unload=lambda cb: None)
            d.discover_displays(entry, entities.extend, "select")
            assert len(entities) == 3
            listeners[0]()
            assert len(entities) == 3
            layout, first, second = entities
            writes = []
            first.async_write_ha_state = lambda: writes.append(True)
            c.changed_modules = {"displays"}
            first._handle_coordinator_update()
            c.changed_modules = set()
            first._handle_coordinator_update()
            assert len(writes) == 1  # Unrelated sensor deltas do not write display state.
            c.last_update_success = False
            first._handle_coordinator_update()
            assert len(writes) == 2  # Availability transitions must still propagate.
            c.last_update_success = True
            assert layout.current_option == "Desk" and first.current_option == "Extended"
            assert first.extra_state_attributes["rotation"] == 1
            await second.async_select_option("Mirror Monitor aaaaaa")
            c.client.command.assert_awaited_with(
                "display",
                {"operation": "mirror", "monitor": B, "source": A},
                timeout=30,
            )
            assert second.current_option == "Extended" # State changes only on confirmed readback.
            await layout.async_select_option("Desk")
            c.client.command.assert_awaited_with(
                "display",
                {"operation": "restore", "layout_id": LAYOUT},
                timeout=30,
            )
            c.data["displays"]["pending"] = True
            assert not second.available and not layout.available
            keep = d.DisplayRecovery(c, "keep")
            assert keep.available
            await keep.async_press()
            c.client.command.assert_awaited_with("display", {"operation": "keep"}, timeout=30)
            c.data["displays"]["control_enabled"] = False
            assert not keep.available
            with pytest.raises(Error):
                await keep.async_press()
            c.data["displays"] |= {"control_enabled": True, "pending": False}
            c.data["displays"]["monitors"] = [monitor(A)]
            assert not second.available and "Mirror Monitor bbbbbb" not in first.options
            c.data["displays"]["layouts"][0]["available"] = False
            assert not layout.available and not layout.options
            c.data["displays"]["enabled"] = False
            assert not first.available
        asyncio.run(run())
    finally:
        sys.modules.pop(name, None)
