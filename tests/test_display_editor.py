import asyncio
import copy
import importlib
import sys
import types
from unittest.mock import AsyncMock
import pytest
import test_protocol
from test_displays import DISPLAYS, A, B
from what_under_test.displays_protocol import validate_layout, validate_displays


def rows():
    return [{k: m[k] for k in ('id', 'group', 'x', 'y', 'width', 'height', 'rotation', 'refresh')} for m in DISPLAYS['monitors']]

@pytest.mark.parametrize(
    'change',
    [lambda r: r.clear(), lambda r: r.append(r[0]), lambda r:r[0].update(x=True), lambda r:r[0].update(refresh=float('nan')), lambda r:r[0].update(width=0), lambda r:r[0].update(rotation=5), lambda r:r[0].update(command='bad')],
)
def test_bad_layout(change):
    value = rows()
    change(value)
    with pytest.raises(ValueError):
        validate_layout(value)

def test_modes_metadata():
    data = copy.deepcopy(DISPLAYS)
    data['layout_editor'] = True
    data['monitors'][0]['modes'] = [{'width': 1920, 'height': 1080, 'refresh': 60}]
    validate_displays(data, ValueError)
    data['monitors'][0]['modes'][0]['refresh'] = float('inf')
    with pytest.raises(ValueError):
        validate_displays(data, ValueError)

@pytest.fixture
def service(monkeypatch):
    class Error(Exception):
        def __init__(self, *args, **kwargs):
            super().__init__(*args)
            self.__dict__.update(kwargs)
    for name, values in {
        'homeassistant': {},
        'homeassistant.core': {'SupportsResponse': types.SimpleNamespace(OPTIONAL=1)},
        'homeassistant.exceptions': {'HomeAssistantError': Error, 'Unauthorized': Error},
        'homeassistant.helpers': {},
        'homeassistant.helpers.entity_registry': {'async_get': lambda hass: hass.registry},
    }.items():
        module = types.ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    name = 'what_under_test.display_service'
    monkeypatch.delitem(sys.modules, name, raising=False)
    module = importlib.import_module(name)
    yield module
    sys.modules.pop(name, None)

def test_display_service_dispatch(service):
    async def run():
        handlers = {}
        client = types.SimpleNamespace(command=AsyncMock(return_value={'ok': True}))
        coordinator = types.SimpleNamespace(
            data={'displays': copy.deepcopy(DISPLAYS) | {'layout_editor': True}},
            last_update_success=True,
            client=client,
        )
        entry = types.SimpleNamespace(domain='what', runtime_data=coordinator)
        entity = types.SimpleNamespace(
            platform='what',
            unique_id='pc_windows_displays_arrangement',
            config_entry_id='entry',
        )
        hass = types.SimpleNamespace(
            registry=types.SimpleNamespace(async_get=lambda id: entity),
            config_entries=types.SimpleNamespace(async_get_entry=lambda id: entry),
            auth=types.SimpleNamespace(
                async_get_user=AsyncMock(return_value=types.SimpleNamespace(is_admin=True)),
            ),
            services=types.SimpleNamespace(
                async_register=lambda domain, name, handler, **kwargs: handlers.update({name: handler}),
            ),
        )
        service.register_display_service(hass)
        data = service.SCHEMA(
            {'entity_id': 'sensor.pc_display_arrangement', 'layout': rows(), 'expected': rows()},
        )
        call = types.SimpleNamespace(data=data, context=types.SimpleNamespace(user_id='owner'))
        result = await handlers['apply_display_layout'](call)
        assert result == {'result': {'ok': True}}
        client.command.assert_awaited_once_with(
            'display',
            {'operation': 'apply', 'layout': rows(), 'expected': rows()},
            timeout=30,
        )
        hass.auth.async_get_user.return_value.is_admin = False
        with pytest.raises(service.Unauthorized):
            await handlers['apply_display_layout'](call)
        hass.auth.async_get_user.return_value.is_admin = True
        for key, value in [('control_enabled', False), ('layout_editor', False), ('busy', True), ('pending', True)]:
            old = coordinator.data['displays'][key]
            coordinator.data['displays'][key] = value
            with pytest.raises(service.HomeAssistantError):
                await handlers['apply_display_layout'](call)
            coordinator.data['displays'][key] = old
        entity.platform = 'other'
        with pytest.raises(service.HomeAssistantError):
            await handlers['apply_display_layout'](call)
        assert client.command.await_count == 1
        entity.platform = 'what'
        client.command.side_effect = TimeoutError()
        with pytest.raises(service.HomeAssistantError):
            await handlers['apply_display_layout'](call)
        assert client.command.await_count == 2
    asyncio.run(run())
