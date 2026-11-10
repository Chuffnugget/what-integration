import asyncio
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
    for name, values in {
        "homeassistant": {}, "homeassistant.core": {"SupportsResponse": types.SimpleNamespace(OPTIONAL=1)},
        "homeassistant.exceptions": {"HomeAssistantError": Error, "Unauthorized": PermissionError}, "homeassistant.helpers": {},
        "homeassistant.helpers.config_validation": {"string": str, "ensure_list": lambda x: x if isinstance(x, list) else [x]},
        "homeassistant.helpers.device_registry": {"async_get": lambda hass: hass.registry},
    }.items():
        m = types.ModuleType(name)
        m.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, m)
    name = "what_under_test.notification_service"
    monkeypatch.delitem(sys.modules, name, raising=False)
    module = importlib.import_module(name)
    yield module
    sys.modules.pop(name, None)


@pytest.mark.parametrize("change", [{"title": "x" * 129}, {"message": "x" * 2001}, {"title": "a\nb"}, {"message": "\0"},
    {"title": "😀" * 65}, {"message": " "}, {"level": "critical"}, {"device_id": ["a", "b"]}, {"url": "https://example.com"},
    {"image": "file:///C:/secret"}, {"image": "https://user:password@example.com/picture.png"},
    {"actions": [{"id": "OPEN", "title": "Reserved"}]},
    {"actions": [{"id": "a", "title": "A"}, {"id": "a", "title": "B"}]},
    {"actions": [{"id": "a", "title": "A", "url": "javascript:alert(1)"}]},
    {"ttl": 0}, {"ttl": 3601}, {"ttl": 1.5}])
def test_notification_bounds(service, change):
    with pytest.raises(vol.Invalid):
        service.SCHEMA({"device_id": "pc", "message": "Hello"} | change)


def test_notification_service(service):
    async def run():
        client = types.SimpleNamespace(command=AsyncMock(return_value={"ok": True}))
        c = types.SimpleNamespace(
            last_update_success=True,
            data={"controls": {"notifications_enabled": False}},
            client=client,
        )
        entry = types.SimpleNamespace(domain="what", runtime_data=c)
        handlers = {}
        hass = types.SimpleNamespace(services=types.SimpleNamespace(
            async_register=lambda domain, name, handler, **kw: handlers.update({name: handler}),
        ),
            registry=types.SimpleNamespace(
                async_get=lambda id: types.SimpleNamespace(config_entries={"entry"}),
            ),
            config_entries=types.SimpleNamespace(async_get_entry=lambda id: entry))
        service.register_notification_service(hass)
        call = types.SimpleNamespace(
            context=types.SimpleNamespace(user_id=None),
            data=service.SCHEMA({"device_id": "pc", "message": "Line one\nLine two"}),
        )
        assert call.data["title"] == "Home Assistant"
        with pytest.raises(service.HomeAssistantError):
            await handlers["send_notification"](call)
        client.command.assert_not_awaited()
        c.data["controls"]["notifications_enabled"] = True
        assert (await handlers["send_notification"](call))["submitted"] is True
        client.command.assert_awaited_once_with(
            "notify",
            {"title": "Home Assistant", "message": "Line one\nLine two", "level": "information"},
            timeout=15,
        )
        client.command.side_effect = TimeoutError()
        with pytest.raises(service.HomeAssistantError):
            await handlers["send_notification"](call)
        assert client.command.await_count == 2 # No retry.
    asyncio.run(run())


@pytest.mark.parametrize("action", ["send_notification", "speak"])
@pytest.mark.parametrize("caller", ["readonly", "regular", "missing", "admin", "automation"])
def test_pc_services_authorize_user_before_dispatch(service, monkeypatch, action, caller):
    # These are plain services, not entity services: HA does not authorize their targets for us.
    for name, values in {
        "homeassistant.components": {"media_source": types.SimpleNamespace()},
        "homeassistant.components.media_player": {
            "MediaPlayerEntity": type("MediaPlayerEntity", (), {}),
            "MediaPlayerEntityFeature": types.SimpleNamespace(**{
                key: 1 << index for index, key in enumerate(
                    ["PLAY_MEDIA", "PLAY", "PAUSE", "STOP", "VOLUME_SET", "VOLUME_MUTE"])
            }),
            "MediaPlayerState": types.SimpleNamespace(),
            "async_process_play_media_url": lambda hass, url: url,
        },
        "homeassistant.helpers.network": {"get_url": lambda *a, **k: "https://example.invalid", "is_hass_url": lambda *a: False},
        "what_under_test.entity": {"WhatEntity": type("WhatEntity", (), {})},
    }.items():
        module = types.ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(sys.modules, "what_under_test.announcements", raising=False)
    speech = importlib.import_module("what_under_test.announcements")

    async def run():
        client = types.SimpleNamespace(command=AsyncMock(return_value={"ok": True}))
        coordinator = types.SimpleNamespace(last_update_success=True, client=client, data={
            "controls": {"notifications_enabled": True}, "announcements": {"enabled": True}})
        entry = types.SimpleNamespace(domain="what", runtime_data=coordinator)
        handlers = {}
        user = None if caller == "missing" else types.SimpleNamespace(is_admin=caller == "admin")
        hass = types.SimpleNamespace(
            auth=types.SimpleNamespace(async_get_user=AsyncMock(return_value=user)),
            services=types.SimpleNamespace(async_register=lambda d, n, h, **k: handlers.update({n: h})),
            registry=types.SimpleNamespace(async_get=lambda identifier: types.SimpleNamespace(config_entries={"entry"})),
            config_entries=types.SimpleNamespace(async_get_entry=lambda identifier: entry),
        )
        service.register_notification_service(hass)
        speech.register_speech_service(hass)
        schema = service.SCHEMA if action == "send_notification" else speech.SPEECH_SCHEMA
        call = types.SimpleNamespace(context=types.SimpleNamespace(user_id=None if caller == "automation" else caller),
            data=schema({"device_id": "pc", "message": "Isolated permission fixture"}))
        if caller not in ("admin", "automation"):
            with pytest.raises(PermissionError):
                await handlers[action](call)
            client.command.assert_not_awaited()
        else:
            await handlers[action](call)
            assert client.command.await_count == 1
            # Even administrators and internal automations still need PC-side opt-in.
            coordinator.data["controls"]["notifications_enabled"] = False
            coordinator.data["announcements"]["enabled"] = False
            with pytest.raises(service.HomeAssistantError):
                await handlers[action](call)
            assert client.command.await_count == 1
        if caller == "automation":
            hass.auth.async_get_user.assert_not_awaited()
        else:
            hass.auth.async_get_user.assert_awaited()
    try:
        asyncio.run(run())
    finally:
        sys.modules.pop("what_under_test.announcements", None)
