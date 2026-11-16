"""Bundled cards register once, with independent cache versions and retries."""
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


def fake_hass():
    async def executor(fn):
        await asyncio.sleep(0)  # Exercise concurrent PC entry setup.
        return fn()

    return types.SimpleNamespace(
        data={}, async_add_executor_job=executor,
        http=types.SimpleNamespace(async_register_static_paths=AsyncMock()),
    )


def test_automatic_modules_once_and_cache_hash(frontend):
    module, add = frontend

    async def run():
        hass = fake_hass()
        await asyncio.gather(*(module.async_setup_frontend(hass) for _ in range(3)))
        assert set(module.CARD_FILES) == {'/what/display-layout-card.js', '/what/sensor-graph-card.js'}
        assert add.call_count == len(module.CARD_FILES)
        assert hass.http.async_register_static_paths.await_count == len(module.CARD_FILES)
        for route, path in module.CARD_FILES.items():
            expected = route + '?v=' + hashlib.sha256(path.read_bytes()).hexdigest()[:16]
            add.assert_any_call(hass, expected)
            assert hass.data[module.DATA_KEY][route] == expected
        for call in hass.http.async_register_static_paths.call_args_list:
            static, = call.args[0]
            assert not static.cache_headers
            assert str(module.CARD_FILES[static.url_path]) == static.path

    asyncio.run(run())


def test_missing_card_does_not_block_other_card_and_can_retry(frontend, monkeypatch, tmp_path):
    module, add = frontend
    present = tmp_path / 'present.js'
    missing = tmp_path / 'missing.js'
    present.write_text('// present', encoding='utf-8')
    monkeypatch.setattr(module, 'CARD_FILES', {'/first.js': missing, '/second.js': present})

    async def run():
        hass = fake_hass()
        await module.async_setup_frontend(hass)
        assert add.call_count == 1
        assert set(hass.data[module.DATA_KEY]) == {'/second.js'}
        missing.write_text('// restored', encoding='utf-8')
        await module.async_setup_frontend(hass)
        await module.async_setup_frontend(hass)
        assert add.call_count == 2
        assert hass.http.async_register_static_paths.await_count == 2
        assert set(hass.data[module.DATA_KEY]) == {'/first.js', '/second.js'}

    asyncio.run(run())
