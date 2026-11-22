"""Exercise the WHAT Desktop grant boundary and wire replies against HA core."""
import asyncio
import base64
import json
import uuid
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.components.light import ColorMode, LightEntityFeature
from homeassistant.config_entries import ConfigEntries
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er

from custom_components.what.desktop_bridge import DesktopBridge
from custom_components.what.desktop_catalog import ADVANCED, CONTROL, CONTROL_ALL_LIGHTS, DESKTOP_ACCESS, PRESETS, VIEW, VIEW_ALL, stable_keys

pytestmark = pytest.mark.asyncio


class Socket:
    def __init__(self):
        self.frames = []

    async def send_json(self, data):
        self.frames.append(data)


async def request(bridge, op, **params):
    identifier = uuid.uuid4().hex
    await bridge._process(identifier, {"op": op, **params})
    return next(frame for frame in reversed(bridge.ws.frames) if frame.get("id") == identifier)


async def test_default_pairing_tracks_the_installation_and_controls_supported_entities(flow_hass, paired_entry):
    hass = flow_hass
    registry = er.async_get(hass)
    office = registry.async_get_or_create("sensor", "example", "office")
    disabled = registry.async_get_or_create("sensor", "example", "disabled",
                                            disabled_by=er.RegistryEntryDisabler.USER)
    hass.states.async_set("light.ceiling", "on")
    hass.states.async_set("input_text.pin", "123456", {"mode": "password"})
    hass.services.async_register("light", "turn_off", lambda call: None)
    bridge = DesktopBridge(hass, paired_entry, Socket())
    with patch("custom_components.what.desktop_bridge.get_url", return_value="http://house.test:8123"):
        await bridge.start()
    assert bridge.ws.frames[0]["web_url"] == "http://house.test:8123"
    found = await request(bridge, "search")
    assert found["view_mode"] == "all"
    assert found["domains"] == ["input_text", "light", "sensor"]
    assert {r["entity_id"] for r in found["rows"]} == {"light.ceiling", "input_text.pin", office.entity_id}
    assert "123456" not in str(found)
    assert not (await request(bridge, "detail", binding={"entity_id": "input_text.pin"}, kind="history"))["ok"]
    row, = (await request(bridge, "search", domain="sensor"))["rows"]
    assert row["registry_id"] == office.id and not row["available"]
    assert not (await request(bridge, "search", domain={}))["ok"]
    assert (await request(bridge, "search", kind="control:light"))["total"] == 1
    assert (await request(bridge, "action", binding={"entity_id": "light.ceiling"}, action="light.turn_off"))["ok"]
    assert not (await request(bridge, "action", binding={"entity_id": "light.ceiling"}, action="homeassistant.restart"))["ok"]
    for entity_id in ("sensor.invented", disabled.entity_id):
        row, = (await request(bridge, "subscribe", bindings=[{"entity_id": entity_id}]))["rows"]
        assert not row["available"] and "domain" not in row
    hass.states.async_set("sensor.new_sensor", "6")
    row, = (await request(bridge, "search", query="new sensor", domain="sensor"))["rows"]
    assert row["entity_id"] == "sensor.new_sensor"
    sub = await request(bridge, "subscribe", bindings=[{"entity_id": "sensor.new_sensor"}])
    assert sub["rows"][0]["state"] == "6"
    hass.states.async_remove("sensor.new_sensor")
    await asyncio.sleep(.15)
    await hass.async_block_till_done()
    assert bridge.ws.frames[-1]["id"] == sub["id"] and not bridge.ws.frames[-1]["rows"][0]["available"]
    assert (await request(bridge, "search", query="new sensor"))["total"] == 0
    hass.config_entries.async_update_entry(paired_entry, options={VIEW_ALL: True, CONTROL: ["light.ceiling"]})
    row, = (await request(bridge, "search", kind="control:light"))["rows"]
    assert row["entity_id"] == "light.ceiling"
    assert (await request(bridge, "action", binding={"entity_id": "light.ceiling"}, action="light.turn_off"))["ok"]
    hass.config_entries.async_update_entry(paired_entry, options={VIEW_ALL: False, VIEW: [office.id]})
    restricted = await request(bridge, "search")
    assert restricted["view_mode"] == "selected" and restricted["domains"] == ["sensor"]
    assert restricted["shared_total"] == 1 and restricted["rows"][0]["registry_id"] == office.id
    await bridge.close()


async def test_access_mode_honours_visible_entities_and_legacy_empty_controls(flow_hass, paired_entry):
    hass = flow_hass
    a, b = "light.desk", "light.hall"
    for entity in (a, b):
        hass.states.async_set(entity, "off")
    calls = []

    @callback
    def on(call):
        calls.append(call.data["entity_id"])

    hass.services.async_register("light", "turn_on", on)
    bridge = DesktopBridge(hass, paired_entry, Socket())
    for options, visible, writable in (
        ({VIEW_ALL: True, CONTROL: []}, {a, b}, set()),
        ({VIEW: [], CONTROL: []}, set(), set()),
        ({DESKTOP_ACCESS: "control", VIEW_ALL: False, VIEW: [a], CONTROL: [b]}, {a}, {a}),
        ({DESKTOP_ACCESS: "selected", VIEW_ALL: True, CONTROL: [b]}, {a, b}, {b}),
        ({DESKTOP_ACCESS: "invalid", VIEW_ALL: True, CONTROL_ALL_LIGHTS: True}, {a, b}, set()),
    ):
        hass.config_entries.async_update_entry(paired_entry, options=options)
        rows = (await request(bridge, "search"))["rows"]
        assert {row["entity_id"] for row in rows} == visible
        assert {row["entity_id"] for row in rows if row["actions"]} == writable
        for entity in (a, b):
            result = await request(bridge, "action", binding={"entity_id": entity}, action="light.turn_on")
            assert result["ok"] == (entity in writable)
    assert calls == [a, b]
    await bridge.close()


async def test_same_entity_id_in_two_home_assistant_installations(flow_hass, paired_entry, tmp_path):
    other = HomeAssistant(str(tmp_path / "other"))
    other.config_entries = ConfigEntries(other, {})
    await other.config_entries.async_initialize()
    dr.async_setup(other)
    await dr.async_load(other, load_empty=True)
    await er.async_load(other, load_empty=True)
    first = DesktopBridge(flow_hass, paired_entry, Socket())
    second = DesktopBridge(other, SimpleNamespace(options={DESKTOP_ACCESS: "view"}), Socket())
    entity = "light.lounge"
    calls = []

    @callback
    def record(call):
        calls.append(call.hass)

    try:
        for hass, state in ((flow_hass, "on"), (other, "off")):
            hass.states.async_set(entity, state)
            hass.services.async_register("light", "turn_on", record)
        binding = {"entity_id": entity}
        for bridge, state, can_control in ((first, "on", True), (second, "off", False)):
            row, = (await request(bridge, "subscribe", bindings=[binding]))["rows"]
            assert row["state"] == state and bool(row["actions"]) == can_control
            result = await request(bridge, "action", binding=binding, action="light.turn_on")
            assert result["ok"] == can_control
        assert calls == [flow_hass]
    finally:
        await first.close()
        await second.close()
        await other.async_stop()


