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
        "homeassistant.exceptions": {"HomeAssistantError": Error}, "homeassistant.helpers": {},
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
