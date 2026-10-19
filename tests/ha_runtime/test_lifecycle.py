"""Real HA registration and lifecycle checks. Run separately from the stub tests."""
import asyncio
import copy
from pathlib import Path
import shutil
from types import MappingProxyType
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("homeassistant")
from homeassistant.core import HomeAssistant
from homeassistant import loader
from homeassistant.auth import auth_manager_from_config
from homeassistant.config_entries import ConfigEntry, ConfigEntries, ConfigEntryState
from homeassistant.setup import async_setup_component
from homeassistant.helpers import device_registry as dr, entity_registry as er, frame as ha_frame


@pytest.mark.asyncio
@pytest.mark.parametrize("start_offline", [False, True], ids=["online-start", "offline-start"])
async def test_platforms_reconnect_reload_and_unload(tmp_path, start_offline, caplog, unused_tcp_port):
    root = Path(__file__).resolve().parents[2]
    shutil.copytree(root / "custom_components", tmp_path / "custom_components", ignore=shutil.ignore_patterns("__pycache__"))
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    loader.async_setup(hass)
    ha_frame.async_setup(hass)
    await hass.config_entries.async_initialize()
    dr.async_setup(hass)
    await dr.async_load(hass)
    await er.async_load(hass)
    hass.auth = await auth_manager_from_config(hass, [{"type": "homeassistant"}], [])
    entry = ConfigEntry(domain="what", title="Audit PC", unique_id="audit-pc", version=1, minor_version=1,
                        data={"host": "127.0.0.1", "port": 1, "token": "a" * 64, "fingerprint": "00" * 32},
                        options={"remote_enabled": start_offline}, source="user", discovery_keys=MappingProxyType({}), subentries_data=[])
    # Only the PC is simulated; entities, registries, services and lifecycle are HA's.
    import sys
    sys.path.insert(0, str(root / "tests"))
    from test_protocol import SNAPSHOT, APP, CONNECTION, CONNECTION_CONFIG, lighting_snapshot
    from test_commands import controls
    from test_beacn import STATE
    from test_displays import DISPLAYS
    from test_icue import ICUE
    from test_icue_automation import AUTO
    from test_icue_lighting import LIGHTING
    frame = copy.deepcopy(SNAPSHOT | {
        "device_id": "audit-pc", "app": APP, "beacn": STATE, "displays": DISPLAYS,
        "icue": ICUE, "icue_automation": AUTO | {"lighting": LIGHTING},
        "dynamic_lighting": lighting_snapshot()["dynamic_lighting"], "controls": controls(),
        "wallpaper": {"enabled": True, "running": True, "control_enabled": True, "share_path": True,
                      "installed": True, "busy": False, "cloud": False, "status": "Running",
                      "current_wallpaper": "C:/fixture/project.json"},
    })
    frame["connections"] = [dict(CONNECTION), CONNECTION | {"id": "b" * 32, "name": "Other House", "type": "mqtt"}]
    frame["app"]["control_enabled"] = True
    frame["announcements"] = {"enabled": True, "state": "idle", "volume": 0.7, "muted": False, "error": ""}
    frame["notifications"] = {"version": 1, "events": []}
    frame["controls"]["notifications_enabled"] = True
    custom_id = "custom_action_" + "a" * 32
    frame["custom"] = {"enabled": True, "control_enabled": True, "cloud": False,
                       "actions": [{"id": custom_id, "name": "Saved Fixture", "available": True, "local_available": True}]}
    presets = frame["icue_automation"]["controls"][0]
    for choice in presets["options"]:
        choice["name"] = "Long preset " + "x" * 240
    frame["controls"]["permissions"] = dict.fromkeys(frame["controls"]["permissions"], True)
    frame["controls"]["power"] = {"active": "a", "plans": [
        {"id": "a", "name": "Power " + "x" * 250}, {"id": "b", "name": "Power " + "x" * 250},
    ]}
    for control in frame["beacn"]["controls"]:
        if control["kind"] == "select":
            control["options"] = ["x" * 512, "x" * 511 + "y"]
            control["value"] = control["options"][0]
    hello = {"device_id": "audit-pc", "name": "Audit PC", "app_version": "0.0.1-beta.16"}
    integration = await loader.async_get_integration(hass, "what")
    await integration.async_get_component()
    from custom_components.what.api import WhatClient

    async def connect(client):
        if start_offline:
            raise ConnectionError("Fixture PC offline")
        client.commands = True
        client.ws = AsyncMock(closed=False)
        return dict(hello), copy.deepcopy(frame)

    async def receive(client):
        await asyncio.Event().wait()

    try:
        with patch.object(WhatClient, "connect", connect), patch.object(WhatClient, "receive_snapshot", receive):
            assert await async_setup_component(hass, "what", {
                "http": {"server_host": "127.0.0.1", "server_port": unused_tcp_port},
            })
            await hass.async_start()
            await hass.config_entries.async_add(entry)
            await hass.async_block_till_done()
            assert entry.state is ConfigEntryState.LOADED
            coordinator = entry.runtime_data
            if start_offline:
                assert not coordinator.last_update_success
                start_offline = False
                coordinator.hello = dict(hello)
                coordinator._accept(copy.deepcopy(frame))
                await hass.async_block_till_done()
            # Authenticated route changes update the config entry in place.
            # Reloading here would discard event deduplication and the live stream.
            configured = copy.deepcopy(frame) | {"connection_config": CONNECTION_CONFIG}
            coordinator._accept(configured)
            await hass.async_block_till_done()
            assert entry.runtime_data is coordinator
            assert entry.options["remote_enabled"] is True
            assert coordinator.configuration_is_applied()
            entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
            assert {entity.domain for entity in entities} == {"sensor", "binary_sensor", "button", "select", "text", "switch", "update", "number", "media_player", "light"}
            missing = [entity.entity_id for entity in entities if not entity.disabled and hass.states.get(entity.entity_id) is None]
            assert not missing
            identities = {entity.unique_id: entity.entity_id for entity in entities}
            connection_id = identities["audit-pc_installation_connection_" + "a" * 32]
            other_id = identities["audit-pc_installation_connection_" + "b" * 32]
            assert er.async_get(hass).async_get(connection_id).device_id == coordinator.parent_device_id
            assert hass.states.get(connection_id).state == "connected"
            assert hass.states.get(other_id).attributes["transport"] == "MQTT"
            renamed = copy.deepcopy(frame)
            renamed["connections"][0].update(name="Renamed House", status="disabled")
            coordinator._accept(renamed)
            await hass.async_block_till_done()
            assert hass.states.get(connection_id).state == "disabled"
            assert "Renamed House" in hass.states.get(connection_id).attributes["friendly_name"]
            from homeassistant.helpers.update_coordinator import UpdateFailed
            coordinator.async_set_update_error(UpdateFailed("Fixture connection lost"))
            await hass.async_block_till_done()
            assert hass.states.get(connection_id).state == "unavailable"
            assert hass.states.get(other_id).state == "unavailable"
            coordinator._accept(copy.deepcopy(frame))
            await hass.async_block_till_done()
            assert hass.states.get(connection_id).state == "connected"

            for identifier, expected_name in (
                ("app_cpu", "WHAT CPU usage"),
                ("wallpaper_engine_nextWallpaper", "next wallpaper"),
            ):
                entity = er.async_get(hass).async_get(identities["audit-pc_" + identifier])
                assert entity.translation_key == identifier.lower()
                assert entity.original_name == expected_name
            coordinator.client.command = AsyncMock(return_value={"ok": True})
            button = identities["audit-pc_" + custom_id]
            await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
            coordinator.client.command.assert_awaited_with("custom_action", {"id": custom_id}, timeout=65)
            parent = dr.async_get(hass).async_get(coordinator.parent_device_id)
            await hass.services.async_call("what", "speak", {"device_id": [parent.id], "message": "Fixture"}, blocking=True)
            coordinator.client.command.assert_awaited_with("speak", {"message": "Fixture"}, timeout=15)
            from custom_components.what.announcements import SPEECH_SCHEMA
            import voluptuous as vol
            for invalid in (None, True, float("nan"), float("inf"), -1, 3):
                with pytest.raises(vol.Invalid):
                    SPEECH_SCHEMA({"device_id": [parent.id], "message": "Fixture", "rate": invalid})
            player = identities["audit-pc_windows_announcements"]
            await hass.services.async_call("media_player", "volume_set", {"entity_id": player, "volume_level": 0.25}, blocking=True)
            coordinator.client.command.assert_awaited_with("audio_control", {"operation": "volume", "value": 0.25}, timeout=15)

            # TTS/media-source results can be relative HA URLs. The same player
            # must use an external HTTPS URL after a remote transport handover.
            hass.config.internal_url = f"http://127.0.0.1:{unused_tcp_port}"
            hass.config.external_url = "https://ha.example.test"
            for transport, base in (("local", hass.config.internal_url),
                                    ("remote", hass.config.external_url),
                                    ("mqtt", hass.config.external_url)):
                coordinator.transport = transport
                await hass.services.async_call("media_player", "play_media", {
                    "entity_id": player, "media_content_type": "music",
                    "media_content_id": "/api/tts_proxy/what-fixture.wav",
                }, blocking=True)
                sent = coordinator.client.command.await_args
                assert sent.args[0] == "play_audio"
                assert sent.args[1]["url"].startswith(base + "/api/tts_proxy/what-fixture.wav")
            coordinator.transport = "local"

            # A transport replacement can deliver the same event frame again.
            # Fire each action once, and do not replay it on integration reload.
            from homeassistant.core import callback
            received = []
            @callback
            def receive_action(event):
                received.append(event.data)
            unsubscribe = hass.bus.async_listen("what_notification_action", receive_action)
            import time
            action_frame = copy.deepcopy(frame)
            action_frame["notifications"]["events"] = [{"id": "b" * 32, "notification_id": "fixture",
                "action": "confirm", "reply": "yes", "created": time.time()}]
            coordinator._accept(action_frame)
            coordinator._accept(copy.deepcopy(action_frame))
            await hass.async_block_till_done()
            assert len(received) == 1 and received[0]["reply"] == "yes"
            assert received[0]["device_id"] == parent.id
            unsubscribe()
            coordinator = entry.runtime_data
            coordinator.hello = dict(hello, app_version="0.0.1-beta.17")
            coordinator._accept(copy.deepcopy(frame))
            devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
            assert all(device.sw_version == "0.0.1-beta.17" for device in devices
                       if any(identifier == "audit-pc" or identifier.startswith("audit-pc_module_")
                              for domain, identifier in device.identifiers))
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            assert entry.state is ConfigEntryState.LOADED
            assert coordinator._task is None and coordinator._probe_task is None
            assert not [record for record in caplog.records if record.levelno >= 40 and "Error adding entity" in record.message]
            assert identities == {entity.unique_id: entity.entity_id for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)}
            coordinator = entry.runtime_data
            assert await hass.config_entries.async_unload(entry.entry_id)
            await hass.async_block_till_done()
            assert coordinator._task is None and coordinator._probe_task is None
    finally:
        await hass.async_stop()