async def test_all_lights_grant_is_live_and_scoped_to_one_pc(flow_hass, paired_entry):
    hass = flow_hass
    bridge = DesktopBridge(hass, paired_entry, Socket())
    other = DesktopBridge(hass, SimpleNamespace(options={DESKTOP_ACCESS: "view"}), Socket())
    registry = er.async_get(hass)
    disabled = registry.async_get_or_create("light", "example", "disabled", disabled_by=er.RegistryEntryDisabler.USER)
    lamp = registry.async_get_or_create("light", "example", "lamp")
    calls = []

    @callback
    def record(call):
        calls.append(call.data["entity_id"])

    for domain in ("light", "switch"):
        hass.services.async_register(domain, "turn_on", record)
    for entity_id in (lamp.entity_id, disabled.entity_id, "switch.outlet"):
        hass.states.async_set(entity_id, "off")
    hass.config_entries.async_update_entry(paired_entry, options={VIEW_ALL: False, CONTROL_ALL_LIGHTS: True})
    hass.states.async_set("light.new", "on")
    rows = (await request(bridge, "search", kind="control:light"))["rows"]
    assert {row["entity_id"] for row in rows} == {lamp.entity_id, "light.new"}
    assert (await request(bridge, "search"))["shared_total"] == 2
    binding = {"registry_id": lamp.id, "entity_id": lamp.entity_id}
    row, = (await request(bridge, "subscribe", bindings=[binding]))["rows"]
    assert row["state"] == "off" and row["actions"]
    assert (await request(bridge, "action", binding=binding, action="light.turn_on"))["ok"]
    assert (await request(bridge, "action", binding={"entity_id": "light.new"}, action="light.turn_on"))["ok"]
    assert not (await request(other, "action", binding=binding, action="light.turn_on"))["ok"]
    for entity_id in (disabled.entity_id, "light.missing", "switch.outlet"):
        domain = entity_id.split(".")[0]
        assert not (await request(bridge, "action", binding={"entity_id": entity_id}, action=domain + ".turn_on"))["ok"]
    hass.config_entries.async_update_entry(paired_entry, options={VIEW_ALL: False, CONTROL: [lamp.id]})
    remaining, = (await request(bridge, "search", kind="control:light"))["rows"]
    assert remaining["registry_id"] == lamp.id
    assert not (await request(bridge, "action", binding={"entity_id": "light.new"}, action="light.turn_on"))["ok"]
    assert calls == [lamp.entity_id, "light.new"]
    await bridge.close()
    await other.close()


async def test_light_widget_validates_live_capabilities_and_targets(flow_hass, paired_entry):
    hass = flow_hass
    bridge = DesktopBridge(hass, paired_entry, Socket())
    lamp = {"entity_id": "light.desk"}
    calls = []

    @callback
    def record(call):
        calls.append((call.service, call.data))

    for service in ("turn_on", "turn_off", "toggle"):
        hass.services.async_register("light", service, record)
    hass.config_entries.async_update_entry(paired_entry, options={CONTROL: ["light.desk"]})
    attrs = {"supported_color_modes": ["hs", "color_temp"], "min_color_temp_kelvin": 2750,
             "max_color_temp_kelvin": 5200, "supported_features": 4, "effect_list": ["Pulse", "Calm"]}
    hass.states.async_set("light.desk", "off", attrs)
    row, = (await request(bridge, "subscribe", bindings=[lamp]))["rows"]
    actions = {a["id"]: a["fields"] for a in row["actions"]}
    assert actions.keys() == {"light.turn_off", "light.turn_on", "light.toggle", "light.set_effect"}
    fields = {f["name"]: f for f in actions["light.turn_on"]}
    assert fields.keys() == {"brightness_pct", "color_temp_kelvin", "rgb_color"}
    assert all(not f["required"] for f in fields.values())
    assert (fields["color_temp_kelvin"]["min"], fields["color_temp_kelvin"]["max"]) == (2750, 5200)
    assert actions["light.set_effect"][0]["options"] == ["Pulse", "Calm"]

    async def send(action, data):
        return (await request(bridge, "action", binding=lamp, action="light." + action, data=data))["ok"]

    assert await send("turn_on", {"brightness_pct": 61, "color_temp_kelvin": 3200})
    assert await send("turn_on", {"rgb_color": [255, 0, 56]})
    assert await send("set_effect", {"effect": "Calm"})
    assert calls[-1] == ("turn_on", {"entity_id": "light.desk", "effect": "Calm"})
    assert not await send("set_effect", {"effect": "Missing"})
    assert not await send("turn_on", {"color_temp_kelvin": 5300})
    assert not await send("turn_on", {"color_temp_kelvin": 3200, "rgb_color": [0, 0, 0]})
    assert not await send("turn_on", {"rgb_color": [True, 0, 0]})
    assert not await send("set_effect", {"effect": "Calm", "entity_id": "light.other"})
    hass.states.async_set("light.desk", "on", {**attrs, "effect": "Calm", "effect_list": ["Pulse"]})
    assert not await send("set_effect", {"effect": "Calm"})
    current = bridge.catalog.row(lamp)
    assert current["attributes"]["effect"] == "Calm"
    hass.states.async_set("light.desk", "off", {"supported_color_modes": ["onoff"], "effect_list": ["Pulse"]})
    assert not await send("turn_on", {"brightness_pct": 10})
    assert not await send("set_effect", {"effect": "Pulse"})
    assert await send("turn_off", {})
    assert len(calls) == 4
    await bridge.close()


async def test_action_state_is_prompt_when_idle_and_batches_the_latest_reading(flow_hass, paired_entry):
    hass = flow_hass
    lamp, other = "light.desk", "sensor.outside"
    hass.states.async_set(lamp, "off")
    hass.states.async_set(other, "10")

    @callback
    def switch_on(call):
        hass.states.async_set(lamp, "on", {"brightness": 25})

    hass.services.async_register("light", "turn_on", switch_on)
    bridge = DesktopBridge(hass, paired_entry, Socket())
    try:
        await request(bridge, "subscribe", bindings=[{"entity_id": lamp}, {"entity_id": other}])
        with patch.object(bridge, "_safe_row", wraps=bridge._safe_row) as build_row:
            reply = await request(bridge, "action", binding={"entity_id": lamp}, action="light.turn_on")
            assert reply["ok"]
            # Drain ready callbacks only. An unconditional 100 ms debounce would
            # still show the old state after the action response.
            for _ in range(5):
                await asyncio.sleep(0)
            updates = [f["rows"] for f in bridge.ws.frames if f["type"] == "desktop_states"]
            assert [rows[0]["state"] for rows in updates] == ["on"]
            build_row.assert_called_once_with({"entity_id": lamp})

            hass.states.async_set(lamp, "on", {"brightness": 40})
            hass.states.async_set(lamp, "on", {"brightness": 80})
            await asyncio.sleep(.15)
            await hass.async_block_till_done()
            updates = [f["rows"] for f in bridge.ws.frames if f["type"] == "desktop_states"]
            assert [rows[0]["attributes"]["brightness"] for rows in updates] == [25, 80]
            assert build_row.call_count == 2
    finally:
        await bridge.close()


