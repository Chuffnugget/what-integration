"""Exercise HA's flow manager, including credentials and rediscovery races."""
from ipaddress import ip_address
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from conftest import HELLO, PAIRING

pytestmark = pytest.mark.asyncio
DETAILS = {"device_id": HELLO["device_id"], "name": HELLO["name"]}


def form_defaults(result):
    return {
        str(key): key.description["suggested_value"] if key.description else key.default()
        for key in result["data_schema"].schema if str(key) != "token"
    }


async def choose_connection(hass, result, mode="api"):
    """Use the same protocol selection step as the setup UI."""
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"connection": mode})
    assert result["step_id"] == mode
    return result


def discovery(device_id="fixture-pc", address="192.0.2.20"):
    ip = ip_address(address)
    return ZeroconfServiceInfo(
        ip_address=ip, ip_addresses=[ip], port=9435, hostname="fixture.local.",
        type="_what._tcp.local.", name="Fixture PC._what._tcp.local.",
        properties={"id": device_id, "version": "2", "tls": "1"},
    )


async def test_manual_pairing_and_duplicate(flow_hass, pc_client):
    flow = flow_hass.config_entries.flow
    result = await flow.async_init("what", context={"source": "user"})
    assert result["step_id"] == "user"
    selector = next(iter(result["data_schema"].schema.values()))
    assert selector.config["options"] == ["api", "mqtt"]
    result = await choose_connection(flow_hass, result)
    result = await flow.async_configure(result["flow_id"], PAIRING | DETAILS)
    assert result["type"] == "create_entry"
    entry = result["result"]
    assert entry.unique_id == HELLO["device_id"]
    assert entry.data == PAIRING
    assert entry.title == HELLO["name"]
    pc_client.return_value.close.assert_awaited_once()

    result = await flow.async_init("what", context={"source": "user"})
    result = await choose_connection(flow_hass, result)
    result = await flow.async_configure(result["flow_id"], PAIRING | DETAILS)
    assert result["reason"] == "already_configured"
    assert len(flow_hass.config_entries.async_entries("what")) == 1


@pytest.mark.parametrize("failure,expected", [
    ("auth", "invalid_auth"), ("certificate", "fingerprint_mismatch"),
    ("protocol", "invalid_protocol"), ("identity", "invalid_protocol"),
    ("offline", "cannot_connect"), ("timeout", "cannot_connect"),
    ("input", "invalid_input"), ("unexpected", "unknown"),
])
async def test_failed_pairing_retains_entry(flow_hass, paired_entry, pc_client, failure, expected):
    from custom_components.what.api import AuthenticationError
    from custom_components.what.protocol import IdentityMismatchError, IncompatibleProtocolError

    errors = {
        "auth": AuthenticationError("Rejected"),
        "certificate": aiohttp.ServerFingerprintMismatch(b"old", b"new", "192.0.2.20", 9435),
        "protocol": IncompatibleProtocolError("Protocol version"),
        "identity": IdentityMismatchError("Different PC"),
        "offline": ConnectionError("Offline"), "timeout": TimeoutError(),
        "unexpected": RuntimeError("Unexpected test failure"),
    }
    submitted = PAIRING | {"host": "192.0.2.20", "token": "b" * 64}
    if failure == "input":
        submitted["fingerprint"] = "not-a-fingerprint"
    else:
        pc_client.return_value.connect.side_effect = errors[failure]
    result = await flow_hass.config_entries.flow.async_init(
        "what", context={"source": "reconfigure", "entry_id": paired_entry.entry_id},
    )
    result = await choose_connection(flow_hass, result)
    result = await flow_hass.config_entries.flow.async_configure(result["flow_id"], submitted | DETAILS)
    assert result["errors"] == {"base": expected}
    assert paired_entry.data == PAIRING
    assert paired_entry.unique_id == HELLO["device_id"]
    # Defaults may retain the address, never the token from the failed attempt.
    defaults = form_defaults(result)
    assert defaults["host"] == submitted["host"]
    assert "b" * 64 not in repr(result["data_schema"].schema)
    if failure != "input":
        pc_client.return_value.close.assert_awaited_once()


