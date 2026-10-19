"""Delta reconstruction and legacy compatibility without an HA installation."""
import copy
import pytest
from test_protocol import SNAPSHOT
from what_under_test.protocol import StreamState, ProtocolError


def full():
    return {**copy.deepcopy(SNAPSHOT), "sequence": 1}


def delta(**changes):
    return {**copy.deepcopy(SNAPSHOT), "type": "delta", "sequence": 2,
            "base_sequence": 1, "sensors": [], "removed": [], **changes}


def test_change_remove_heartbeat_and_reconnect():
    stream = StreamState()
    stream.accept(full(), "pc-1", True)
    reading = copy.deepcopy(SNAPSHOT["sensors"][0])
    reading["value"] = 60
    changed = stream.accept(delta(sensors=[reading]), "pc-1", True)
    assert changed["sensors"][0]["value"] == 60
    heartbeat = stream.accept(delta(sequence=3, base_sequence=2), "pc-1", True)
    assert heartbeat["sensors"] == changed["sensors"]
    assert stream.accept(delta(sequence=4, base_sequence=3, removed=[reading["id"]]), "pc-1", True)["sensors"] == []
    assert StreamState().accept(full(), "pc-1", True)["sensors"] == SNAPSHOT["sensors"]


@pytest.mark.parametrize(
    "frame",
    [delta(), delta(sequence=3), delta(sequence=True), full() | {"sequence": 0}],
)
def test_no_delta_without_full_baseline(frame):
    with pytest.raises(ProtocolError):
        StreamState().accept(frame, "pc-1", True)


@pytest.mark.parametrize("changes", [{"sequence": 3}, {"base_sequence": True},
    {"removed": ["1_0_1", "1_0_1"]}, {"removed": ["1_0_1"], "sensors": SNAPSHOT["sensors"]},
    {"device_id": "wrong"}, {"sensors": SNAPSHOT["sensors"] * 2}])
def test_malformed_delta_leaves_baseline_unchanged(changes):
    stream = StreamState()
    stream.accept(full(), "pc-1", True)
    with pytest.raises(ProtocolError):
        stream.accept(delta(**changes), "pc-1", True)
    assert stream.sequence == 1
    assert list(stream.rows.values()) == SNAPSHOT["sensors"]


def test_legacy_snapshot_does_not_require_sequence():
    assert StreamState().accept(copy.deepcopy(SNAPSHOT), "pc-1")["sensors"] == SNAPSHOT["sensors"]


def test_delta_cannot_be_used_without_negotiation():
    with pytest.raises(ProtocolError):
        StreamState().accept(delta(), "pc-1")


def test_internal_delta_hints_preserve_baseline_and_ignore_wire_claims():
    stream = StreamState()
    first = stream.accept(full(), "pc-1", True)
    reading = copy.deepcopy(SNAPSHOT["sensors"][0])
    reading["value"] = 77
    second = stream.accept(delta(sensors=[reading], changed_ids=["forged"]), "pc-1", True)
    assert second.baseline is first.readings
    assert second.changed_ids == {reading["id"]}
    assert first.readings[reading["id"]]["value"] != 77
    heartbeat = stream.accept(delta(sequence=3, base_sequence=2), "pc-1", True)
    assert heartbeat.baseline is second.readings
    assert heartbeat.changed_ids == set()
    removed = stream.accept(delta(sequence=4, base_sequence=3, removed=[reading["id"]]), "pc-1", True)
    assert removed.changed_ids == {reading["id"]}
    assert not removed.readings


def test_coretemp_source_survives_full_and_delta_streams():
    initial = full()
    row = initial["sensors"][0]
    row.update(id="coretemp_cpu_0_core_0_temperature", source="coretemp", value=54.5)
    stream = StreamState()
    assert stream.accept(initial, "pc-1", True)["sensors"][0]["source"] == "coretemp"
    changed = copy.deepcopy(row)
    changed["value"] = 55
    assert stream.accept(delta(sensors=[changed]), "pc-1", True)["sensors"][0]["value"] == 55



def test_unchanged_delta_reuses_baseline_without_mutating_old_frames():
    import copy
    from test_protocol import SNAPSHOT
    from what_under_test.protocol import StreamState, ProtocolError
    stream = StreamState()
    original = copy.deepcopy(SNAPSHOT) | {"sequence": 1}
    first = stream.accept(original, "pc-1", True)
    heartbeat = original | {"type": "delta", "sequence": 2, "base_sequence": 1, "sensors": [], "removed": []}
    second = stream.accept(heartbeat, "pc-1", True)
    assert second.readings is first.readings
    assert second["sensors"] is first["sensors"]
    assert second.changed_ids == set()
    changed = copy.deepcopy(original["sensors"][0]) | {"value": 99}
    third = stream.accept(
        heartbeat | {"sequence": 3, "base_sequence": 2, "sensors": [changed]},
        "pc-1",
        True,
    )
    assert first["sensors"][0]["value"] == 55.5
    assert third["sensors"][0]["value"] == 99
    assert third.readings is not first.readings
    assert third.baseline is second.readings
    assert third.changed_ids == {changed["id"]}
    with pytest.raises(ProtocolError):
        stream.accept(
            heartbeat | {"sequence": 4, "base_sequence": 3, "publish_seconds": 0},
            "pc-1",
            True,
        )
    assert stream.sequence == 3
    assert stream.rows is third.readings


def test_removing_an_unknown_reading_is_ignored():
    stream = StreamState()
    stream.accept(full(), "pc-1", True)
    assert len(stream.accept(delta(removed=["skipped_by_older_integration"]), "pc-1", True)["sensors"]) == 1
