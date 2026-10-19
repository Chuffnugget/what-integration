"""Percentage entity precision, independent of sensor provider."""
import importlib
import sys
import types
import pytest
import test_protocol


@pytest.fixture
def sensor(monkeypatch):
    class Base:
        def __init__(self, coordinator, identifier, name):
            self.coordinator = coordinator
    class Sensor:
        pass
    classes = types.SimpleNamespace(**{name: name.lower() for name in (
        "TEMPERATURE", "VOLTAGE", "CURRENT", "POWER", "FREQUENCY", "DATA_SIZE",
        "DATA_RATE", "DURATION", "TIMESTAMP", "ENUM")})
    for name, values in {
        "homeassistant": {}, "homeassistant.components": {},
        "homeassistant.components.sensor": {"SensorDeviceClass": classes, "SensorEntity": Sensor, "SensorStateClass": str},
        "homeassistant.const": {"EntityCategory": types.SimpleNamespace(DIAGNOSTIC="diagnostic")},
        "homeassistant.core": {"callback": lambda fn: fn},
        "what_under_test.entity": {"WhatEntity": Base},
    }.items():
        module = types.ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    name = "what_under_test.sensor"
    monkeypatch.delitem(sys.modules, name, raising=False)
    module = importlib.import_module(name)
    yield module
    sys.modules.pop(name, None)


@pytest.mark.parametrize("source", ["windows", "hwinfo", "icue", "nvidia"])
@pytest.mark.parametrize(
    "value, expected",
    [(44.903315, 44.9), (12.3456, 12.35), (0, 0), (100, 100), (99.9999, 100), (None, None)],
)
def test_percentage_precision(sensor, source, value, expected):
    row = {
        "source": source,
        "group": "Usage",
        "name": "Load",
        "unit": "%",
        "kind": "number",
        "value": value,
    }
    coordinator = types.SimpleNamespace(readings={"load": row})
    entity = sensor.HardwareSensor(coordinator, "load", row)
    assert entity.native_value == expected
    assert entity._attr_suggested_display_precision == 2
    assert row["value"] == value  # Shared incoming data remains unchanged.


@pytest.mark.parametrize(
    "unit, kind, value",
    [("V", "number", 1.123456), ("", "number", 3.141592), ("%", "text", "44.903315%")],
)
def test_other_values_unchanged(sensor, unit, kind, value):
    row = {
        "source": "hwinfo",
        "group": "Fixture",
        "name": "Reading",
        "unit": unit,
        "kind": kind,
        "value": value,
    }
    entity = sensor.HardwareSensor(types.SimpleNamespace(readings={"reading": row}), "reading", row)
    assert entity.native_value == value
    assert not hasattr(entity, "_attr_suggested_display_precision")


def test_display_arrangement_privacy_and_readback(sensor):
    from test_displays import DISPLAYS
    import copy
    coordinator = types.SimpleNamespace(
        data={"displays": copy.deepcopy(DISPLAYS) | {"layout_editor": True}},
        last_update_success=True,
    )
    entity = sensor.DisplayArrangement(coordinator)
    assert entity.available and entity.native_value == 2
    assert entity.extra_state_attributes["monitors"][1]["x"] == 1920
    coordinator.data["displays"]["control_enabled"] = False
    assert entity.available and not entity.extra_state_attributes["control_enabled"]
    coordinator.data["displays"]["enabled"] = False
    assert not entity.available and entity.extra_state_attributes["monitors"] == []
    coordinator.last_update_success = False
    assert not entity.available


@pytest.mark.parametrize(
    "host,scope",
    [("127.0.0.1", "Localhost"), ("::1", "Localhost"), ("192.168.1.2", "LAN"), ("pc.example", "Hostname (network scope unknown)")],
)
def test_pc_connection_diagnostics(sensor, host, scope, monkeypatch):
    from what_under_test import connection_info as info
    monkeypatch.setattr(info.time, "monotonic", lambda: 160)
    c = types.SimpleNamespace(last_update_success=True, transport="local", connection_started=100, connected_since=None,
        entry=types.SimpleNamespace(data={"host": host}, options={}),
        readings={"a": {"available": True, "value": 0}, "b": {"available": False, "value": None}},
        data={"beacn": {"controls": [{"available": False}]}})
    c.reading_counts = {"published_entities": 2, "unavailable_readings": 1, "beacn_unavailable_controls": 1}
    uptime = sensor.ConnectionDiagnostic(c, "connection_uptime", "Connection uptime")
    transport = sensor.ConnectionDiagnostic(c, "connection_transport", "Connection transport")
    assert uptime.native_value == 60
    assert transport.native_value == "Direct TLS WebSocket"
    assert transport.extra_state_attributes == {"endpoint_scope": scope}
    assert info.connection_info(c)["published_entities"] == 2
    assert info.connection_info(c)["unavailable_readings"] == 1
    assert info.connection_info(c)["beacn_unavailable_controls"] == 1
    c.transport = "mqtt"
    assert transport.native_value == "MQTT"
    assert transport.extra_state_attributes == {"endpoint_scope": "Unknown"}
    c.entry.options["remote_enabled"] = True
    assert info.connection_info(c)["connection_mode"] == "Remote enabled"
    c.last_update_success = False
    assert uptime.available and uptime.native_value == 0
    assert transport.available and transport.native_value == "Disconnected"
    assert not sensor.ConnectionDiagnostic(c, "published_entities", "Published readings").available


