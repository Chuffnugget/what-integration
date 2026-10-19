"""Power-mode commands work with old label-based and new key-based apps."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("power, expected", [
    ({"modes": {"ac": "Balanced"}}, "Best performance"),
    ({"mode_keys": {"ac": "balanced"}}, "performance"),
    ({"modes": {"ac": "Other label"}, "mode_keys": {"ac": "balanced"}}, "performance"),
])
async def test_power_mode_compatibility(flow_hass, power, expected):
    from custom_components.what.select import PowerMode

    entity = PowerMode.__new__(PowerMode)
    entity.supply = "ac"
    entity.coordinator = SimpleNamespace(data={"controls": {"power": power}})
    entity.command = AsyncMock()
    assert entity.current_option == "Balanced"
    await entity.async_select_option("Best performance")
    entity.command.assert_awaited_once_with("power_mode_ac", expected)