async def test_change_during_chunked_snapshot_arrives_after_initial_values(flow_hass, paired_entry):
    hass = flow_hass
    lamp, other = "light.desk", "sensor.outside"
    hass.states.async_set(other, "10")
    hass.states.async_set(lamp, "off")
    first_chunk, release = asyncio.Event(), asyncio.Event()

    class SlowSocket(Socket):
        async def send_json(self, frame):
            await super().send_json(frame)
            if frame["type"] == "desktop_result":
                first_chunk.set()
                await release.wait()

    bridge = DesktopBridge(hass, paired_entry, SlowSocket())
    def chunks(rows):
        return ([row] for row in rows)

    try:
        with patch.object(bridge, "_chunks", chunks):
            bridge.submit({"id": uuid.uuid4().hex, "op": "subscribe",
                           "bindings": [{"entity_id": other}, {"entity_id": lamp}]})
            async with asyncio.timeout(2):
                await first_chunk.wait()
                hass.states.async_set(lamp, "on")
                for _ in range(5):
                    await asyncio.sleep(0)
                release.set()
                while len(bridge.ws.frames) < 3:
                    await asyncio.sleep(0)
        assert [f["rows"][0]["state"] for f in bridge.ws.frames] == ["10", "off", "on"]
    finally:
        release.set()
        await bridge.close()


async def test_light_group_exports_its_own_capabilities_independently_of_permission(flow_hass, paired_entry):
    hass = flow_hass
    registry = er.async_get(hass)
    lamp = registry.async_get_or_create("light", "group", "desk_group", capabilities={
        "supported_color_modes": frozenset({ColorMode.HS, ColorMode.XY, ColorMode.COLOR_TEMP}),
        "min_color_temp_kelvin": 2127, "max_color_temp_kelvin": 6535, "effect_list": ["Calm"],
        "private": "private-metadata"}, supported_features=LightEntityFeature.EFFECT)
    binding = {"registry_id": lamp.id, "entity_id": lamp.entity_id}
    bridge = DesktopBridge(hass, paired_entry, Socket())
    hass.config_entries.async_update_entry(paired_entry, options={VIEW: [lamp.id]})
    hass.services.async_register("light", "turn_on", lambda call: None)
    # An unshared group member must not add capabilities or leak its identity.
    hass.states.async_set("light.unshared_member", "on", {"supported_color_modes": ["rgbww"]})
    hass.states.async_set(lamp.entity_id, "off", {"entity_id": ["light.unshared_member"], "color_mode": None})
    reply = await request(bridge, "subscribe", bindings=[binding])
    row, = json.loads(json.dumps(reply))["rows"]
    assert row["available"] and row["state"] == "off" and row["actions"] == []
    attrs = row["attributes"]
    assert set(attrs["supported_color_modes"]) == {"hs", "xy", "color_temp"}
    assert (attrs["min_color_temp_kelvin"], attrs["max_color_temp_kelvin"]) == (2127, 6535)
    assert attrs["supported_features"] == 4 and attrs["effect_list"] == ["Calm"]
    assert "private-metadata" not in str(row) and "unshared_member" not in str(row)
    payload = {"binding": binding, "action": "light.turn_on", "data": {"rgb_color": [120, 11, 60]}}
    assert not (await request(bridge, "action", **payload))["ok"]
    hass.config_entries.async_update_entry(paired_entry, options={CONTROL: [lamp.id]})
    assert (await request(bridge, "action", **payload))["ok"]

    # A white-temperature bulb may publish an RGB reading derived from Kelvin.
    # Re-evaluate its declarations on every action, even if the registry still lists HS.
    hass.states.async_set(lamp.entity_id, "on", {"supported_color_modes": [ColorMode.COLOR_TEMP],
                          "supported_features": LightEntityFeature.TRANSITION, "color_mode": ColorMode.COLOR_TEMP,
                          "rgb_color": (255, 209, 163), "effect_list": []})
    live = bridge.catalog.row(binding)
    assert live["attributes"]["rgb_color"] == [255, 209, 163]
    assert live["attributes"]["supported_features"] == 32
    assert live["attributes"]["effect_list"] == []
    assert {f["name"] for f in live["actions"][0]["fields"]} == {"brightness_pct", "color_temp_kelvin"}
    assert not (await request(bridge, "action", **payload))["ok"]
    hass.states.async_remove(lamp.entity_id)
    offline = bridge.catalog.row(binding)
    assert not offline["available"] and "hs" in offline["attributes"]["supported_color_modes"]
    assert not (await request(bridge, "action", **payload))["ok"]
    await bridge.close()


@pytest.mark.parametrize(("mode", "extra", "fields"), [
    (ColorMode.WHITE, {}, {"brightness_pct"}),
    (ColorMode.ONOFF, {}, set()),
    (ColorMode.COLOR_TEMP, {}, {"brightness_pct", "color_temp_kelvin"}),
    (ColorMode.HS, {"hs_color": (280.5, 90)}, {"brightness_pct", "rgb_color"}),
    (ColorMode.XY, {"xy_color": (0.3, 0.4)}, {"brightness_pct", "rgb_color"}),
    (ColorMode.RGBW, {"rgbw_color": (20, 30, 40, 50)}, {"brightness_pct", "rgb_color"}),
    (ColorMode.RGBWW, {"rgbww_color": (20, 30, 40, 50, 60)}, {"brightness_pct", "rgb_color"}),
])
async def test_current_mode_on_light_without_registry_capabilities(flow_hass, paired_entry, mode, extra, fields):
    hass = flow_hass
    hass.services.async_register("light", "turn_on", lambda call: None)
    hass.states.async_set("light.desk", "on", {"color_mode": mode, **extra})
    hass.config_entries.async_update_entry(paired_entry, options={CONTROL: ["light.desk"]})
    bridge = DesktopBridge(hass, paired_entry, Socket())
    row, = (await request(bridge, "search", kind="light"))["rows"]
    assert row["attributes"]["supported_color_modes"] == [mode.value]
    for key, value in extra.items():
        assert row["attributes"][key] == list(value)
    assert {f["name"] for f in row["actions"][0]["fields"]} == fields
    if mode == ColorMode.COLOR_TEMP:
        kelvin = next(f for f in row["actions"][0]["fields"] if f["name"] == "color_temp_kelvin")
        assert (kelvin["min"], kelvin["max"]) == (2000, 6535)
    await bridge.close()


async def test_large_catalog_returns_sorted_pages_and_live_totals(flow_hass, paired_entry):
    bridge = DesktopBridge(flow_hass, paired_entry, Socket())
    ids = [f"sensor.office_{i:03d}" for i in range(400)]
    for entity_id in reversed(ids):
        flow_hass.states.async_set(entity_id, "19", {"friendly_name": "Office"})
    for offset in (0, 25, 100, 386, 400):
        page = await request(bridge, "search", kind="numeric", offset=offset)
        assert page["shared_total"] == page["total"] == len(ids)
        assert [row["entity_id"] for row in page["rows"]] == ids[offset:offset + 25]
    await bridge.close()


