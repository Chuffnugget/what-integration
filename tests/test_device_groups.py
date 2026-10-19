import importlib
import sys
import time
import types
import copy
import pytest
import test_protocol
from what_under_test.device_groups import module_for, device_name
from what_under_test.entity_cleanup import cleanup_candidates

@pytest.mark.parametrize(
    "identifier,module",
    [("connection", None), ("entity_cleanup", None), ("entity_cleanup_preview", None), ("entity_cleanup_remove", None), ("source_status", "hwinfo"), ("license", "hwinfo"), ("windows_problem", "windows"), ("command_lock", "windows"), ("beacn_volume_2", "beacn"), ("wallpaper_engine_play", "wallpaper"), ("icue_lighting_effect", "icue")],
)
def test_control_module(identifier, module):
    assert module_for(identifier, {}) == module

@pytest.mark.parametrize("source", ["windows", "hwinfo", "nvidia", "coretemp", "beacn", "icue"])
def test_reading_module(source):
    assert module_for("opaque", {"opaque": {"source": source}}) == source

def test_entity_identity_and_module_device(monkeypatch):
    class Base:
        def __init__(self, c):
            self.coordinator = c
    for name, values in {
        "homeassistant": {},
        "homeassistant.helpers": {},
        "homeassistant.helpers.device_registry": {"DeviceInfo": dict},
        "homeassistant.helpers.update_coordinator": {"CoordinatorEntity": Base},
    }.items():
        m = types.ModuleType(name)
        m.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, m)
    monkeypatch.delitem(sys.modules, "what_under_test.entity", raising=False)
    module = importlib.import_module("what_under_test.entity")
    try:
        c = types.SimpleNamespace(
            parent_device_id="ha-parent-id",
            entry=types.SimpleNamespace(unique_id="pc-stable"),
            hello={"name": "DESKTOP-PC", "app_version": "1"},
            readings={"cpu": {"source": "hwinfo"}},
        )
        entity = module.WhatEntity(c, "cpu", "CPU P-core 5 clock")
        assert entity._attr_unique_id == "pc-stable_cpu"
        assert entity._attr_device_info["identifiers"] == {("what", "pc-stable_module_hwinfo")}
        assert entity._attr_device_info["name"] == "WHAT · DESKTOP-PC · HWiNFO"
        assert entity._attr_device_info["via_device_id"] == "ha-parent-id"
        assert entity._attr_name == "CPU P-core 5 clock"
        from what_under_test.device_groups import MODULES
        for source, label in MODULES.items():
            c.readings = {"module_reading": {"source": source}}
            reading = module.WhatEntity(c, "module_reading", "Example reading")
            assert reading._attr_device_info["name"] == "WHAT · DESKTOP-PC · " + label
            assert reading._attr_name == "Example reading"

        beacn = module.WhatEntity(c, "beacn_mute_2", "BEACN Game mute")
        assert beacn._attr_name == "Game mute"
        assert device_name("DESKTOP-PC", "icue", "Keyboard") == "WHAT · DESKTOP-PC · iCUE · Keyboard"
    finally:
        sys.modules.pop("what_under_test.entity", None)

def coordinator():
    return types.SimpleNamespace(entry=types.SimpleNamespace(unique_id="pc", entry_id="entry"), last_update_success=True,
        last_snapshot_received=time.monotonic(), data={"selected_sensor_ids": ["keep", "unavailable", "disabled_module", "cloud_only"]})

def entity(identifier, domain="sensor", entry="entry", platform="what"):
    return types.SimpleNamespace(
        entity_id=domain + "." + identifier,
        unique_id="pc_" + identifier,
        config_entry_id=entry,
        platform=platform,
        domain=domain,
    )

def test_cleanup_preserves_selected_diagnostics_controls_and_other_pcs():
    c = coordinator()
    rows = [entity(v) for v in ("keep", "unavailable", "disabled_module", "cloud_only", "old", "source_status", "license", "beacn_status", "wallpaper_engine_status", "wallpaper_engine_current", "windows_displays_arrangement")]
    rows += [entity("connection", "binary_sensor"), entity("old_binary", "binary_sensor"), entity("other", entry="other"), entity("other", platform="other"), entity("led", "light"), entity("mute", "switch")]
    assert set(cleanup_candidates(c, rows)) == {"sensor.old", "binary_sensor.old_binary"}

@pytest.mark.parametrize("state", ["offline", "stale", "cloud"])
def test_cleanup_refuses_incomplete_or_stale_selection(state):
    c = coordinator()
    if state == "offline":
        c.last_update_success = False
    if state == "stale":
        c.last_snapshot_received -= 61
    if state == "cloud":
        c.data = {}
    with pytest.raises(ValueError):
        cleanup_candidates(c, [entity("old")])

@pytest.mark.parametrize("value", [None, {}, ["same", "same"], [""], [1], ["x" * 129]])
def test_bad_selection_catalogue(value):
    with pytest.raises(test_protocol.ProtocolError):
        test_protocol.validate_message(
            copy.deepcopy(test_protocol.SNAPSHOT) | {"selected_sensor_ids": value},
            "snapshot",
        )

def test_valid_selection_catalogue():
    frame = copy.deepcopy(test_protocol.SNAPSHOT) | {"selected_sensor_ids": ["a", "b"]}
    assert test_protocol.validate_message(frame, "snapshot")["selected_sensor_ids"] == ["a", "b"]


def test_new_diagnostics_stay_on_pc_and_survive_cleanup():
    from what_under_test.connection_info import CONNECTION_SENSORS
    for identifier in CONNECTION_SENSORS:
        assert module_for(identifier, {}) is None
    assert cleanup_candidates(coordinator(), [entity(key) for key in CONNECTION_SENSORS]) == []

def test_aux_cleanup_requires_explicit_retirement_and_preserves_active_controls():
    c = coordinator()
    rows = [entity("beacn_volume_5", "number"), entity("beacn_mute_6", "switch"), entity("beacn_mute_2", "switch")]
    assert cleanup_candidates(c, rows) == []
    c.data["beacn"] = {"unsupported_controls": ["volume_5", "mute_6", "mute_2"], "controls": []}
    assert set(cleanup_candidates(c, rows)) == {"number.beacn_volume_5", "switch.beacn_mute_6"}
    c.data["beacn"]["controls"] = [{"id": "volume_5", "available": False}]
    assert cleanup_candidates(c, rows) == ["switch.beacn_mute_6"]


def test_app_diagnostics_preserve_identity_and_survive_cleanup():
    from what_under_test.connection_info import APP_SENSORS
    # app_uptime used to be a selectable Windows reading. Its ID stays unchanged.
    readings = {"app_uptime": {"source": "windows"}}
    for identifier in (*APP_SENSORS, "app_logging", "app_publish_interval", "app_restart", "app_update"):
        assert module_for(identifier, readings) is None
    assert cleanup_candidates(coordinator(), [entity(key) for key in APP_SENSORS]) == []


def test_named_installation_diagnostics_stay_on_pc_and_out_of_cleanup():
    key = "installation_connection_" + "a" * 32
    assert module_for(key, {}) is None
    assert cleanup_candidates(coordinator(), [entity(key)]) == []
