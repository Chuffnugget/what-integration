"""Custom metadata is bounded and never accepts executable definitions."""
import copy

import pytest
from test_protocol import SNAPSHOT, ProtocolError, validate_message


def snapshot():
    data = copy.deepcopy(SNAPSHOT)
    data["sensors"][0]["source"] = "custom"
    data["custom"] = {"enabled": True, "control_enabled": True, "cloud": False,
                      "actions": [{"id": "custom_action_" + "a" * 32, "name": "Backup",
                                   "available": True, "local_available": True}]}
    return data


def test_custom_snapshot():
    assert validate_message(snapshot(), "snapshot")["custom"]["actions"][0]["name"] == "Backup"


@pytest.mark.parametrize("change", [
    lambda s: s["custom"].update(command="echo unexpected"),
    lambda s: s["custom"].update(enabled="true"),
    lambda s: s["custom"]["actions"][0].update(command="echo unexpected"),
    lambda s: s["custom"]["actions"][0].update(id="custom_action_bad"),
    lambda s: s["custom"]["actions"][0].update(available=1),
    lambda s: s["custom"]["actions"][0].update(name="a" * 101),
    lambda s: s["custom"]["actions"].extend(s["custom"]["actions"]),
    lambda s: s["custom"].update(actions=[{}] * 129),
])
def test_reject_invalid_custom_metadata(change):
    data = snapshot()
    change(data)
    with pytest.raises(ProtocolError):
        validate_message(data, "snapshot")


@pytest.mark.parametrize("field,value", [("volume", float("nan")), ("muted", 1), ("state", None), ("state", {}), ("state", ""), ("state", "x" * 65)])
def test_invalid_announcement_state(field, value):
    data = snapshot()
    data["announcements"] = {"enabled": True, "state": "idle", "volume": 0.7, "muted": False, "error": ""}
    data["announcements"][field] = value
    with pytest.raises(ProtocolError):
        validate_message(data, "snapshot")


def test_unknown_announcement_state_does_not_disconnect():
    data = snapshot()
    data["announcements"] = {"enabled": True, "state": "executing", "volume": 0.7, "muted": False, "error": ""}
    assert validate_message(data, "snapshot")["announcements"]["state"] == "idle"


def test_notification_event_bounds():
    data = snapshot()
    event = {"id": "b" * 32, "notification_id": "fixture", "action": "confirm", "reply": "yes", "created": 1700000000}
    data["notifications"] = {"version": 1, "events": [event]}
    validate_message(copy.deepcopy(data), "snapshot")
    event["reply"] = "x" * 257
    with pytest.raises(ProtocolError):
        validate_message(data, "snapshot")