async def test_scoped_discovery_control_and_rename(flow_hass, paired_entry):
    hass = flow_hass
    reg = er.async_get(hass)
    lamp = reg.async_get_or_create("light", "test", "lamp", suggested_object_id="old_light")
    hass.states.async_set(lamp.entity_id, "on", {"friendly_name": "Office light", "supported_color_modes": ["rgb"],
                                                "access_token": "private_key", "entity_picture": "/api?token=keep_out"})
    hass.states.async_set("sensor.unshared", "7")
    calls = []

    @callback
    def tapped(call):
        calls.append(call.data)

    hass.services.async_register("light", "turn_on", tapped)
    hass.services.async_register("light", "turn_off", tapped)
    hass.config_entries.async_update_entry(paired_entry, options={VIEW_ALL: False})
    b = DesktopBridge(hass, paired_entry, Socket())
    await b.start()
    assert b.ws.frames == [{"type": "desktop_ready"}]
    assert (await request(b, "search"))["total"] == 0
    hass.config_entries.async_update_entry(paired_entry, options={VIEW: stable_keys(hass, [lamp.entity_id])})
    row, = (await request(b, "search", query="office", limit=1))["rows"]
    assert row["registry_id"] == lamp.id and not row["actions"]
    assert "private_key" not in str(row) and "keep_out" not in str(row)
    binding = {"entity_id": lamp.entity_id, "registry_id": lamp.id}
    assert not (await request(b, "action", binding=binding, action="light.turn_on"))["ok"]
    hass.config_entries.async_update_entry(paired_entry, options={CONTROL: [lamp.id]})
    assert (await request(b, "search", kind="toggle"))["total"] == 1
    other = DesktopBridge(hass, SimpleNamespace(options={VIEW: []}), Socket())
    assert (await request(other, "search"))["total"] == 0
    assert not (await request(other, "action", binding=binding, action="light.turn_on"))["ok"]
    forged = {"entity_id": "sensor.unshared", "registry_id": uuid.uuid4().hex}
    assert not (await request(b, "action", binding=forged, action="light.turn_on"))["ok"]
    for data in ({"entity_id": "light.all"}, {"brightness_pct": 101}, {"rgb_color": [300, 0, 0]}):
        assert not (await request(b, "action", binding=binding, action="light.turn_on", data=data))["ok"]
    identifier = uuid.uuid4().hex
    msg = {"op": "action", "binding": binding, "action": "light.turn_on", "data": {"brightness_pct": 32}}
    await b._process(identifier, msg)
    assert b.ws.frames[-1]["ok"]
    await b._process(identifier, msg)
    assert not b.ws.frames[-1]["ok"]
    assert len(calls) == 1 and calls[0]["brightness_pct"] == 32 and calls[0]["entity_id"] == lamp.entity_id
    sub = await request(b, "subscribe", bindings=[binding])
    assert sub["ok"] and sub["rows"][0]["state"] == "on"
    hass.states.async_set(lamp.entity_id, "off")
    await asyncio.sleep(.15)
    await hass.async_block_till_done()
    assert b.ws.frames[-1]["id"] == sub["id"] and b.ws.frames[-1]["rows"][0]["state"] == "off"
    reg.async_update_entity(lamp.entity_id, new_entity_id="light.renamed")
    hass.states.async_set("light.renamed", "on")
    replacement = reg.async_get_or_create("light", "test", "new-light", suggested_object_id="old_light")
    assert replacement.id != lamp.id
    await asyncio.sleep(.15)
    await hass.async_block_till_done()
    assert b.ws.frames[-1]["rows"][0]["entity_id"] == "light.renamed"
    assert not (await request(b, "action", binding={"entity_id":replacement.entity_id}, action="light.turn_on"))["ok"]
    reg.async_remove("light.renamed")
    assert not (await request(b, "action", binding=binding, action="light.turn_on"))["ok"]
    await b.close()
    await other.close()
    assert b.unlisten is None and b.registry_unlisten is None and not b.tasks


async def test_search_across_device_area_and_entity_fields(flow_hass, paired_entry):
    hass = flow_hass
    await ar.async_load(hass)
    areas = ar.async_get(hass)
    area = areas.async_create("Living Room")
    attic = areas.async_create("Attic")
    private = areas.async_create("Study")
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(config_entry_id=paired_entry.entry_id,
        identifiers={("example", "left")}, name="Ceiling fixture")
    devices.async_update_device(device.id, area_id=area.id, name_by_user="Pendant")
    registry = er.async_get(hass)
    lamp = registry.async_get_or_create("light", "example", "a", device_id=device.id,
                                        suggested_object_id="lamp_a")
    second = registry.async_get_or_create("light", "example", "b", device_id=device.id,
                                          suggested_object_id="lamp_b", original_name="Second lamp", has_entity_name=True)
    secret = registry.async_get_or_create("light", "example", "secret", device_id=device.id,
                                          suggested_object_id="unshared_spotlight", original_name="Spotlight",
                                          has_entity_name=True)
    registry.async_update_entity(secret.entity_id, area_id=private.id)
    for entity in (lamp, second, secret):
        hass.states.async_set(entity.entity_id, "on", {"friendly_name": "Reading lamp"})
    bridge = DesktopBridge(hass, paired_entry, Socket())
    hass.config_entries.async_update_entry(paired_entry, options={VIEW: []})
    empty = await request(bridge, "search")
    assert empty["areas"] == [] and empty["shared_total"] == empty["total"] == 0
    hass.config_entries.async_update_entry(paired_entry, options={VIEW: [lamp.id, second.id, lamp.entity_id]})
    for offset, entity in enumerate((lamp, second)):
        page = await request(bridge, "search", area=area.id, query="ROOM pendant, reading_light", limit=1, offset=offset)
        assert page["shared_total"] == page["total"] == 2
        assert page["areas"] == [{"id": area.id, "name": area.name}]
        row, = page["rows"]
        assert row["entity_id"] == entity.entity_id and row["device"] == "Pendant" and row["area"] == "Living Room"
    page = await request(bridge, "search", query="example lamp-b")
    assert [row["entity_id"] for row in page["rows"]] == [second.entity_id]
    for query, kind in ((secret.entity_id, "all"), ("absent", "all"), ("living lamp", "button")):
        page = await request(bridge, "search", query=query, kind=kind)
        assert page["rows"] == [] and page["total"] == 0 and page["shared_total"] == 2
    hass.states.async_set(second.entity_id, "on", {"friendly_name": "A copy of " + lamp.entity_id})
    page = await request(bridge, "search", query=lamp.entity_id)
    assert [row["entity_id"] for row in page["rows"]] == [lamp.entity_id, second.entity_id]
    registry.async_update_entity(second.entity_id, area_id=attic.id)
    areas.async_update(area.id, name="Lounge")
    page = await request(bridge, "search", area=area.id, query="pendant lounge", kind="light")
    assert page["areas"] == [{"id": attic.id, "name": attic.name}, {"id": area.id, "name": "Lounge"}]
    assert page["shared_total"] == 2 and page["total"] == 1 and page["rows"][0]["entity_id"] == lamp.entity_id
    hidden_area = await request(bridge, "search", area=private.id)
    assert hidden_area["rows"] == [] and hidden_area["areas"] == page["areas"]
    assert not (await request(bridge, "search", area=[area.id]))["ok"]
    hass.config_entries.async_update_entry(paired_entry, options={VIEW_ALL: False})
    empty = await request(bridge, "search", area=area.id)
    assert empty["shared_total"] == 0 and empty["areas"] == []
    await bridge.close()


