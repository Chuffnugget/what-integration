"""A real HA core with only the Windows endpoint and entry setup simulated."""
from pathlib import Path
import shutil
from types import MappingProxyType
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("homeassistant")
import pytest_asyncio
from homeassistant import loader
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers import frame as ha_frame, issue_registry as ir


PAIRING = {"host": "192.0.2.10", "port": 9435, "token": "a" * 64, "fingerprint": "00" * 32}
HELLO = {"device_id": "fixture-pc", "name": "Fixture PC", "app_version": "0.0.1-beta.17"}


@pytest_asyncio.fixture
async def flow_hass(tmp_path):
    root = Path(__file__).resolve().parents[2]
    shutil.copytree(root / "custom_components", tmp_path / "custom_components",
                    ignore=shutil.ignore_patterns("__pycache__"))
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    loader.async_setup(hass)
    ha_frame.async_setup(hass)
    await hass.config_entries.async_initialize()
    dr.async_setup(hass)
    await dr.async_load(hass)
    await er.async_load(hass)
    await ir.async_load(hass)
    # Flow tests do not need HTTP listeners or entity platforms. The separate
    # lifecycle tests load the complete integration and all its dependencies.
    hass.config.components.update({"http", "frontend"})
    hass.config.components.add("mqtt")
    integration = await loader.async_get_integration(hass, "what")
    await integration.async_get_component()
    with patch.object(hass.config_entries, "async_setup", AsyncMock(return_value=True)):
        try:
            yield hass
        finally:
            await hass.async_stop()


@pytest_asyncio.fixture
async def paired_entry(flow_hass):
    entry = ConfigEntry(
        domain="what", title="My PC", unique_id=HELLO["device_id"],
        version=1, minor_version=1, data=dict(PAIRING),
        options={"remote_enabled": True, "mqtt_enabled": True}, source="user",
        discovery_keys=MappingProxyType({}), subentries_data=[],
    )
    await flow_hass.config_entries.async_add(entry)
    return entry


@pytest.fixture
def pc_client(flow_hass):
    with (
        patch("custom_components.what.config_flow.async_get_clientsession"),
        patch("custom_components.what.config_flow.WhatClient") as factory,
    ):
        factory.return_value.connect = AsyncMock(return_value=(dict(HELLO), {}))
        factory.return_value.close = AsyncMock()
        yield factory
