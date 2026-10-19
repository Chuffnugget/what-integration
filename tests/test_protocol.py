"""Run with pytest; no Home Assistant installation needed for protocol tests."""
import asyncio
import copy
import importlib.util
from pathlib import Path
import sys
import types

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / 'custom_components/what'
if 'what_under_test' not in sys.modules:
    package = types.ModuleType('what_under_test')
    package.__path__ = [str(PACKAGE)]
    sys.modules[package.__name__] = package
from what_under_test.protocol import ProtocolError, fingerprint_bytes, validate_message

SNAPSHOT = {'type': 'snapshot', 'protocol': 2, 'device_id': 'pc-1', 'healthy': True, 'hwinfo_enabled': True, 'windows_healthy': True,
            'status': 'Connected', 'sample_time': 1700000000, 'sample_age': 1, 'publish_seconds': 5, 'license': 'pro',
            'sensors': [{
                'id': '1_0_1',
                'group': 'CPU',
                'name': 'Package',
                'unit': '°C',
                'type': 1,
                'value': 55.5,
                'source': 'hwinfo',
                'kind': 'number',
                'available': True,
                'state_class': 'measurement',
            }]}

def test_valid_snapshot():
    assert validate_message(copy.deepcopy(SNAPSHOT), 'snapshot', 'pc-1')['healthy']

@pytest.mark.parametrize(
    'field,value',
    [('protocol', 1), ('protocol', True), ('device_id', 'other'), ('healthy', 1), ('sample_age', float('nan')), ('publish_seconds', 0), ('publish_seconds', 301), ('license', 7)],
)
def test_invalid_envelopes(field, value):
    data = copy.deepcopy(SNAPSHOT)
    data[field] = value
    with pytest.raises(ProtocolError):
        validate_message(data, 'snapshot', 'pc-1')

@pytest.mark.parametrize('value', [float('nan'), float('inf'), '55', True])
def test_invalid_readings(value):
    data = copy.deepcopy(SNAPSHOT)
    data['sensors'][0]['value'] = value
    with pytest.raises(ProtocolError):
        validate_message(data, 'snapshot')

def test_unknown_licence_is_reported_as_unknown():
    data = copy.deepcopy(SNAPSHOT)
    data['license'] = 'paid'
    assert validate_message(data, 'snapshot', 'pc-1')['license'] == 'unknown'

def test_null_and_missing_sensors():
    data = copy.deepcopy(SNAPSHOT)
    data['sensors'][0]['value'] = None
    data['sensors'][0]['available'] = False
    assert validate_message(data, 'snapshot')['sensors'][0]['value'] is None
    data['sensors'] = []
    data['healthy'] = False
    assert not validate_message(data, 'snapshot')['healthy']

def test_duplicates_rejected():
    data = copy.deepcopy(SNAPSHOT)
    data['sensors']*=2
    with pytest.raises(ProtocolError):
        validate_message(data, 'snapshot')

def test_fingerprints():
    assert fingerprint_bytes(':'.join(['AB'] * 32)) == bytes.fromhex('ab' * 32)
    for value in ['', 'aa' * 31, 'zz' * 32, 'aa' * 33]:
        with pytest.raises(ValueError):
            fingerprint_bytes(value)


@pytest.mark.parametrize(
    "kind,value",
    [("text", "Windows 11"), ("text", ""), ("binary", False), ("binary", True), ("timestamp", "2026-09-07T10:20:30Z")],
)
def test_typed_windows_readings_survive_hwinfo_failure(kind, value):
    data = copy.deepcopy(SNAPSHOT)
    data.update(healthy=False, hwinfo_enabled=False)
    data["sensors"][0].update(source="windows", kind=kind, value=value, unit="", state_class="")
    assert validate_message(data, "snapshot")["sensors"][0]["value"] == value


@pytest.mark.parametrize("changes", [
    {"kind": "binary", "value": 1, "unit": "", "state_class": ""},
    {"kind": "text", "value": "x" * 256, "unit": "", "state_class": ""},
    {"kind": "timestamp", "value": "2026-09-07T10:20:30", "unit": "", "state_class": ""},
    {"kind": "timestamp", "value": "not a date", "unit": "", "state_class": ""},
    {"available": False, "value": 12}, {"available": True, "value": None},
    {"kind": "text", "value": "55"},
])
def test_invalid_typed_readings(changes):
    data = copy.deepcopy(SNAPSHOT)
    data["sensors"][0].update(changes)
    with pytest.raises(ProtocolError):
        validate_message(data, "snapshot")