async def test_search_pages_fit_with_large_area_menu(flow_hass, paired_entry):
    bridge = DesktopBridge(flow_hass, paired_entry, Socket())
    areas = [{"id": str(i), "name": "温" * 128} for i in range(128)]
    rows = [{"entity_id": f"select.input_{i}", "attributes": {"options": ["x" * 256] * 96}}
            for i in range(24)]

    async def search(query, kind, offset, limit, area_id, domain):
        return {"areas": areas.copy(), "domains": ["select"], "rows": rows[offset:offset + limit],
                "total": len(rows), "shared_total": len(rows)}

    with patch.object(bridge.catalog, "search", side_effect=search):
        first = await request(bridge, "search")
        assert first["areas"] == areas and 0 < len(first["rows"]) < len(rows)
        second = await request(bridge, "search", offset=len(first["rows"]))
        assert first["rows"] + second["rows"] == rows
        assert all(len(json.dumps(frame)) < 500_000 for frame in (first, second))
        # A large first row still fits on its own page if the optional area list shrinks.
        rows[0]["attributes"]["options"] = ["温" * 256] * 280
        large = await request(bridge, "search")
        assert large["rows"] == rows[:1] and 0 < len(large["areas"]) < len(areas)
        assert len(json.dumps(large)) < 500_000
    await bridge.close()


async def test_options_saved_action_and_revocation(flow_hass, paired_entry):
    hass = flow_hass
    calls = []

    @callback
    def press(call):
        calls.append(call.data)

    hass.services.async_register("sample", "mark", press)
    flows = hass.config_entries.options
    result = await flows.async_init(paired_entry.entry_id)
    assert "entities" in result["menu_options"]
    form = await flows.async_configure(result["flow_id"], {"next_step_id":"add_action"})
    assert form["step_id"] == "add_action"
    result = await flows.async_configure(form["flow_id"], {"name":"Mark", "sequence":[
        {"action":"sample.mark", "data":{"text":"private"}, "target":{"entity_id":["sensor.original"]}}]})
    assert result["type"] == "create_entry"
    key, = paired_entry.options[PRESETS]
    assert paired_entry.options["remote_enabled"]
    assert paired_entry.options[DESKTOP_ACCESS] == "control"
    b = DesktopBridge(hass, paired_entry, Socket())
    assert (await request(b, "search", kind="admin"))["rows"] == []
    result = await flows.async_init(paired_entry.entry_id)
    form = await flows.async_configure(result["flow_id"], {"next_step_id":"entities"})
    result = await flows.async_configure(form["flow_id"], {DESKTOP_ACCESS: "control", ADVANCED: True})
    assert result["type"] == "create_entry"
    row, = (await request(b, "search", kind="admin"))["rows"]
    assert row["entity_id"] == key and "private" not in str(row)
    binding = {"entity_id":key}
    assert not (await request(b, "action", binding=binding, action="run", data={"text":"changed"}))["ok"]
    assert (await request(b, "action", binding=binding, action="run"))["ok"]
    assert calls[0]["text"] == "private" and calls[0]["entity_id"] == ["sensor.original"]
    result = await flows.async_init(paired_entry.entry_id)
    form = await flows.async_configure(result["flow_id"], {"next_step_id":"remove_action"})
    await flows.async_configure(form["flow_id"], {"preset":key})
    assert not (await request(b, "action", binding=binding, action="run"))["ok"]
    await b.close()


async def test_detail_revocation_and_cancel(flow_hass, paired_entry):
    hass = flow_hass
    hass.states.async_set("camera.outside", "idle", {"access_token":"secret"})
    hass.config_entries.async_update_entry(paired_entry, options={VIEW:["camera.outside"]})
    bridge = DesktopBridge(hass, paired_entry, Socket())
    binding = {"entity_id":"camera.outside"}
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed(*args):
        started.set()
        await release.wait()
        return {"image":"private-image"}

    with patch("custom_components.what.desktop_bridge.detail", side_effect=delayed):
        identifier = uuid.uuid4().hex
        msg = {"op":"detail", "id":identifier, "binding":binding, "kind":"camera"}
        bridge.submit(msg)
        await asyncio.wait_for(started.wait(), 1)
        assert (await request(bridge, "cancel", target=identifier))["ok"]
        await hass.async_block_till_done()
        await asyncio.sleep(0)
        assert identifier not in bridge.cancelable
        started.clear()
        bridge.submit({**msg, "id":uuid.uuid4().hex})
        await asyncio.wait_for(started.wait(), 1)
        hass.config_entries.async_update_entry(paired_entry, options={VIEW_ALL: False})
        release.set()
        await hass.async_block_till_done()
        assert bridge.ws.frames[-1]["ok"] is False
        assert "private-image" not in str(bridge.ws.frames)
    await bridge.close()


async def test_first_button_press_and_thermostat_range(flow_hass, paired_entry):
    hass = flow_hass
    calls = []

    @callback
    def record(call):
        calls.append((call.service, call.data))

    hass.services.async_register("input_button", "press", record)
    hass.services.async_register("climate", "set_temperature", record)
    hass.states.async_set("input_button.new", "unknown")
    hass.states.async_set("climate.office", "heat", {"supported_features": 3})
    hass.config_entries.async_update_entry(paired_entry, options={CONTROL: ["input_button.new", "climate.office"]})
    bridge = DesktopBridge(hass, paired_entry, Socket())
    button = {"entity_id": "input_button.new"}
    climate = {"entity_id": "climate.office"}
    assert (await request(bridge, "action", binding=button, action="input_button.press"))["ok"]
    hass.states.async_set("input_button.new", "unavailable")
    assert not (await request(bridge, "action", binding=button, action="input_button.press"))["ok"]
    row, = (await request(bridge, "search", kind="climate"))["rows"]
    assert {a["id"] for a in row["actions"]} == {"climate.set_temperature", "climate.set_temperature_range"}
    assert (await request(bridge, "action", binding=climate, action="climate.set_temperature",
                          data={"temperature": 22}))["ok"]
    assert not (await request(bridge, "action", binding=climate, action="climate.set_temperature_range",
                              data={"target_temp_low": 24, "target_temp_high": 19}))["ok"]
    assert (await request(bridge, "action", binding=climate, action="climate.set_temperature_range",
                          data={"target_temp_low": 19, "target_temp_high": 24}))["ok"]
    assert [service for service, _ in calls] == ["press", "set_temperature", "set_temperature"]
    assert calls[-1][1]["target_temp_high"] == 24
    await bridge.close()


