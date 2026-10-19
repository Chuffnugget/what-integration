"""Work out which entities of a PC are no longer shared and can be removed."""
import time
from .device_groups import DIAGNOSTICS

def selection_is_fresh(coordinator):
    data = coordinator.data or {}
    received = getattr(coordinator, "last_snapshot_received", None)
    return (coordinator.last_update_success and "selected_sensor_ids" in data
            and received is not None and time.monotonic() - received <= 60)


def cleanup_candidates(coordinator, entries):
    if not selection_is_fresh(coordinator):
        raise ValueError("fresh_selection_required")
    data = coordinator.data or {}
    selected = set(data["selected_sensor_ids"]) | set(getattr(coordinator, "readings", {}))
    # Only explicitly retired Aux controls are eligible, never an arbitrary
    # missing/disconnected control. Require the updated producer's declaration.
    retired = {"beacn_" + identifier for identifier in data.get("beacn", {}).get("unsupported_controls", [])
               if identifier in {f"{kind}_{source}" for source in (5, 6) for kind in ("volume", "mute", "audience")}}
    active = {"beacn_" + control["id"] for control in data.get("beacn", {}).get("controls", [])}
    retired -= active
    prefix = coordinator.entry.unique_id + "_"
    return [entity.entity_id for entity in entries
            if entity.config_entry_id == coordinator.entry.entry_id and entity.platform == "what"
            and entity.unique_id.startswith(prefix)
            and ((entity.domain in {"sensor", "binary_sensor"}
                  and entity.unique_id[len(prefix):] not in selected | DIAGNOSTICS
                  and not entity.unique_id[len(prefix):].startswith("installation_connection_"))
                 or (entity.domain in {"number", "switch"}
                     and entity.unique_id[len(prefix):] in retired))]
