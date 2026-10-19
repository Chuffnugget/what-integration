import asyncio
import sys
import time
import types

import pytest
import test_protocol
from test_device_groups import coordinator, entity
from what_under_test.entity_maintenance import EntityMaintenance


@pytest.fixture
def maintenance(monkeypatch):
    c = coordinator()
    rows = [entity(name) for name in ("old", "also_old", "keep", "unavailable", "entity_cleanup")]
    removed, responses, reloaded = [], [], []
    registry = types.SimpleNamespace(async_remove=removed.append)
    module = types.ModuleType("homeassistant.helpers.entity_registry")
    module.async_get = lambda hass: registry
    module.async_entries_for_config_entry = lambda registry, entry: rows
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.entity_registry = module
    errors = types.ModuleType("homeassistant.exceptions")
    errors.HomeAssistantError = RuntimeError
    for name, value in (("homeassistant.helpers", helpers),
                        ("homeassistant.helpers.entity_registry", module),
                        ("homeassistant.exceptions", errors)):
        monkeypatch.setitem(sys.modules, name, value)

    async def reply(action, result):
        responses.append((action, dict(result)))

    async def reload(entry):
        reloaded.append(entry)

    c.client = types.SimpleNamespace(command=reply)
    c.async_update_listeners = lambda: None
    c.hass = types.SimpleNamespace(
        async_create_task=asyncio.create_task,
        config_entries=types.SimpleNamespace(async_reload=reload),
    )
    bridge = c.entity_maintenance = EntityMaintenance(c)
    yield types.SimpleNamespace(c=c, bridge=bridge, rows=rows, removed=removed,
                                responses=responses, reloaded=reloaded)
    bridge.stop()


def test_pc_preview_remove_and_reselection(maintenance):
    m = maintenance

    async def run():
        preview = {"id": "a" * 32, "operation": "preview"}
        m.bridge.receive(preview)
        await m.bridge.task
        assert not m.removed
        assert m.responses[-1][1]["entities"] == ["sensor.also_old", "sensor.old"]
        assert not m.bridge.can_remove  # The HA button requires its own preview.
        token = m.responses[-1][1]["token"]
        m.bridge.receive(preview)
        assert len(m.responses) == 1
        m.c.data["selected_sensor_ids"].append("old")
        m.rows.append(entity("newly_stale"))
        m.bridge.receive({"id": "b" * 32, "operation": "remove", "token": token})
        await m.bridge.task
        await asyncio.sleep(0)
        assert m.removed == ["sensor.also_old"]
        assert m.reloaded == ["entry"]
        assert m.responses[-1][1]["count"] == 1
        m.bridge.receive({"id": "c" * 32, "operation": "remove", "token": token})
        await m.bridge.task
        assert m.responses[-1][1]["status"] == "error"
        assert "expired" in m.responses[-1][1]["message"]
        assert m.removed == ["sensor.also_old"]

    asyncio.run(run())


def test_device_preview_rechecks_selection_without_expanding_review(maintenance):
    m = maintenance

    async def run():
        await m.bridge.async_device_action("preview")
        assert m.bridge.state["count"] == 2
        assert m.bridge.can_remove
        m.c.data["selected_sensor_ids"].append("old")
        m.rows.append(entity("newly_stale"))
        await m.bridge.async_device_action("remove")
        assert m.removed == ["sensor.also_old"]
        assert m.reloaded == ["entry"]
        assert not m.bridge.can_remove
        assert m.bridge.state["removed"] == 1
        assert not m.responses  # HA buttons do not need a command round trip to the PC.

    asyncio.run(run())


def test_device_zero_candidates_and_preview_expiry(maintenance):
    m = maintenance

    async def run():
        m.c.data["selected_sensor_ids"].extend(["old", "also_old"])
        await m.bridge.async_device_action("preview")
        assert m.bridge.state["status"] == "Nothing to remove"
        assert not m.bridge.can_remove
        assert not m.removed
        m.bridge._expire()
        assert m.bridge.state["status"] == "Preview expired"
        with pytest.raises(RuntimeError, match="expired"):
            await m.bridge.async_device_action("remove")
        assert not m.removed

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["offline", "stale", "expired", "no_preview"])
def test_device_removal_requires_current_selection_and_review(maintenance, failure):
    m = maintenance

    async def run():
        if failure != "no_preview":
            await m.bridge.async_device_action("preview")
        if failure == "offline":
            m.c.last_update_success = False
        elif failure == "stale":
            m.c.last_snapshot_received -= 61
        elif failure == "expired":
            m.bridge.previews[m.bridge.device_preview] = (time.monotonic() - 1, {"sensor.old"})
        assert not m.bridge.can_remove
        with pytest.raises(RuntimeError):
            await m.bridge.async_device_action("remove")
        assert not m.removed

    asyncio.run(run())


def test_lost_pc_reply_is_logged_and_removal_is_not_retried(maintenance, caplog):
    m = maintenance

    async def run():
        m.bridge.receive({"id": "a" * 32, "operation": "preview"})
        await m.bridge.task
        token = m.responses[-1][1]["token"]

        async def fail(action, result):
            raise ConnectionError("PC disconnected")

        m.c.client.command = fail
        request = {"id": "b" * 32, "operation": "remove", "token": token}
        m.bridge.receive(request)
        await m.bridge.task
        await asyncio.sleep(0)
        m.bridge.receive(request)
        assert m.removed == ["sensor.also_old", "sensor.old"]
        assert m.reloaded == ["entry"]
        assert "Could not deliver WHAT cleanup result (removed)" in caplog.text

    asyncio.run(run())