async def test_group_details_check_current_members_and_individual_grants(flow_hass, paired_entry):
    hass = flow_hass
    binding = {"entity_id": "group.office"}
    group, lamp, hidden, offline, secret = "group.office", "light.desk", "sensor.hidden", "sensor.offline", "input_text.secret"
    members = [lamp, hidden, offline, secret, "sensor.missing", lamp, group]
    hass.states.async_set(group, "on", {"entity_id": members})
    hass.states.async_set(lamp, "on", {"friendly_name": "Desk lamp", "access_token": "private token"})
    hass.states.async_set(hidden, "private state")
    hass.states.async_set(offline, "unavailable")
    hass.states.async_set(secret, "password", {"mode": "password"})
    hass.config_entries.async_update_entry(paired_entry, options={CONTROL: [group], VIEW: [lamp, offline, secret]})
    bridge = DesktopBridge(hass, paired_entry, Socket())
    params = {"binding": binding, "kind": "group"}
    result = await request(bridge, "detail", **params)
    assert result["data"] == {"items": [{"entity_id": lamp, "name": "Desk lamp", "state": "on", "unit": ""}]}
    assert (await request(bridge, "subscribe", bindings=[binding]))["rows"][0]["actions"] == []
    assert not (await request(bridge, "action", binding={"entity_id": lamp}, action="light.turn_off"))["ok"]
    assert not (await request(bridge, "detail", binding={"entity_id": lamp}, kind="group"))["ok"]

    async def queued_detail(change):
        async with bridge.send_lock:
            task = asyncio.create_task(request(bridge, "detail", **params))
            await asyncio.sleep(0)
            assert not task.done()
            change()
        return await task

    # A membership edit can happen independently of this pairing's grant revision.
    result = await queued_detail(lambda: hass.states.async_set(group, "on", {"entity_id": [hidden]}))
    assert result["data"] == {"items": []}
    hass.states.async_set(group, "on", {"entity_id": members})
    result = await queued_detail(lambda: hass.config_entries.async_update_entry(paired_entry, options={VIEW: [group]}))
    assert not result["ok"] and "data" not in result
    assert (await request(bridge, "detail", **params))["data"] == {"items": []}
    await bridge.close()


async def test_automatic_widget_separates_live_capabilities_from_per_pc_actions(flow_hass, paired_entry):
    hass = flow_hass
    registry = er.async_get(hass)
    attrs = {"hvac_modes": ["off", "auto", "cool", "dry", "heat", "fan_only"],
             "fan_modes": ["auto", "quiet", "low", "medium", "high"], "preset_modes": ["none", "away"],
             "swing_modes": ["up_down_auto", "highest", "middle", "normal", "lowest", "3d_auto"],
             "min_temp": 10, "max_temp": 31, "target_temp_step": 0.5}
    climate = registry.async_get_or_create("climate", "example", "air-con", suggested_object_id="air_con",
                                           capabilities=attrs, supported_features=953)
    binding = {"entity_id": climate.entity_id, "registry_id": climate.id}
    hass.states.async_set(climate.entity_id, "cool", {"current_temperature": 24.5, "temperature": 21})
    calls = []

    @callback
    def record(call):
        calls.append((call.service, dict(call.data)))

    for service in ("set_hvac_mode", "set_fan_mode", "set_preset_mode", "set_swing_mode", "set_temperature",
                    "set_temperature_range", "turn_off", "turn_on"):
        hass.services.async_register("climate", service, record)
    viewer = DesktopBridge(hass, SimpleNamespace(options={VIEW: [climate.id]}), Socket())
    controller = DesktopBridge(hass, paired_entry, Socket())
    searched, = (await request(viewer, "search"))["rows"]
    assert searched["actions"] == [] and "capabilities" not in searched
    read, = (await request(viewer, "subscribe", bindings=[binding]))["rows"]
    writable, = (await request(controller, "subscribe", bindings=[binding]))["rows"]
    assert read["actions"] == [] and read["capabilities"] == writable["actions"]
    assert "capabilities" not in writable
    assert read["attributes"]["current_temperature"] == 24.5
    assert read["attributes"]["temperature_unit"] == hass.config.units.temperature_unit
    fields = {action["id"]: action["fields"] for action in read["capabilities"]}
    assert fields["climate.set_hvac_mode"][0]["options"] == attrs["hvac_modes"]
    assert fields["climate.set_fan_mode"][0]["options"] == attrs["fan_modes"]
    assert fields["climate.set_swing_mode"][0]["options"] == attrs["swing_modes"]
    assert fields["climate.set_preset_mode"][0]["options"] == attrs["preset_modes"]
    assert "climate.set_temperature_range" not in fields
    temperature, = fields["climate.set_temperature"]
    assert (temperature["min"], temperature["max"], temperature["step"]) == (10, 31, 0.5)
    options = {"binding": binding, "action": "climate.set_temperature", "data": {"temperature": 21.5}}
    assert not (await request(viewer, "action", **options))["ok"]
    assert (await request(controller, "action", **options))["ok"]
    assert calls == [("set_temperature", {"entity_id": climate.entity_id, "temperature": 21.5})]
    # A live feature removal takes precedence over the registry. Prior grants and
    # the list previously rendered by the client cannot authorize a stale field.
    hass.states.async_set(climate.entity_id, "cool", {"supported_features": 0, "hvac_modes": ["off", "cool"]})
    changed = controller.catalog.row(binding, include_capabilities=True)
    assert {s["id"] for s in changed["actions"]} == {"climate.set_hvac_mode"}
    assert not (await request(controller, "action", **options))["ok"]
    hass.config_entries.async_update_entry(paired_entry, options={VIEW: [climate.id]})
    assert not (await request(controller, "action", binding=binding, action="climate.set_hvac_mode",
                              data={"hvac_mode": "off"}))["ok"]
    assert len(calls) == 1
    await viewer.close()
    await controller.close()


@pytest.mark.parametrize(("domain", "features", "attributes", "service", "data"), [
    ("cover", 128, {"current_tilt_position": 20}, "set_cover_tilt_position", {"tilt_position": 70}),
    ("fan", 2, {"oscillating": False}, "oscillate", {"oscillating": True}),
    ("humidifier", 1, {"available_modes": ["auto", "normal"]}, "set_mode", {"mode": "auto"}),
    ("water_heater", 2, {"operation_list": ["heat_pump", "off"]}, "set_operation_mode", {"operation_mode": "heat_pump"}),
    ("media_player", 2, {"media_duration": 90, "media_position": 24}, "media_seek", {"seek_position": 60}),
    ("vacuum", 32, {"fan_speed_list": ["quiet", "power"]}, "set_fan_speed", {"fan_speed": "quiet"}),
    ("valve", 4, {"current_position": 20}, "set_valve_position", {"position": 75}),
    ("lawn_mower", 4, {}, "dock", {}),
    ("lock", 1, {"code_format": r"^\d+$"}, "open", {"code": "1928"}),
    ("alarm_control_panel", 16, {}, "alarm_arm_custom_bypass", {}),
    ("camera", 1, {}, "turn_off", {}),
    ("image_processing", 0, {}, "scan", {}),
    ("siren", 31, {"available_tones": {1: "Chime", 2: "Alarm"}}, "turn_on",
     {"tone": "Chime", "volume_level": 0.25, "duration": 10}),
    ("calendar", 1, {}, "create_event", {"summary": "Shopping", "start_date_time": "2026-11-20T15:00:00",
                                         "end_date_time": "2026-11-20T16:00:00"}),
    ("todo", 4, {}, "update_item", {"item": "test-uid", "status": "completed"}),
    ("notify", 1, {}, "send_message", {"message": "Hello", "title": "Desk"}),
    ("assist_satellite", 1, {}, "announce", {"message": "Hello"}),
    ("remote", 4, {"activity_list": ["TV", "Game"]}, "turn_on", {"activity": "TV"}),
])
async def test_automatic_domain_action_is_scoped_and_feature_gated(flow_hass, paired_entry, domain, features,
                                                                    attributes, service, data):
    hass = flow_hass
    entity = domain + ".desk"
    initial = "unknown" if domain in ("notify", "image_processing") else "on"
    hass.states.async_set(entity, initial, {"supported_features": features, **attributes})
    calls = []

    @callback
    def record(call):
        calls.append(dict(call.data))

    hass.services.async_register(domain, service, record)
    bridge = DesktopBridge(hass, paired_entry, Socket())
    binding = {"entity_id": entity}
    key = "remote.select_activity" if domain == "remote" else domain + "." + service
    hass.config_entries.async_update_entry(paired_entry, options={DESKTOP_ACCESS: "view"})
    read, = (await request(bridge, "subscribe", bindings=[binding]))["rows"]
    assert not read["actions"]
    assert key in {a["id"] for a in read["capabilities"]}
    assert not (await request(bridge, "action", binding=binding, action=key, data=data))["ok"]
    hass.config_entries.async_update_entry(paired_entry, options={DESKTOP_ACCESS: "control"})
    assert not (await request(bridge, "action", binding=binding, action=key,
                              data={**data, "entity_id": domain + ".unshared"}))["ok"]
    assert (await request(bridge, "action", binding=binding, action=key, data=data))["ok"]
    assert calls == [{"entity_id": entity, **data}]
    await bridge.close()


