"""Service routing/schema tests with narrow HA boundary doubles."""
import asyncio
import importlib
import sys
import types
from unittest.mock import AsyncMock
import pytest
import voluptuous as vol
import test_protocol  # creates the isolated integration package


@pytest.fixture
def service(monkeypatch):
    class ServiceError(Exception):
        def __init__(self, *args, **kwargs):
            super().__init__(*args)
            self.__dict__.update(kwargs)
    class Denied(Exception):
        pass
    modules = {
        "homeassistant": {},
        "homeassistant.core": {"SupportsResponse": types.SimpleNamespace(OPTIONAL=1)},
        "homeassistant.exceptions": {"HomeAssistantError": ServiceError, "Unauthorized": Denied},
        "homeassistant.helpers": {},
        "homeassistant.helpers.config_validation": {"ensure_list": lambda x: x if isinstance(x, list) else [x], "string": str, "boolean": vol.Boolean()},
        "homeassistant.helpers.device_registry": {"async_get": lambda hass: hass.registry},
    }
    for name, values in modules.items():
        module = types.ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(sys.modules, "what_under_test.services", raising=False)
    module = importlib.import_module("what_under_test.services")
    yield module
    sys.modules.pop("what_under_test.services", None)


def test_schema_requires_exactly_one_device_and_bounded_timeout(service):
    data = {"device_id": "pc", "shell": "cmd", "command": "echo hello"}
    assert service.SCHEMA(data)["device_id"] == ["pc"]
    assert service.SCHEMA(data | {"leave_running": True})["leave_running"] is True
    assert "leave_running" not in service.SCHEMA(data)
    for change in ({"device_id": []}, {"device_id": ["a", "b"]}, {"timeout": 301}, {"shell": "bash"}, {"command": "x" * 2049}):
        with pytest.raises(vol.Invalid):
            service.SCHEMA(data | change)


@pytest.mark.parametrize("leave_running", [False, True])
def test_routes_only_to_enabled_pc_and_returns_result(service, leave_running):
    async def run():
        response = {
            "stdout": "hello",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "cancelled": False,
            "truncated": False,
        }
        client = types.SimpleNamespace(command=AsyncMock(return_value={"result": response}))
        coordinator = types.SimpleNamespace(
            last_update_success=True,
            data={"controls": {"shell_enabled": False}},
            client=client,
        )
        entry = types.SimpleNamespace(domain="what", runtime_data=coordinator)
        callbacks = {}
        hass = types.SimpleNamespace(
            services=types.SimpleNamespace(
                async_register=lambda domain, name, handler, **kw: callbacks.update(handler=handler),
            ),
            registry=types.SimpleNamespace(
                async_get=lambda identifier: types.SimpleNamespace(config_entries={"entry"}),
            ),
            config_entries=types.SimpleNamespace(async_get_entry=lambda identifier: entry),
            auth=types.SimpleNamespace(
                async_get_user=AsyncMock(return_value=types.SimpleNamespace(is_admin=False)),
            ))
        service.register_services(hass)
        call = types.SimpleNamespace(
            context=types.SimpleNamespace(user_id=None),
            data=service.SCHEMA(
                {"device_id": "pc", "shell": "cmd", "command": "echo hello", "timeout": 90, "leave_running": leave_running},
            ),
        )
        with pytest.raises(service.HomeAssistantError):
            await callbacks["handler"](call)
        client.command.assert_not_awaited()
        coordinator.data["controls"]["shell_enabled"] = True
        assert await callbacks["handler"](call) == response
        client.command.assert_awaited_once_with(
            "run_command",
            {"shell": "cmd", "command": "echo hello", "timeout": 90, "leave_running": leave_running},
            timeout=105,
        )
        call.context.user_id = "non-admin"
        with pytest.raises(service.Unauthorized):
            await callbacks["handler"](call)
        assert client.command.await_count == 1
    asyncio.run(run())
