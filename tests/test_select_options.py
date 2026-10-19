"""Long and duplicate labels must remain valid HA states and target the right ID."""
from hashlib import sha256
import test_protocol
from what_under_test.select_options import option_labels


def test_long_duplicate_and_literal_collision_labels():
    identifier = "a" * 256
    suffix = f" [{sha256(identifier.encode()).hexdigest()[:12]}]"
    collision = "x" * (255 - len(suffix)) + suffix
    choices = [("x" * 256, identifier), ("x" * 260, "other"), (collision, "literal"), ("Normal", "plain")]
    labels = option_labels(choices)
    assert len(labels) == len(choices)
    assert all(len(label) <= 255 for label in labels)
    assert set(labels.values()) == {identifier, "other", "literal", "plain"}
    assert labels["Normal"] == "plain"
    assert labels[collision] == "literal"
    assert labels == option_labels(reversed(choices))


def test_existing_short_labels_are_preserved():
    assert option_labels([("Default", "a"), ("Default", "b"), ("Gaming", "c")]) == {
        "Default [a]": "a", "Default [b]": "b", "Gaming": "c",
    }