async def test_unrecognised_entity_and_password_helper_have_safe_fallbacks(flow_hass, paired_entry):
    hass = flow_hass
    entity = "unrecognised.example"
    hass.states.async_set(entity, "42", {"unit_of_measurement": "rpm", "access_token": "DO-NOT-SHARE"})
    hass.states.async_set("input_text.secret", "DO-NOT-SHARE", {"mode": "password", "max": 64})
    for domain in ("unrecognised", "input_text"):
        hass.services.async_register(domain, "set_value", lambda _: None)
    hass.config_entries.async_update_entry(paired_entry, options={CONTROL: [entity, "input_text.secret"]})
    bridge = DesktopBridge(hass, paired_entry, Socket())
    rows = (await request(bridge, "subscribe", bindings=[{"entity_id": entity}, {"entity_id": "input_text.secret"}]))["rows"]
    assert rows[0]["state"] == "42" and rows[0]["unit"] == "rpm"
    assert not rows[1]["available"] and not rows[1]["attributes"]
    assert all(not row["actions"] and not row.get("capabilities") for row in rows)
    assert "DO-NOT-SHARE" not in json.dumps(rows)
    await bridge.close()


async def test_every_home_assistant_entity_platform_can_be_browsed_and_watched(flow_hass, paired_entry):
    from homeassistant.const import Platform

    hass = flow_hass
    domains = {p.value for p in Platform} | {"automation", "person", "zone", "group", "custom_widget"}
    entities = {f"{domain}.desktop_fixture" for domain in domains}
    for entity_id in entities:
        hass.states.async_set(entity_id, "test state", {"friendly_name": "Desktop fixture"})
    bridge = DesktopBridge(hass, paired_entry, Socket())
    discovered = set()
    while len(discovered) < len(entities):
        result = await request(bridge, "search", query="Desktop fixture", limit=25, offset=len(discovered))
        page = {row["entity_id"] for row in result["rows"]}
        assert page and not page & discovered
        discovered.update(page)
    assert discovered == entities
    result = await request(bridge, "subscribe", bindings=[{"entity_id": item} for item in sorted(entities)])
    assert {row["domain"] for row in result["rows"]} == domains
    assert all(row["state"] == "test state" and not row["actions"] for row in result["rows"])
    await bridge.close()


async def test_scoped_calendar_weather_and_snapshot_details(flow_hass, paired_entry):
    from homeassistant.core import SupportsResponse
    from homeassistant.components.camera import Image
    from PIL import Image as PillowImage

    hass = flow_hass
    ids = ["camera.outside", "weather.home", "calendar.home"]
    for entity_id in ids:
        hass.states.async_set(entity_id, "idle", {"supported_features": 1})
    hass.config_entries.async_update_entry(paired_entry, options={VIEW: ids})

    @callback
    def forecast(call):
        assert call.data["entity_id"] == "weather.home" and call.data["type"] == "daily"
        return {"weather.home": {"forecast": [{"temperature": 12, "condition": "sunny", "private": "hidden"}]}}

    @callback
    def calendar(call):
        assert call.data["entity_id"] == "calendar.home"
        return {"calendar.home": {"events": [{"summary":"Lunch", "description":"hidden", "start":"12:00"}]}}

    hass.services.async_register("weather", "get_forecasts", forecast, supports_response=SupportsResponse.ONLY)
    hass.services.async_register("calendar", "get_events", calendar, supports_response=SupportsResponse.ONLY)
    bridge = DesktopBridge(hass, paired_entry, Socket())
    snapshot = BytesIO()
    PillowImage.new("RGB", (1, 1), "blue").save(snapshot, "JPEG")
    image = Image(content=snapshot.getvalue(), content_type="image/jpeg")
    with patch("homeassistant.components.camera.async_get_image", return_value=image) as capture:
        reply = await request(bridge, "detail", binding={"entity_id": ids[0]}, kind="camera")
        assert reply["data"]["image"].startswith("data:image/jpeg;base64,")
        with PillowImage.open(BytesIO(base64.b64decode(reply["data"]["image"].split(",", 1)[1]))) as decoded:
            assert decoded.size == (1, 1) and decoded.getpixel((0, 0))[2] > 240
        capture.assert_awaited_once_with(hass, ids[0], width=320, height=180)
    reply = await request(bridge, "detail", binding={"entity_id": ids[1]}, kind="weather")
    assert reply["data"]["forecast"] == [{"temperature": 12, "condition": "sunny"}]
    reply = await request(bridge, "detail", binding={"entity_id": ids[2]}, kind="calendar")
    assert reply["data"]["events"] == [{"summary":"Lunch", "start":"12:00"}]
    await bridge.close()