@pytest.mark.parametrize("changes", [{"source": "invented"}, {"kind": "gauge"}, {"state_class": "total"}])
def test_readings_from_newer_apps_are_skipped(changes):
    data = copy.deepcopy(SNAPSHOT)
    data["sensors"].append(dict(data["sensors"][0], id="new_reading", **changes))
    assert [row["id"] for row in validate_message(data, "snapshot")["sensors"]] == ["1_0_1"]


def test_nvidia_without_hwinfo():
    data = copy.deepcopy(SNAPSHOT)
    data.update(healthy=False, hwinfo_enabled=False)
    data['sensors'][0].update(id='nvidia_fixture_temperature', source='nvidia', group='NVIDIA GPU')
    result = validate_message(data, 'snapshot')
    assert result['sensors'][0]['available'] is True
    data['sensors'][0].update(value=None, available=False)
    assert validate_message(data, 'snapshot')['sensors'][0]['value'] is None


def lighting_snapshot():
    data = copy.deepcopy(SNAPSHOT)
    data["dynamic_lighting"] = {"enabled": True, "control_enabled": True, "cloud": False, "devices": [
        {
            "id": "win_lighting_" + "a" * 24,
            "name": "Keyboard",
            "available": True,
            "lamp_count": 104,
            "rgb": [1, 2, 255],
            "brightness": 75,
        }
    ]}
    return data


def test_dynamic_lighting_state():
    data = lighting_snapshot()
    assert validate_message(data, "snapshot", "pc-1")["dynamic_lighting"]["devices"][0]["brightness"] == 75
    data["dynamic_lighting"]["devices"][0].update(rgb=None, brightness=None)
    validate_message(data, "snapshot", "pc-1")


@pytest.mark.parametrize(
    "key,value",
    [("id", "raw-device-path"), ("available", 1), ("rgb", [1, 2, 256]), ("rgb", [True, 2, 3]), ("brightness", float("nan")), ("brightness", 101), ("lamp_count", -1)],
)
def test_dynamic_lighting_rejects_invalid_devices(key, value):
    data = lighting_snapshot()
    data["dynamic_lighting"]["devices"][0][key] = value
    with pytest.raises(ProtocolError):
        validate_message(data, "snapshot", "pc-1")


def test_dynamic_lighting_duplicate_and_permission_validation():
    data = lighting_snapshot()
    data["dynamic_lighting"]["devices"] *= 2
    with pytest.raises(ProtocolError):
        validate_message(data, "snapshot", "pc-1")
    data = lighting_snapshot()
    data["dynamic_lighting"]["control_enabled"] = "true"
    with pytest.raises(ProtocolError):
        validate_message(data, "snapshot", "pc-1")


@pytest.mark.parametrize("module,keys", [
    ("wallpaper", ("control_enabled", "share_path", "cloud")),
    ("beacn", ("control_enabled", "cloud")),
])
def test_restarted_qt_permission_flags(module, keys):
    for text, expected in (("true", True), ("false", False)):
        frame = copy.deepcopy(SNAPSHOT)
        frame[module] = {key: text for key in keys}
        result = validate_message(frame, "snapshot")
        assert all(result[module][key] is expected for key in keys)
    for bad in ("yes", "0", "False", 1, None, [], {}):
        frame = copy.deepcopy(SNAPSHOT)
        frame[module] = {keys[0]: bad}
        with pytest.raises(ProtocolError):
            validate_message(frame, "snapshot")



@pytest.mark.parametrize("choices", [None, [], {}, {"remote_enabled": True},
    {"remote_enabled": "true", "mqtt_enabled": False},
    {"remote_enabled": True, "mqtt_enabled": 1}])
def test_connection_options_require_both_boolean_flags(choices):
    with pytest.raises(ProtocolError, match="connection options"):
        validate_message(dict(SNAPSHOT, connection_options=choices), "snapshot")


def test_connection_options_are_preserved_for_the_paired_coordinator():
    choices = {"remote_enabled": True, "mqtt_enabled": False}
    assert validate_message(dict(SNAPSHOT, connection_options=choices), "snapshot")["connection_options"] == choices
    newer = choices | {"extra": False}
    assert validate_message(dict(SNAPSHOT, connection_options=newer), "snapshot")["connection_options"] == choices