@pytest.mark.parametrize("source", ["reauth", "reconfigure"])
async def test_pairing_recovery_preserves_customizations(flow_hass, paired_entry, pc_client, source):
    devices = dr.async_get(flow_hass)
    device = devices.async_get_or_create(
        config_entry_id=paired_entry.entry_id, identifiers={("what", HELLO["device_id"])},
        name="Fixture PC",
    )
    registry = er.async_get(flow_hass)
    entity = registry.async_get_or_create(
        "sensor", "what", HELLO["device_id"] + "_app_cpu", config_entry=paired_entry,
        device_id=device.id, suggested_object_id="my_cpu",
    )
    registry.async_update_entity(entity.entity_id, name="My custom name")
    original_options = dict(paired_entry.options)
    updated = PAIRING | {"token": "b" * 64, "fingerprint": "11" * 32}
    with patch.object(flow_hass.config_entries, "async_schedule_reload") as reload:
        result = await flow_hass.config_entries.flow.async_init(
            "what", context={"source": source, "entry_id": paired_entry.entry_id},
            data=dict(paired_entry.data) if source == "reauth" else None,
        )
        result = await choose_connection(flow_hass, result)
        result = await flow_hass.config_entries.flow.async_configure(result["flow_id"], updated | DETAILS)
        assert result["reason"] == source + "_successful"
        reload.assert_called_once_with(paired_entry.entry_id)
    assert paired_entry.data == updated
    assert paired_entry.unique_id == HELLO["device_id"]
    assert paired_entry.options == original_options | {
        "direct_enabled": True, "remote_enabled": True, "mqtt_enabled": False,
        "preferred_transport": "local",
    }
    assert devices.async_get(device.id).identifiers == {("what", HELLO["device_id"])}
    assert registry.async_get(entity.entity_id).name == "My custom name"
    assert registry.async_get(entity.entity_id).unique_id == entity.unique_id
    assert pc_client.call_args.kwargs["device_id"] == HELLO["device_id"]


async def test_reconfigure_rejects_different_identity(flow_hass, paired_entry, pc_client):
    # Even if a client unexpectedly accepts it, the flow must not replace an ID.
    pc_client.return_value.connect.return_value = (HELLO | {"device_id": "other-pc"}, {})
    result = await flow_hass.config_entries.flow.async_init(
        "what", context={"source": "reconfigure", "entry_id": paired_entry.entry_id},
    )
    result = await choose_connection(flow_hass, result)
    result = await flow_hass.config_entries.flow.async_configure(result["flow_id"], PAIRING | DETAILS)
    assert result["errors"] == {"base": "invalid_protocol"}
    assert paired_entry.data == PAIRING
    assert paired_entry.unique_id == HELLO["device_id"]


async def test_discovery_requires_pairing(flow_hass, pc_client):
    result = await flow_hass.config_entries.flow.async_init(
        "what", context={"source": "zeroconf"}, data=discovery(),
    )
    assert result["step_id"] == "user"
    pc_client.assert_not_called()
    result = await choose_connection(flow_hass, result)
    defaults = form_defaults(result)
    assert defaults["device_id"] == HELLO["device_id"]
    assert defaults["host"] == "192.0.2.20"
    result = await flow_hass.config_entries.flow.async_configure(result["flow_id"], PAIRING | DETAILS)
    assert result["type"] == "create_entry"
    assert pc_client.call_args.kwargs["device_id"] == HELLO["device_id"]


@pytest.mark.parametrize("case", ["verified", "unverified", "unchanged", "pairing_changed"])
async def test_discovery_address_update(flow_hass, paired_entry, pc_client, case):
    if case == "unverified":
        pc_client.return_value.connect.side_effect = aiohttp.ServerFingerprintMismatch(
            b"old", b"new", "192.0.2.20", 9435,
        )
    elif case == "pairing_changed":
        async def change_pairing():
            flow_hass.config_entries.async_update_entry(paired_entry, data=PAIRING | {"token": "b" * 64})
            return dict(HELLO), {}
        pc_client.return_value.connect.side_effect = change_pairing
    listener = AsyncMock()
    paired_entry.add_update_listener(listener)
    info = discovery(address=PAIRING["host"] if case == "unchanged" else "192.0.2.20")
    result = await flow_hass.config_entries.flow.async_init(
        "what", context={"source": "zeroconf"}, data=info,
    )
    assert result["reason"] == "already_configured"
    assert paired_entry.data["host"] == ("192.0.2.20" if case == "verified" else PAIRING["host"])
    assert paired_entry.data["token"] == ("b" * 64 if case == "pairing_changed" else PAIRING["token"])
    assert paired_entry.data["fingerprint"] == PAIRING["fingerprint"]
    if case == "unchanged":
        pc_client.assert_not_called()
    else:
        assert pc_client.call_args.kwargs == PAIRING | {"host": "192.0.2.20", "device_id": HELLO["device_id"]}
        pc_client.return_value.close.assert_awaited_once()