async def test_scoped_image_and_todo_details(flow_hass, paired_entry):
    from homeassistant.components.image import Image
    from homeassistant.core import SupportsResponse
    from PIL import Image as PillowImage, PngImagePlugin

    hass = flow_hass
    photo, tasks = "image.front", "todo.chores"
    for entity_id in (photo, tasks, "image.other", "todo.other"):
        hass.states.async_set(entity_id, "1", {"entity_picture": "https://private.test/token"})
    hass.config_entries.async_update_entry(paired_entry, options={VIEW_ALL: False, VIEW: [photo, tasks]})
    bridge = DesktopBridge(hass, paired_entry, Socket())

    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Comment", "private source metadata")
    picture = BytesIO()
    PillowImage.new("RGBA", (1600, 900), (17, 54, 116, 128)).save(picture, "PNG", pnginfo=metadata)
    source = Image(content_type="image/png", content=picture.getvalue())
    with patch("homeassistant.components.image.async_get_image", return_value=source) as fetch:
        for entity_id in ("image.other", tasks):
            assert not (await request(bridge, "detail", binding={"entity_id": entity_id}, kind="image"))["ok"]
        fetch.assert_not_awaited()
        result = await request(bridge, "detail", binding={"entity_id": photo}, kind="image")
        fetch.assert_awaited_once_with(hass, photo)
        media_type, encoded = result["data"]["image"].split(",", 1)
        assert media_type == "data:image/png;base64"
        preview = base64.b64decode(encoded)
        assert len(preview) < 240_000 and b"private" not in preview
        with PillowImage.open(BytesIO(preview)) as decoded:
            assert decoded.size == (320, 180) and decoded.getpixel((0, 0))[3] == 128
        fetch.return_value = Image(content_type="image/png", content=b"<svg/>")
        assert not (await request(bridge, "detail", binding={"entity_id": photo}, kind="image"))["ok"]

    @callback
    def get_items(call):
        assert call.data["entity_id"] == tasks
        assert set(call.data["status"]) == {"needs_action", "completed"}
        entries = [{"uid": f"uid-{index}", "summary": "Shared item", "status": "completed" if index else "needs_action",
                    "description": "private description", "due": "private date"} for index in range(60)]
        return {tasks: {"items": entries}, "todo.other": {"items": [{"summary": "another home"}]}}

    hass.services.async_register("todo", "get_items", get_items, supports_response=SupportsResponse.ONLY)
    result = await request(bridge, "detail", binding={"entity_id": tasks}, kind="todo")
    items = result["data"]["items"]
    assert len(items) == 50 and result["data"]["has_more"]
    assert items[0] == {"uid": "uid-0", "summary": "Shared item", "status": "needs_action"}
    assert items[-1] == {"uid": "uid-49", "summary": "Shared item", "status": "completed"}
    assert "private" not in str(result) and "another home" not in str(result)
    assert not (await request(bridge, "detail", binding={"entity_id": "todo.other"}, kind="todo"))["ok"]
    assert not (await request(bridge, "action", binding={"entity_id": tasks}, action="todo.update_item",
                              data={"item": "uid-0", "status": "completed"}))["ok"]
    await bridge.close()


@pytest.mark.parametrize("remote", [False, True])
async def test_open_page_uses_the_selected_installation_origin(flow_hass, paired_entry, remote):
    flow_hass.config_entries.async_update_entry(paired_entry, options={VIEW: ["sensor.desk"]})
    bridge = DesktopBridge(flow_hass, paired_entry, Socket(), remote=remote)
    with patch("custom_components.what.desktop_bridge.get_url", return_value="https://house.test:8123/path?private=yes") as get:
        await bridge.start()
    get.assert_called_once_with(flow_hass, prefer_external=remote)
    assert bridge.ws.frames == [{"type": "desktop_ready", "web_url": "https://house.test:8123"}]
    await bridge.close()


async def test_saved_native_device_action(flow_hass, paired_entry):
    hass = flow_hass
    device = dr.async_get(hass).async_get_or_create(config_entry_id=paired_entry.entry_id,
        identifiers={("example", "test-switch")}, name="Office switch")
    outlet = er.async_get(hass).async_get_or_create("switch", "example", "outlet", device_id=device.id)
    calls = []

    @callback
    def on(call):
        calls.append(call.data)

    hass.services.async_register("switch", "turn_on", on)
    sequence = [{"type": "turn_on", "device_id": device.id, "entity_id": outlet.id, "domain": "switch"}]
    flow = hass.config_entries.options
    menu = await flow.async_init(paired_entry.entry_id)
    form = await flow.async_configure(menu["flow_id"], {"next_step_id": "add_action"})
    result = await flow.async_configure(form["flow_id"], {"name": "Office power", "sequence": sequence})
    assert result["type"] == "create_entry"
    key, = paired_entry.options[PRESETS]
    hass.config_entries.async_update_entry(paired_entry, options={**paired_entry.options, ADVANCED: True})
    bridge = DesktopBridge(hass, paired_entry, Socket())
    result = await request(bridge, "action", binding={"entity_id": key}, action="run")
    assert result["ok"] and len(calls) == 1
    assert outlet.entity_id in calls[0]["entity_id"]
    hass.config_entries.async_update_entry(paired_entry, options={**paired_entry.options, DESKTOP_ACCESS: "view"})
    assert not (await request(bridge, "search", kind="admin"))["rows"]
    assert not (await request(bridge, "action", binding={"entity_id": key}, action="run"))["ok"]
    assert len(calls) == 1
    await bridge.close()


async def test_read_and_control_picker_compatibility(flow_hass, paired_entry):
    hass = flow_hass
    hass.services.async_register("light", "turn_on", lambda call: None)
    hass.states.async_set("light.read", "on")
    hass.states.async_set("light.control", "off")
    hass.states.async_set("sensor.energy", "unavailable", {"state_class": "total_increasing"})
    hass.states.async_set("sensor.message", "unknown")
    hass.config_entries.async_update_entry(paired_entry, options={
        VIEW: ["light.read", "sensor.energy", "sensor.message"], CONTROL: ["light.control"],
    })
    bridge = DesktopBridge(hass, paired_entry, Socket())
    for kind, expected in (("light", ["light.control", "light.read"]),
                           ("control:light", ["light.control"]), ("numeric", ["sensor.energy"])):
        assert [r["entity_id"] for r in (await request(bridge, "search", kind=kind))["rows"]] == expected
    await bridge.close()


async def test_script_field_validation_and_uncertain_action(flow_hass, paired_entry):
    hass = flow_hass
    calls = []

    @callback
    def record(call):
        calls.append(call.data)

    hass.states.async_set("script.arrival", "off")
    hass.services.async_register("script", "arrival", record)
    hass.services.async_register("script", "turn_on", record)
    hass.config_entries.async_update_entry(paired_entry, options={CONTROL: ["script.arrival"]})
    bridge = DesktopBridge(hass, paired_entry, Socket())
    description = {"script": {"arrival": {"fields": {
        "brightness": {"required": True, "selector": {"number": {"min": 0, "max": 100}}},
        "pin": {"selector": {"text": {"type": "password"}}},
    }}}}
    binding = {"entity_id": "script.arrival"}
    with patch("custom_components.what.desktop_bridge.async_get_all_descriptions", return_value=description):
        row, = (await request(bridge, "search", kind="script"))["rows"]
        fields = {field["name"]: field for field in row["actions"][0]["fields"]}
        assert fields["pin"]["sensitive"] and fields["brightness"]["required"]
        assert not (await request(bridge, "action", binding=binding, action="script.arrival", data={}))["ok"]
        assert (await request(bridge, "action", binding=binding, action="script.arrival",
                              data={"brightness": 50}))["ok"]
        assert calls == [{"entity_id": "script.arrival", "variables": {"brightness": 50}}]
        with patch.object(type(hass.services), "async_call", side_effect=TimeoutError):
            result = await request(bridge, "action", binding=binding, action="script.arrival",
                                   data={"brightness": 75})
            assert not result["ok"] and "Check the target" in result["message"]
    await bridge.close()

