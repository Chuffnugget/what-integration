import asyncio
import hashlib
import importlib
import sys
import types
from unittest.mock import AsyncMock, Mock
import pytest
import test_protocol

@pytest.fixture
def frontend(monkeypatch):
    add = Mock()
    class StaticPath:
        def __init__(self, url, path, cache):
            self.url_path = url
            self.path = path
            self.cache_headers = cache
    for name, values in {
        'homeassistant': {},
        'homeassistant.components': {},
        'homeassistant.components.frontend': {'add_extra_js_url': add},
        'homeassistant.components.http': {'StaticPathConfig': StaticPath},
    }.items():
        module = types.ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    name = 'what_under_test.frontend'
    monkeypatch.delitem(sys.modules, name, raising=False)
    module = importlib.import_module(name)
    yield module, add
    sys.modules.pop(name, None)

def test_automatic_module_once_and_cache_hash(frontend):
    module, add = frontend
    async def run():
        async def executor(fn):
            return fn()
        hass = types.SimpleNamespace(
            data={},
            async_add_executor_job=executor,
            http=types.SimpleNamespace(async_register_static_paths=AsyncMock()),
        )
        await module.async_setup_frontend(hass)
        await module.async_setup_frontend(hass)
        expected = module.CARD_URL + '?v=' + hashlib.sha256(module.CARD_PATH.read_bytes()).hexdigest()[:16]
        add.assert_called_once_with(hass, expected)
        hass.http.async_register_static_paths.assert_awaited_once()
        static = hass.http.async_register_static_paths.call_args.args[0][0]
        assert static.url_path == module.CARD_URL and not static.cache_headers
        assert hass.data[module.DATA_KEY] == expected
    asyncio.run(run())

def test_missing_file_does_not_break_pc_setup(frontend, monkeypatch, tmp_path):
    module, add = frontend
    monkeypatch.setattr(module, 'CARD_PATH', tmp_path / 'missing.js')
    async def run():
        async def executor(fn):
            return fn()
        hass = types.SimpleNamespace(
            data={},
            async_add_executor_job=executor,
            http=types.SimpleNamespace(async_register_static_paths=AsyncMock()),
        )
        await module.async_setup_frontend(hass)
        add.assert_not_called()
        hass.http.async_register_static_paths.assert_not_awaited()
        assert module.DATA_KEY not in hass.data
    asyncio.run(run())