async def test_discovery_without_identity(flow_hass, pc_client):
    result = await flow_hass.config_entries.flow.async_init(
        "what", context={"source": "zeroconf"}, data=discovery(device_id=""),
    )
    assert result["reason"] == "invalid_discovery"
    pc_client.assert_not_called()


@pytest.mark.parametrize("mode", ["api", "mqtt"])
async def test_pair_without_direct_pc_access(flow_hass, pc_client, mode):
    result = await flow_hass.config_entries.flow.async_init("what", context={"source": "user"})
    result = await choose_connection(flow_hass, result, mode)
    if mode == "mqtt":
        assert {str(key) for key in result["data_schema"].schema} == {"device_id", "name", "token", "fingerprint"}
    submitted = {"token": PAIRING["token"], "fingerprint": PAIRING["fingerprint"],
                 "device_id": "another-house-pc", "name": "Office PC"}
    result = await flow_hass.config_entries.flow.async_configure(result["flow_id"], submitted)
    assert result["type"] == "create_entry"
    entry = result["result"]
    assert entry.unique_id == "another-house-pc"
    assert entry.data["token"] == PAIRING["token"]
    assert entry.options == {
        "direct_enabled": False, "remote_enabled": mode == "api", "mqtt_enabled": mode == "mqtt",
        "preferred_transport": "mqtt" if mode == "mqtt" else "local",
    }
    assert entry.data["host"] == "localhost"
    pc_client.assert_not_called()


@pytest.mark.parametrize("mode", ["api", "mqtt"])
async def test_pairing_rejects_invalid_details(flow_hass, pc_client, mode):
    result = await flow_hass.config_entries.flow.async_init("what", context={"source": "user"})
    result = await choose_connection(flow_hass, result, mode)
    submitted = {"token": PAIRING["token"], "fingerprint": PAIRING["fingerprint"],
                 "device_id": "../wrong", "name": "PC"}
    result = await flow_hass.config_entries.flow.async_configure(result["flow_id"], submitted)
    assert result["errors"] == {"base": "invalid_input"}
    assert not flow_hass.config_entries.async_entries("what")
    pc_client.assert_not_called()


async def test_remote_reconfigure_keeps_identity(flow_hass, paired_entry, pc_client):
    result = await flow_hass.config_entries.flow.async_init(
        "what", context={"source": "reconfigure", "entry_id": paired_entry.entry_id})
    result = await choose_connection(flow_hass, result, "mqtt")
    submitted = {"token": PAIRING["token"], "fingerprint": PAIRING["fingerprint"], **DETAILS}
    with patch.object(flow_hass.config_entries, "async_schedule_reload"):
        result = await flow_hass.config_entries.flow.async_configure(result["flow_id"], submitted)
    assert result["reason"] == "reconfigure_successful"
    assert paired_entry.unique_id == HELLO["device_id"]
    assert paired_entry.options["mqtt_enabled"]
    assert paired_entry.options["direct_enabled"] is False
    pc_client.assert_not_called()


@pytest.mark.parametrize("source", ["reauth", "reconfigure"])
@pytest.mark.parametrize("options,mode,host", [
    ({}, "api", PAIRING["host"]),
    ({"direct_enabled": False, "remote_enabled": True}, "api", ""),
    ({"direct_enabled": False, "mqtt_enabled": True}, "mqtt", ""),
    ({"direct_enabled": True, "mqtt_enabled": True, "preferred_transport": "mqtt"}, "mqtt", ""),
])
async def test_existing_routes_keep_pairing_defaults(flow_hass, paired_entry, pc_client, source, options, mode, host):
    flow_hass.config_entries.async_update_entry(paired_entry, options=options)
    result = await flow_hass.config_entries.flow.async_init(
        "what", context={"source": source, "entry_id": paired_entry.entry_id},
        data=dict(paired_entry.data) if source == "reauth" else None,
    )
    connection = next(iter(result["data_schema"].schema))
    assert connection.default() == mode
    result = await choose_connection(flow_hass, result, mode)
    defaults = form_defaults(result)
    assert defaults["device_id"] == paired_entry.unique_id
    assert defaults["name"] == paired_entry.title
    assert defaults.get("host", "") == host
    assert "token" not in defaults
    # Merely opening setup must not change the stored route or credentials.
    assert paired_entry.options == options
    assert paired_entry.data == PAIRING

    with patch.object(flow_hass.config_entries, "async_schedule_reload"):
        result = await flow_hass.config_entries.flow.async_configure(
            result["flow_id"], defaults | {"token": "b" * 64},
        )
    assert result["reason"] == source + "_successful"
    assert paired_entry.unique_id == HELLO["device_id"]
    assert paired_entry.data["token"] == "b" * 64
    if not host:
        pc_client.assert_not_called()


