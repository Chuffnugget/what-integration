import asyncio
import copy
import importlib
import sys
import types
from unittest.mock import AsyncMock
import pytest
import voluptuous as vol
import test_protocol

@pytest.fixture
def service(monkeypatch):
    class Error(Exception):
        def __init__(self, *args, **kwargs):
            super().__init__(*args)
            self.__dict__.update(kwargs)
    class Entity:
        def __init__(self, coordinator, *args):
            self.coordinator = coordinator
    for name, values in {
        "homeassistant": {}, "homeassistant.components": {},
        "homeassistant.components.button": {"ButtonEntity": type("ButtonEntity", (), {})},
        "homeassistant.components.sensor": {"SensorEntity": type("SensorEntity", (), {})},
        "homeassistant.core": {"SupportsResponse": types.SimpleNamespace(OPTIONAL=1)},
        "homeassistant.exceptions": {"HomeAssistantError": Error, "Unauthorized": Error},
        "homeassistant.helpers": {},
        "homeassistant.helpers.config_validation": {"string": str, "ensure_list": lambda x: x if isinstance(x, list) else [x]},
        "homeassistant.helpers.device_registry": {"async_get": lambda hass: hass.registry},
        "what_under_test.entity": {"WhatEntity": Entity},
    }.items():
        module = types.ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    name = "what_under_test.wallpaper"
    monkeypatch.delitem(sys.modules, name, raising=False)
    module = importlib.import_module(name)
    yield module
    sys.modules.pop(name, None)

def test_metadata_validation():
    frame = copy.deepcopy(test_protocol.SNAPSHOT)
    frame["wallpaper"] = {"enabled": True, "running": True, "current_wallpaper": "C:/wallpapers/project.json"}
    assert test_protocol.validate_message(frame, "snapshot")["wallpaper"]["running"]
    for value in ({"enabled": 1}, {"current_wallpaper": 123}, {"status": "x" * 8193}, []):
        frame["wallpaper"] = value
        with pytest.raises(test_protocol.ProtocolError):
            test_protocol.validate_message(frame, "snapshot")

def test_schema(service):
    for op in service.OPERATIONS:
        assert service.SCHEMA({"device_id": "pc", "operation": op})["operation"] == op
    with pytest.raises(vol.Invalid):
        service.SCHEMA({"device_id": "pc", "operation": "run_command"})

def test_permission_dispatch_and_no_retry(service):
    async def run():
        client = types.SimpleNamespace(command=AsyncMock(return_value={"ok": True}))
        c = types.SimpleNamespace(
            last_update_success=True,
            data={"wallpaper": {"enabled": True, "running": True, "control_enabled": False}},
            client=client,
        )
        with pytest.raises(service.HomeAssistantError):
            await service.send(c, {"operation": "play"})
        client.command.assert_not_awaited()
        c.data["wallpaper"]["control_enabled"] = True
        status = service.WallpaperStatus(c)
        assert status.native_value == "Running"
        button = service.WallpaperButton(c, "play", "play")
        assert button.available
        await button.async_press()
        client.command.assert_awaited_once_with("wallpaper", {"operation": "play"}, timeout=12)
        c.data["wallpaper"]["busy"] = True
        assert not button.available
        client.command.side_effect = TimeoutError()
        with pytest.raises(service.HomeAssistantError):
            await service.send(c, {"operation": "pause"})
        assert client.command.await_count == 2
    asyncio.run(run())

def test_service_admin_and_target(service):
    async def run():
        c = types.SimpleNamespace(
            last_update_success=True,
            data={"wallpaper": {"enabled": True, "running": True, "control_enabled": True}},
            client=types.SimpleNamespace(command=AsyncMock(return_value={})),
        )
        entry = types.SimpleNamespace(domain="what", runtime_data=c)
        handlers = {}
        hass = types.SimpleNamespace(
            services=types.SimpleNamespace(
                async_register=lambda domain, name, handler, **kw: handlers.update({name: handler}),
            ),
            registry=types.SimpleNamespace(
                async_get=lambda id: types.SimpleNamespace(config_entries={"entry"}),
            ),
            config_entries=types.SimpleNamespace(async_get_entry=lambda id: entry),
            auth=types.SimpleNamespace(
                async_get_user=AsyncMock(return_value=types.SimpleNamespace(is_admin=False)),
            ),
        )
        service.register_wallpaper_service(hass)
        call = types.SimpleNamespace(
            context=types.SimpleNamespace(user_id="non-admin"),
            data=service.SCHEMA(
                {
                    "device_id": "pc",
                    "operation": "openPlaylist",
                    "options": {"playlist": "Night", "monitor": 1},
                },
            ),
        )
        with pytest.raises(service.Unauthorized):
            await handlers["wallpaper_engine"](call)
        c.client.command.assert_not_awaited()
        call.context.user_id = None
        await handlers["wallpaper_engine"](call)
        c.client.command.assert_awaited_once_with(
            "wallpaper",
            {"operation": "openPlaylist", "playlist": "Night", "monitor": 1},
            timeout=12,
        )
    asyncio.run(run())

def test_structured_fields_and_current_wallpaper_privacy(service):
    parsed = service.SCHEMA(
        {
            "device_id": "pc",
            "operation": "openWallpaper",
            "file": "C:/wallpapers/example.json",
            "monitor": 0,
        },
    )
    assert parsed["file"] == "C:/wallpapers/example.json"
    assert parsed["monitor"] == 0
    with pytest.raises(vol.Invalid):
        service.SCHEMA({"device_id": "pc", "operation": "openWallpaper", "monitor": -1})
    state = {
        "enabled": True,
        "running": True,
        "share_path": True,
        "current_wallpaper": "C:/wallpapers/example.json",
    }
    coordinator = types.SimpleNamespace(last_update_success=True, data={"wallpaper": state})
    entity = service.WallpaperCurrent(coordinator)
    assert entity.available and entity.native_value == "example.json"
    assert entity.extra_state_attributes["path"] == state["current_wallpaper"]
    state["share_path"] = False
    assert not entity.available
    assert entity.extra_state_attributes == {}
