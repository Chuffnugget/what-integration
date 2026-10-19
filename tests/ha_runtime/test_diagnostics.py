"""Recovery notices and exported diagnostics must remain useful and private."""
import json
from types import SimpleNamespace
import time
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant.helpers import issue_registry as ir

from conftest import HELLO, PAIRING

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("alternate_enabled", [False, True])
async def test_certificate_failure_creates_repair_without_blocking_alternates(
    flow_hass, paired_entry, alternate_enabled,
):
    from homeassistant.exceptions import ConfigEntryNotReady
    from custom_components.what.coordinator import WhatCoordinator

    flow_hass.config_entries.async_update_entry(
        paired_entry, options={"remote_enabled": alternate_enabled},
    )
    with patch("custom_components.what.coordinator.async_get_clientsession", return_value=None):
        coordinator = WhatCoordinator(flow_hass, paired_entry)
    coordinator.client.connect = AsyncMock(side_effect=aiohttp.ServerFingerprintMismatch(
        b"old", b"new", "private-host", 1234,
    ))
    if alternate_enabled:
        await coordinator.async_connect()
        assert coordinator.hello["device_id"] == paired_entry.unique_id
        assert not coordinator.last_update_success
    else:
        with pytest.raises(ConfigEntryNotReady):
            await coordinator.async_connect()
        assert coordinator.data is None
    assert ir.async_get(flow_hass).async_get_issue(
        "what", f"{paired_entry.entry_id}_local_certificate_mismatch",
    )


async def test_repairs_are_route_specific_and_bounded(flow_hass, paired_entry):
    from custom_components.what import async_remove_entry
    from custom_components.what.connection_health import ConnectionHealth
    from custom_components.what.protocol import IncompatibleProtocolError

    health = ConnectionHealth(flow_hass, paired_entry)
    registry = ir.async_get(flow_hass)
    local_issue = f"{paired_entry.entry_id}_local_certificate_mismatch"
    health.failed("local", aiohttp.ServerFingerprintMismatch(b"old", b"new", "private-host", 1234))
    assert registry.async_get_issue("what", local_issue) is not None
    health.recovered("mqtt")
    assert registry.async_get_issue("what", local_issue) is not None
    health.recovered("local")
    assert registry.async_get_issue("what", local_issue) is None

    for _ in range(30):
        health.failed("remote", TimeoutError("private-error"))
    assert health.failures[-1]["count"] == 30
    assert not registry.issues
    for _ in range(15):
        health.failed("mqtt", IncompatibleProtocolError("private-protocol-error"))
        health.failed("local", ConnectionError("private-host"))
    assert len(health.failures) == 12
    assert "private" not in json.dumps(health.diagnostics())
    assert registry.async_get_issue("what", f"{paired_entry.entry_id}_mqtt_protocol_mismatch")
    await async_remove_entry(flow_hass, paired_entry)
    assert not registry.issues


async def test_diagnostics_before_setup(flow_hass, paired_entry):
    from custom_components.what.diagnostics import async_get_config_entry_diagnostics

    report = await async_get_config_entry_diagnostics(flow_hass, paired_entry)
    assert report["snapshot_available"] is False
    assert report["connected"] is False
    assert report["integration_version"]
    assert report["home_assistant_version"]
    assert PAIRING["token"] not in json.dumps(report)


async def test_diagnostics_include_modules_without_private_payloads(flow_hass, paired_entry):
    from custom_components.what.connection_health import ConnectionHealth
    from custom_components.what.diagnostics import async_get_config_entry_diagnostics, MODULES

    secret = "PRIVATE-DIAGNOSTIC-FIXTURE"
    health = ConnectionHealth(flow_hass, paired_entry)
    health.failed("local", ConnectionError(secret))
    coordinator = SimpleNamespace(
        entry=paired_entry, health=health, last_update_success=True, transport="mqtt",
        last_snapshot_received=time.monotonic(), hello=dict(HELLO),
        reading_counts={"published_entities": 2, "unavailable_readings": 1, "beacn_unavailable_controls": 0},
        readings={
            "a": {"source": "hwinfo", "value": secret, "available": True, "name": secret},
            "b": {"source": "custom", "value": None, "available": False, "name": secret},
        },
        data={
            "healthy": True, "hwinfo_enabled": True,
            "beacn": {"enabled": True, "connected": True, "app_version": "1.2.3", "serial": secret},
            "icue": {"sdk_version": secret, "status": secret, "devices": [{"name": secret}]},
            "custom": {"cloud": False, "actions": [{"command": secret}]},
            "wallpaper": {"current_wallpaper": secret},
            "controls": {"permissions": {"media": True, "urls": False, secret: True}, "shell_enabled": False},
            "app": {"cpu_percent": 1.0, "memory_bytes": 1000, "release_url": secret},
            "notifications": {"events": [{"reply": secret}]},
        },
    )
    paired_entry.runtime_data = coordinator
    report = await async_get_config_entry_diagnostics(flow_hass, paired_entry)
    encoded = json.dumps(report)
    for private in (secret, PAIRING["host"], PAIRING["token"], PAIRING["fingerprint"]):
        assert private not in encoded
    assert report["connection"]["connection_transport"] == "MQTT"
    assert set(report["modules"]) == set(MODULES)
    assert report["modules"]["hwinfo"]["readings"] == 1
    assert report["modules"]["custom"]["unavailable_readings"] == 1
    assert report["modules"]["beacn"]["versions"]["app_version"] == "1.2.3"
    assert report["modules"]["icue"]["versions"]["sdk_version"] is None
    assert report["modules"]["icue"]["devices_count"] == 1
    assert report["windows_permissions"] == {"media": True, "urls": False}
