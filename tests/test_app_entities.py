"""Parent app entities use reported state and never grant their own permissions."""
import asyncio
import copy
import importlib
import sys
import types
from enum import IntFlag
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import test_protocol


@pytest.fixture
def platforms(monkeypatch):
    class Base:
        def __init__(self, coordinator, identifier, name):
            self.coordinator = coordinator
            self._attr_unique_id = "pc_" + identifier
            self.writes = 0

        def async_write_ha_state(self):
            self.writes += 1

    class Entity:
        pass

    class UpdateFeature(IntFlag):
        INSTALL = 1

    classes = types.SimpleNamespace(**{key: key.lower() for key in (
        "TEMPERATURE", "VOLTAGE", "CURRENT", "POWER", "FREQUENCY", "DATA_SIZE",
        "DATA_RATE", "DURATION", "TIMESTAMP", "ENUM",
    )})
    ignore = lambda *args, **kwargs: None
    definitions = {
        "homeassistant": {}, "homeassistant.components": {},
        "homeassistant.const": {"EntityCategory": types.SimpleNamespace(CONFIG="config", DIAGNOSTIC="diagnostic")},
        "homeassistant.core": {"callback": lambda fn: fn},
        "homeassistant.components.sensor": {"SensorEntity": Entity, "SensorDeviceClass": classes,
                                             "SensorStateClass": types.SimpleNamespace(MEASUREMENT="measurement")},
        "homeassistant.components.number": {"NumberEntity": Entity, "NumberMode": types.SimpleNamespace(BOX="box")},
        "homeassistant.components.switch": {"SwitchEntity": Entity},
        "homeassistant.components.button": {"ButtonEntity": Entity},
        "homeassistant.components.update": {"UpdateEntity": Entity, "UpdateEntityFeature": UpdateFeature},
        "what_under_test.entity": {"WhatEntity": Base},
        "what_under_test.beacn": {"discover_beacn": ignore},
        "what_under_test.icue": {"IcueEntity": Base, "discover": ignore},
        "what_under_test.icue_lighting": {"LightingEntity": Base, "discover_lighting": ignore},
        "what_under_test.displays": {"discover_displays": ignore},
    }
    for name, values in definitions.items():
        module = types.ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    names = ["app_entity", "control_entity", "sensor", "number", "switch", "button", "update"]
    loaded = {}
    for name in names:
        full = "what_under_test." + name
        monkeypatch.delitem(sys.modules, full, raising=False)
        loaded[name] = importlib.import_module(full)
    yield types.SimpleNamespace(**loaded)
    for name in names:
        sys.modules.pop("what_under_test." + name, None)


def coordinator():
    return types.SimpleNamespace(
        data={"app": copy.deepcopy(test_protocol.APP)}, readings={},
        last_update_success=True, changed_modules={"app"}, force_entity_update=False,
        client=types.SimpleNamespace(commands=True), async_app_command=AsyncMock(),
        last_seen=datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc),
    )


def test_controls_require_permission_and_use_reported_values(platforms):
    c = coordinator()
    logging = platforms.switch.DiagnosticLogging(c)
    rate = platforms.number.PublishingInterval(c)
    restart = platforms.button.RestartWhat(c)
    assert not logging.available and not rate.available and not restart.available
    c.data["app"]["control_enabled"] = True
    assert all(entity.available for entity in (logging, rate, restart))
    assert rate.native_value == 1000 and rate.native_min_value == 100
    c.data["app"]["high_interval_ms"] = 500
    assert rate.native_min_value == 500

    async def run():
        await logging.async_turn_on()
        c.async_app_command.assert_awaited_with("app_logging", True)
        assert logging.is_on is False  # Do not assume the PC accepted the command.
        c.data["app"]["logging"] = True
        assert logging.is_on is True
        await rate.async_set_native_value(2500)
        c.async_app_command.assert_awaited_with("app_publish_interval", 2500)
        assert rate.native_value == 1000
        await restart.async_press()
        c.async_app_command.assert_awaited_with("app_restart", None)
    asyncio.run(run())
    c.data["app"]["restart_available"] = False
    assert not restart.available and rate.available
    c.client.commands = False
    assert not logging.available
    c.last_update_success = False
    assert not rate.available


def test_diagnostics_units_uptime_identity_and_disconnect(platforms):
    c = coordinator()
    entities = {key: platforms.sensor.AppDiagnostic(c, key, name)
                for key, name in platforms.sensor.APP_SENSORS.items()}
    assert entities["app_uptime"]._attr_unique_id == "pc_app_uptime"
    assert entities["app_uptime"].native_value == 120
    assert entities["app_memory"].native_value == 117.7
    assert entities["app_memory"]._attr_native_unit_of_measurement == "MiB"
    assert entities["app_cpu"].native_value == 3.5
    assert entities["app_last_seen"].native_value == c.last_seen
    assert "helpers excluded" in entities["app_cpu"].extra_state_attributes["measurement"]
    c.last_update_success = False
    assert not entities["app_cpu"].available
    assert entities["app_last_seen"].available and entities["app_last_seen"].native_value == c.last_seen


def test_app_updates_ignore_unrelated_sensor_deltas(platforms, monkeypatch):
    c = coordinator()
    cpu = platforms.sensor.AppDiagnostic(c, "app_cpu", "CPU")
    cpu._handle_coordinator_update()
    assert cpu.writes == 1
    c.changed_modules = {"beacn"}
    cpu._handle_coordinator_update()
    assert cpu.writes == 1
    c.last_update_success = False
    cpu._handle_coordinator_update()
    assert cpu.writes == 2
    seen = platforms.sensor.AppDiagnostic(c, "app_last_seen", "Last seen")
    c.last_update_success = True
    monkeypatch.setattr(platforms.sensor.time, "monotonic", lambda: 100)
    seen._handle_coordinator_update()
    seen._handle_coordinator_update()
    assert seen.writes == 1
    monkeypatch.setattr(platforms.sensor.time, "monotonic", lambda: 105)
    seen._handle_coordinator_update()
    assert seen.writes == 2
    c.last_update_success = False
    seen._handle_coordinator_update()
    assert seen.writes == 3


def test_update_starts_unknown_and_has_no_install_feature(platforms):
    c = coordinator()
    update = platforms.update.WhatUpdate(c)
    assert update.available and update.installed_version == "0.0.1-beta.16"
    assert update.latest_version is None and update.release_url is None
    assert update._attr_supported_features == 0
    c.data["app"]["update"].update(
        latest="0.0.1-beta.17", checked_at="2026-09-27T12:00:00Z",
        release_url="https://github.com/Chuffnugget/what-windows/releases/tag/v0.0.1-beta.17",
    )
    assert update.latest_version == "0.0.1-beta.17"
    assert update.extra_state_attributes["channel"] == "beta"
    c.last_update_success = False
    assert not update.available