APP = {
    "version": 1, "control_enabled": False, "logging": False, "restart_available": True,
    "publishing_interval_ms": 1000, "high_interval_ms": 100,
    "uptime_seconds": 120, "cpu_percent": 3.5, "memory_bytes": 123456789,
    "update": {"installed": "0.0.1-beta.16", "latest": None, "release_url": None,
               "checked_at": None, "channel": "beta", "busy": False},
}


def test_app_status_valid_and_unknown_measurements():
    frame = copy.deepcopy(SNAPSHOT) | {"app": copy.deepcopy(APP)}
    assert validate_message(frame, "snapshot")["app"]["cpu_percent"] == 3.5
    frame["app"].update(cpu_percent=None, memory_bytes=None)
    assert validate_message(frame, "snapshot")["app"]["memory_bytes"] is None
    frame["app"]["update"].update(
        latest="0.0.1-beta.17", checked_at="2026-09-27T12:00:00Z",
        release_url="https://github.com/Chuffnugget/what-windows/releases/tag/v0.0.1-beta.17",
    )
    assert validate_message(frame, "snapshot")["app"]["update"]["release_url"] == (
        "https://github.com/Chuffnugget/what-windows/releases/tag/v0.0.1-beta.17"
    )


@pytest.mark.parametrize("release_url", [
    "https://github.com/other/what-windows/releases/tag/v0.0.1-beta.17",
    "https://github.com/Chuffnugget/what-windows-other/releases/tag/v0.0.1-beta.17",
    "https://github.com/Chuffnugget/what-windows/releases/tag/v0.0.1-beta.18",
    "https://github.com/Chuffnugget/what-windows/releases/tag/v0.0.1-beta.17?extra=1",
    [], None,
])
def test_app_update_drops_unrelated_release_urls(release_url):
    frame = copy.deepcopy(SNAPSHOT) | {"app": copy.deepcopy(APP)}
    frame["app"]["update"].update(latest="0.0.1-beta.17", release_url=release_url)
    assert validate_message(frame, "snapshot")["app"]["update"]["release_url"] is None


@pytest.mark.parametrize("key,value", [
    ("version", True), ("control_enabled", "true"), ("logging", 1), ("restart_available", 0),
    ("publishing_interval_ms", 99), ("publishing_interval_ms", 60001),
    ("publishing_interval_ms", 100.5), ("high_interval_ms", 1001),
    ("cpu_percent", float("nan")), ("cpu_percent", 101), ("cpu_percent", True),
    ("memory_bytes", -1), ("uptime_seconds", None),
])
def test_app_status_rejects_invalid_state(key, value):
    app = copy.deepcopy(APP) | {key: value}
    with pytest.raises(ProtocolError):
        validate_message(copy.deepcopy(SNAPSHOT) | {"app": app}, "snapshot")


@pytest.mark.parametrize("key,value", [
    ("installed", "latest"), ("latest", ""),
    ("checked_at", "2026-09-27T12:00:00"), ("checked_at", True),
    ("checked_at", "invalid"), ("channel", "Nightly!"), ("channel", None), ("busy", "false"),
])
def test_app_update_rejects_invalid_state(key, value):
    app = copy.deepcopy(APP)
    app["update"][key] = value
    with pytest.raises(ProtocolError):
        validate_message(copy.deepcopy(SNAPSHOT) | {"app": app}, "snapshot")


def test_app_update_accepts_new_channels():
    app = copy.deepcopy(APP)
    app["update"]["channel"] = "nightly"
    assert validate_message(copy.deepcopy(SNAPSHOT) | {"app": app}, "snapshot")["app"]["update"]["channel"] == "nightly"


CONNECTION = {"id": "a" * 32, "name": "Main House", "type": "api", "status": "connected",
              "scope": "local", "connected_since": 1700000000000, "last_reply": 1700000005000}


def test_named_connections_preserve_identity_and_reject_duplicates():
    rows = [CONNECTION, CONNECTION | {"id": "b" * 32, "name": "Other House", "type": "mqtt"}]
    frame = copy.deepcopy(SNAPSHOT) | {"connections": rows}
    assert validate_message(frame, "snapshot")["connections"] == rows
    frame["connections"].append(CONNECTION)
    with pytest.raises(ProtocolError):
        validate_message(frame, "snapshot")