@pytest.mark.parametrize("mode", ["api", "mqtt"])
async def test_offline_pairing_cannot_replace_another_pc(flow_hass, paired_entry, pc_client, mode):
    result = await flow_hass.config_entries.flow.async_init(
        "what", context={"source": "reconfigure", "entry_id": paired_entry.entry_id},
    )
    result = await choose_connection(flow_hass, result, mode)
    submitted = {"device_id": "another-pc", "name": "PC", "token": PAIRING["token"],
                 "fingerprint": PAIRING["fingerprint"]}
    if mode == "api":
        submitted["host"] = ""
    result = await flow_hass.config_entries.flow.async_configure(result["flow_id"], submitted)
    assert result["reason"] == "unique_id_mismatch"
    assert paired_entry.data == PAIRING
    assert paired_entry.unique_id == HELLO["device_id"]
    pc_client.assert_not_called()


async def test_connection_labels_are_translated(flow_hass):
    from homeassistant.helpers.translation import async_get_translations

    labels = await async_get_translations(flow_hass, "en", "selector", {"what"})
    assert labels["component.what.selector.connection.options.api"] == "WHAT API (Recommended)"
    assert labels["component.what.selector.connection.options.mqtt"] == "MQTT"


async def test_clearing_local_address_switches_to_incoming_api(flow_hass, paired_entry, pc_client):
    result = await flow_hass.config_entries.flow.async_init(
        "what", context={"source": "reconfigure", "entry_id": paired_entry.entry_id},
    )
    result = await choose_connection(flow_hass, result)
    # The frontend omits an optional text field when the user clears it.
    submitted = {"token": PAIRING["token"], "fingerprint": PAIRING["fingerprint"], **DETAILS}
    submitted = result["data_schema"](submitted)
    assert "host" not in submitted
    with patch.object(flow_hass.config_entries, "async_schedule_reload"):
        result = await flow_hass.config_entries.flow.async_configure(result["flow_id"], submitted)
    assert result["reason"] == "reconfigure_successful"
    assert paired_entry.data["host"] == "localhost"
    assert paired_entry.options["direct_enabled"] is False
    assert paired_entry.options["remote_enabled"] is True
    assert paired_entry.unique_id == HELLO["device_id"]
    pc_client.assert_not_called()


async def test_mqtt_requires_configured_broker(flow_hass, pc_client):
    flow_hass.config.components.remove("mqtt")
    submitted = {"token": PAIRING["token"], "fingerprint": PAIRING["fingerprint"]} | DETAILS
    flow = flow_hass.config_entries.flow
    result = await flow.async_init("what", context={"source": "user"})
    result = await choose_connection(flow_hass, result, "mqtt")
    assert result["errors"] == {"base": "mqtt_not_configured"}
    result = await flow.async_configure(result["flow_id"], submitted)
    assert result["errors"] == {"base": "mqtt_not_configured"}
    assert not flow_hass.config_entries.async_entries("what")
    flow_hass.config.components.add("mqtt")
    result = await flow.async_configure(result["flow_id"], submitted)
    assert result["type"] == "create_entry"
    pc_client.assert_not_called()


@pytest.mark.parametrize("field,value", [("version", "99"), ("version", ""), ("tls", "0")])
async def test_discovery_rejects_incompatible_service(flow_hass, pc_client, field, value):
    service = discovery()
    service.properties[field] = value
    result = await flow_hass.config_entries.flow.async_init(
        "what", context={"source": "zeroconf"}, data=service,
    )
    assert result["reason"] == "incompatible_discovery"
    assert not flow_hass.config_entries.async_entries("what")
    pc_client.assert_not_called()
