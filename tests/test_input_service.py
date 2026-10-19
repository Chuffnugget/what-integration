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
        "homeassistant.exceptions": {"HomeAssistantError": Error, "Unauthorized": Error}, "homeassistant.helpers": {},
        "homeassistant.helpers.config_validation": {"string": str, "ensure_list": lambda x: x if isinstance(x, list) else [x]},
        "homeassistant.helpers.device_registry": {"async_get": lambda hass: hass.registry},
    }.items():
        m = types.ModuleType(name)
        m.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, m)
    name = "what_under_test.input_service"
    monkeypatch.delitem(sys.modules, name, raising=False)
    module = importlib.import_module(name)
    yield module
    sys.modules.pop(name, None)


@pytest.mark.parametrize(
    "data",
    [{"keys": ["BOGUS"]}, {"keys": ["CTRL", "CTRL"]}, {"text": "x" * 1025}, {"keys": ["A"], "text": "hi"}, {}],
)
def test_bad_keyboard(service, data):
    with pytest.raises(vol.Invalid):
        service.KEYBOARD_SCHEMA({"device_id": "pc"} | data)

@pytest.mark.parametrize(
    "data",
    [{"x": 1.1}, {"x": True}, {"button": "hold"}, {"clicks": 2}, {"button": "left", "clicks": 3}],
)
def test_bad_mouse(service, data):
    with pytest.raises(vol.Invalid):
        service.MOUSE_SCHEMA({"device_id": "pc", "x": 0, "y": 0} | data)

def test_input_dispatch(service):
    async def run():
        client = types.SimpleNamespace(command=AsyncMock(return_value={}))
        c = types.SimpleNamespace(
            last_update_success=True,
            data={"controls": {"input_enabled": False}},
            client=client,
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
        service.register_input_services(hass)
        call = types.SimpleNamespace(
            service="send_keyboard",
            context=types.SimpleNamespace(user_id=None),
            data=service.KEYBOARD_SCHEMA({"device_id": "pc", "keys": ["ctrl", "s"]}),
        )
        with pytest.raises(service.HomeAssistantError):
            await handlers[call.service](call)
        client.command.assert_not_awaited()
        c.data["controls"]["input_enabled"] = True
        await handlers[call.service](call)
        client.command.assert_awaited_once_with("keyboard", {"keys": ["CTRL", "S"]})
        call.service = "send_mouse"
        call.data = service.MOUSE_SCHEMA({"device_id": "pc", "x": -100, "y": 500, "button": "left"})
        await handlers[call.service](call)
        client.command.assert_awaited_with("mouse", {"x": -100, "y": 500, "button": "left"})
        call.context.user_id = "non-admin"
        with pytest.raises(service.Unauthorized):
            await handlers[call.service](call)
        assert client.command.await_count == 2
        call.context.user_id = None
        client.command.side_effect = TimeoutError()
        with pytest.raises(service.HomeAssistantError):
            await handlers[call.service](call)
        assert client.command.await_count == 3
    asyncio.run(run())