@pytest.mark.parametrize("field,value", [
    ("id", "bad"), ("name", ""), ("name", "a" * 81), ("name", "private\ntext"),
    ("last_reply", True), ("last_reply", -1), ("last_reply", 253402300799001),
    ("connected_since", float("nan")),
])
def test_invalid_named_connection_metadata(field, value):
    frame = copy.deepcopy(SNAPSHOT) | {"connections": [CONNECTION | {field: value}]}
    with pytest.raises(ProtocolError):
        validate_message(frame, "snapshot")


@pytest.mark.parametrize("field,value", [("type", "http"), ("status", "anything"), ("scope", [])])
def test_named_connections_from_newer_apps_are_skipped(field, value):
    frame = copy.deepcopy(SNAPSHOT) | {"connections": [CONNECTION, CONNECTION | {"id": "b" * 32, field: value}]}
    assert validate_message(frame, "snapshot")["connections"] == [CONNECTION]


def test_named_connections_drop_unknown_fields():
    frame = copy.deepcopy(SNAPSHOT) | {"connections": [CONNECTION | {"token": "must not be published"}]}
    assert validate_message(frame, "snapshot")["connections"] == [CONNECTION]


CONNECTION_CONFIG = {"revision": "a" * 32, "preferred": "api", "remote_enabled": True,
                     "mqtt_enabled": False, "direct_host": "192.0.2.1", "direct_port": 48765,
                     "fingerprint": "b" * 64}


def test_connection_configuration_is_optional_and_preserved():
    frame = copy.deepcopy(SNAPSHOT) | {"connection_config": CONNECTION_CONFIG}
    assert validate_message(frame, "snapshot")["connection_config"] == CONNECTION_CONFIG
    validate_message(copy.deepcopy(SNAPSHOT), "snapshot")


@pytest.mark.parametrize("field,value", [
    ("revision", "stale"), ("preferred", "both"), ("mqtt_enabled", 1), ("remote_enabled", "true"),
    ("direct_host", "https://192.0.2.1"), ("direct_host", "bad\x00host"), ("direct_port", 0),
    ("direct_port", 65536), ("direct_port", True), ("fingerprint", "00"),
])
def test_connection_configuration_rejects_invalid_fields(field, value):
    frame = copy.deepcopy(SNAPSHOT) | {"connection_config": CONNECTION_CONFIG | {field: value}}
    with pytest.raises(ProtocolError):
        validate_message(frame, "snapshot")


def test_connection_configuration_drops_unknown_fields():
    frame = copy.deepcopy(SNAPSHOT) | {"connection_config": CONNECTION_CONFIG | {"token": "unexpected"}}
    assert validate_message(frame, "snapshot")["connection_config"] == CONNECTION_CONFIG


def test_control_state_from_newer_apps():
    frame = copy.deepcopy(SNAPSHOT) | {"controls": {
        "version": 1,
        "permissions": {"media": True, "power": False, "power_plan": True, "urls": False, "monitor": False, "future": True},
        "values": {"win_audio_volume": 40, "future_value": 1},
        "power": {"plans": [], "modes": {"ac": "Balanced", "dc": "Quiet", "usb": "Balanced"}},
    }}
    controls = validate_message(frame, "snapshot")["controls"]
    assert set(controls["permissions"]) == {"media", "power", "power_plan", "urls", "monitor"}
    assert controls["values"] == {"win_audio_volume": 40}
    assert controls["power"]["modes"] == {"ac": "Balanced"}


@pytest.mark.parametrize("keys, expected", [
    ({"ac": "balanced", "dc": "efficiency"}, {"ac": "balanced", "dc": "efficiency"}),
    ({"ac": "future", "dc": "performance", "usb": "balanced"}, {"dc": "performance"}),
])
def test_stable_power_modes(keys, expected):
    frame = copy.deepcopy(SNAPSHOT)
    frame["controls"] = {
        "version": 1, "permissions": {}, "values": {},
        "power": {"plans": [], "mode_keys": keys},
    }
    assert validate_message(frame, "snapshot")["controls"]["power"]["mode_keys"] == expected


def test_malformed_power_mode_keys():
    frame = copy.deepcopy(SNAPSHOT)
    frame["controls"] = {
        "version": 1, "permissions": {}, "values": {},
        "power": {"plans": [], "mode_keys": []},
    }
    with pytest.raises(ProtocolError, match="Invalid power mode keys"):
        validate_message(frame, "snapshot")
