"""Base class and discovery for iCUE SDK device entities."""
import hashlib
import aiohttp
from homeassistant.exceptions import HomeAssistantError
from .entity import WhatEntity
from .device_groups import device_name


def discover(entry, async_add_entities, factory):
    coordinator = entry.runtime_data
    seen = set()
    last_signature = None
    def update():
        nonlocal last_signature
        devices = (coordinator.data or {}).get("icue", {}).get("devices", [])
        signature = tuple(
            ((d["id"], tuple(x["id"] for x in d["leds"]), tuple((p["id"], p["writable"]) for p in d["properties"])) for d in devices),
        )
        if signature == last_signature:
            return
        last_signature = signature
        added = []
        for device in devices:
            for entity in factory(coordinator, device):
                if entity.unique_id not in seen:
                    seen.add(entity.unique_id)
                    added.append(entity)
        if added:
            async_add_entities(added)
    entry.async_on_unload(coordinator.async_add_listener(update))
    update()


class IcueEntity(WhatEntity):
    def __init__(self, coordinator, device, suffix, name):
        self.icue_id = device["id"]
        token = hashlib.sha256(self.icue_id.encode()).hexdigest()[:24]
        super().__init__(coordinator, f"icue_{token}_{suffix}", name)
        self._attr_device_info = {
            "identifiers": {("what", f"{coordinator.entry.unique_id}_icue_{token}")},
            "name": device_name(coordinator.hello["name"], "icue", device["model"]), "manufacturer": "Corsair", "model": device["model"],
            "via_device_id": coordinator.parent_device_id,
        }
    @property
    def module(self):
        return (self.coordinator.data or {}).get("icue", {})
    @property
    def device(self):
        return next((d for d in self.module.get("devices", []) if d["id"] == self.icue_id), None)
    @property
    def available(self):
        return (
            self.coordinator.last_update_success
            and self.module.get("enabled", False)
            and self.module.get("connected", False)
            and self.module.get("control_enabled", False)
            and self.device is not None
        )
    def _handle_coordinator_update(self):
        value = (self.available, self.device)
        if value != getattr(self, "_previous_icue_state", None):
            self._previous_icue_state = value
            self.async_write_ha_state()
    async def send(self, action, **args):
        if not self.available:
            raise HomeAssistantError(
                "Enable the iCUE module and remote-control permission on the PC",
                translation_domain="what",
                translation_key="enable_the_icue_module_and_remote_control_permission_on",
            )
        try:
            await self.coordinator.client.command(action, {"device": self.icue_id, **args})
        except (ConnectionError, OSError, TimeoutError, aiohttp.ClientError) as err:
            raise HomeAssistantError(str(err) or "iCUE command timed out; not retried") from err