def test_active_connection_mode_tracks_route_not_configuration(sensor):
    from what_under_test.connection_info import connection_info
    from types import SimpleNamespace
    c = SimpleNamespace(last_update_success=True, transport="local", readings={}, data={}, reading_counts={},
                        entry=SimpleNamespace(
                            data={"host": "192.168.1.20"},
                            options={"remote_enabled": True},
                        ))
    entity = sensor.ConnectionDiagnostic(c, "connection_active_mode", "Active connection mode")
    assert entity.native_value == "Local"
    assert connection_info(c)["connection_mode"] == "Remote enabled"
    c.transport = "remote"
    assert entity.native_value == "Remote"
    c.transport = "mqtt"
    assert entity.native_value == "Unknown"
    c.transport = "local"
    for host, expected in (("localhost", "Local"), ("::1", "Local"), ("8.8.8.8", "Online"), ("my-pc", "Unknown")):
        c.entry.data["host"] = host
        assert entity.native_value == expected
    c.last_update_success = False
    assert entity.available and entity.native_value == "Disconnected"



def test_cleanup_status_exposes_preview_and_zero_result(sensor):
    maintenance = types.SimpleNamespace(state={"status": "Review required", "count": 1, "entities": ["sensor.old"]})
    entity = sensor.CleanupStatus(types.SimpleNamespace(entity_maintenance=maintenance))
    assert entity.native_value == "Review required"
    assert entity.extra_state_attributes == {"count": 1, "entities": ["sensor.old"]}
    maintenance.state = {"status": "Nothing to remove", "count": 0, "entities": []}
    assert entity.native_value == "Nothing to remove"
    assert entity.extra_state_attributes["count"] == 0


@pytest.fixture
def installation_sensor(sensor, monkeypatch):
    name = "what_under_test.installation_sensor"
    monkeypatch.delitem(sys.modules, name, raising=False)
    yield importlib.import_module(name)
    sys.modules.pop(name, None)


def test_named_installation_state_rename_and_disconnection(installation_sensor):
    from test_protocol import CONNECTION
    rows = {CONNECTION["id"]: dict(CONNECTION)}
    c = types.SimpleNamespace(installation_connections=rows, last_update_success=True)
    entity = installation_sensor.InstallationConnection(c, CONNECTION["id"])
    assert entity.name == "Connection - Main House"
    assert entity.available and entity.native_value == "connected"
    attrs = entity.extra_state_attributes
    assert attrs["transport"] == "Home Assistant API" and attrs["sharing_scope"] == "local"
    assert attrs["last_reply"] == "2023-11-14T22:13:25+00:00"
    rows[CONNECTION["id"]]["name"] = "Renamed House"
    assert entity.name == "Connection - Renamed House"
    rows[CONNECTION["id"]]["status"] = "disabled"
    assert entity.available and entity.native_value == "disabled"
    c.last_update_success = False
    assert not entity.available and entity.extra_state_attributes is None
    c.last_update_success = True
    rows.clear()
    assert not entity.available and entity.native_value is None
    assert entity.name == "Connection - Renamed House"


def test_named_installation_discovery_adds_only_new_ids(installation_sensor):
    from test_protocol import CONNECTION
    listeners, added, unloads = [], [], []
    c = types.SimpleNamespace(installation_connections={CONNECTION["id"]: CONNECTION},
        async_add_listener=lambda listener: (listeners.append(listener) or (lambda: None)))
    entry = types.SimpleNamespace(runtime_data=c, async_on_unload=unloads.append)
    installation_sensor.async_setup_installation_sensors(entry, added.extend)
    assert len(added) == 1 and len(unloads) == 1
    listeners[0]()
    assert len(added) == 1
    c.installation_connections["b" * 32] = CONNECTION | {"id": "b" * 32, "name": "Other House"}
    listeners[0]()
    assert len(added) == 2
    del c.installation_connections[CONNECTION["id"]]
    listeners[0]()
    assert len(added) == 2  # Registry IDs and history survive temporary absence.


def test_coretemp_margin_is_not_an_absolute_temperature(sensor):
    row = {"source": "coretemp", "group": "CPU 1", "name": "Distance to TjMax",
           "unit": "°C", "kind": "number", "value": 20}
    margin = sensor.HardwareSensor(types.SimpleNamespace(), "coretemp_cpu_0_core_0_distance_to_tjmax", row)
    temperature = sensor.HardwareSensor(types.SimpleNamespace(), "coretemp_cpu_0_core_0_temperature", row)
    assert margin._attr_device_class is None
    assert margin._attr_native_unit_of_measurement == "°C"
    assert temperature._attr_device_class == "temperature"
