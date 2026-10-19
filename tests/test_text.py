"""URL commands must not publish an invalid or private text state."""
import asyncio
import importlib
import sys
import types
from unittest.mock import AsyncMock, Mock

import pytest
import test_protocol


def test_url_command_has_no_stored_state(monkeypatch):
    class Error(RuntimeError):
        def __init__(self, *args, **kwargs):
            super().__init__(*args)

    class Control:
        def __init__(self, *args):
            self.command = AsyncMock()
            self.async_write_ha_state = Mock()

    class Text:
        @property
        def state(self):
            value = self._attr_native_value
            if value is not None and len(value) < self._attr_native_min:
                raise ValueError("Text state is too short")
            return value

    for name, values in {
        "homeassistant.components.text": {"TextEntity": Text, "TextMode": types.SimpleNamespace(TEXT="text")},
        "homeassistant.exceptions": {"HomeAssistantError": Error},
        "what_under_test.control_entity": {"WindowsControlEntity": Control},
    }.items():
        module = types.ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(sys.modules, "what_under_test.text", raising=False)
    module = importlib.import_module("what_under_test.text")

    async def run():
        entity = module.LaunchURL(None)
        assert entity.state is None
        await entity.async_set_value("https://example.com/private-query")
        entity.command.assert_awaited_once_with("open_url", "https://example.com/private-query")
        assert entity.state is None
        with pytest.raises(RuntimeError):
            await entity.async_set_value("")
        assert entity.command.await_count == 1

    try:
        asyncio.run(run())
    finally:
        sys.modules.pop("what_under_test.text", None)
