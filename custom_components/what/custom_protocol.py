"""Validation of the Custom module state (saved command names and permissions, not scripts)."""
import re


def validate_custom(data, error):
    if not isinstance(data, dict) or set(data) != {"enabled", "control_enabled", "cloud", "actions"}:
        raise error("Invalid custom module metadata")
    if any(type(data[key]) is not bool for key in ("enabled", "control_enabled", "cloud")):
        raise error("Invalid custom module permission")
    actions = data["actions"]
    if not isinstance(actions, list) or len(actions) > 128:
        raise error("Invalid custom commands")
    known = set()
    for action in actions:
        if (not isinstance(action, dict)
                or set(action) != {"id", "name", "available", "local_available"}
                or not isinstance(action["id"], str)
                or not re.fullmatch(r"custom_action_[a-f0-9]{32}", action["id"])
                or action["id"] in known
                or not isinstance(action["name"], str) or not action["name"].strip()
                or len(action["name"]) > 100 or "\0" in action["name"]
                or type(action["available"]) is not bool or type(action["local_available"]) is not bool):
            raise error("Invalid custom command metadata")
        known.add(action["id"])
