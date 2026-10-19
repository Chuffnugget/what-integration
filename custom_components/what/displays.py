"""Per-monitor desktop controls and named whole-PC layouts."""
import aiohttp
from homeassistant.components.select import SelectEntity
from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError
from .entity import WhatEntity


def discover_displays(entry, add, platform):
    coordinator = entry.runtime_data
    seen = set()
    def update():
        module = (coordinator.data or {}).get("displays", {})
        if not module.get("enabled"):
            return
        entities = []
        if "global" not in seen:
            seen.add("global")
            entities.extend([DisplayLayout(coordinator)] if platform == "select" else
                            [DisplayRecovery(coordinator, "keep"), DisplayRecovery(coordinator, "revert")])
        for monitor in module.get("monitors", []):
            if monitor["id"] in seen:
                continue
            seen.add(monitor["id"])
            entities.append(
                DisplayMode(coordinator, monitor) if platform == "select" else DisplayPrimary(coordinator, monitor),
            )
        if entities:
            add(entities)
    entry.async_on_unload(coordinator.async_add_listener(update))
    update()


class DisplayEntity(WhatEntity):
    _attr_icon = "mdi:monitor"
    def __init__(self, coordinator, identifier, name, monitor=None):
        super().__init__(coordinator, "windows_displays_" + identifier, name)
        self.monitor_id = monitor
    @property
    def module(self):
        return (self.coordinator.data or {}).get("displays", {})
    @property
    def monitor(self):
        return next((m for m in self.module.get("monitors", []) if m["id"] == self.monitor_id), None)
    @property
    def available(self):
        return bool(self.coordinator.last_update_success and self.module.get("enabled") and self.module.get("available")
                    and self.module.get("control_enabled") and not self.module.get("busy") and not self.module.get("pending")
                    and (self.monitor_id is None or self.monitor is not None))
    @property
    def extra_state_attributes(self):
        result = {
            "status": self.module.get("status"),
            "confirmation_pending": self.module.get("pending", False),
        }
        if self.monitor:
            result.update(
                {key: self.monitor[key] for key in ("connected", "enabled", "primary", "width", "height", "refresh", "x", "y", "rotation")},
            )
        return result
    def _handle_coordinator_update(self):
        availability = bool(self.available)
        if ("displays" in getattr(self.coordinator, "changed_modules", {"displays"})
                or availability != getattr(self, "_previous_available", None)
                or getattr(self.coordinator, "force_entity_update", False)):
            self._previous_available = availability
            self.async_write_ha_state()
    async def send(self, **args):
        if not self.available:
            raise HomeAssistantError(
                "Enable Displays and its Home Assistant control permission on the PC; resolve any interrupted/failed display change first",
                translation_domain="what",
                translation_key="enable_displays_and_its_ha_control_permission_on_the",
            )
        try:
            await self.coordinator.client.command("display", args, timeout=30)
        except (ConnectionError, OSError, TimeoutError, aiohttp.ClientError) as err:
            raise HomeAssistantError(
                (
                    str(err)
                    or "Display command timed out; not retried. Check the PC and its recovery notice."
                ),
            ) from err


class DisplayMode(DisplayEntity, SelectEntity):
    def __init__(self, coordinator, monitor):
        super().__init__(
            coordinator,
            monitor["id"] + "_mode",
            monitor["name"] + " desktop mode",
            monitor["id"],
        )
    @property
    def choices(self):
        return {"Disconnected": {"operation": "disconnect"}, "Extended": {"operation": "extend"},
                **{"Mirror " + m["name"]: {"operation": "mirror", "source": m["id"]}
                   for m in self.module.get("monitors", []) if m["id"] != self.monitor_id and m["enabled"]}}
    @property
    def options(self):
        return list(self.choices)
    @property
    def current_option(self):
        monitor = self.monitor
        if monitor is None:
            return None
        if not monitor["enabled"]:
            return "Disconnected"
        for m in self.module.get("monitors", []):
            if m["enabled"] and m["id"] != self.monitor_id and m["group"] == monitor["group"]:
                return "Mirror " + m["name"]
        return "Extended"
    async def async_select_option(self, option):
        if option not in self.choices:
            raise HomeAssistantError(
                "This monitor mode is no longer available",
                translation_domain="what",
                translation_key="this_monitor_mode_is_no_longer_available",
            )
        await self.send(monitor=self.monitor_id, **self.choices[option])


class DisplayPrimary(DisplayEntity, ButtonEntity):
    def __init__(self, coordinator, monitor):
        super().__init__(
            coordinator,
            monitor["id"] + "_primary",
            monitor["name"] + " make primary",
            monitor["id"],
        )
    @property
    def available(self):
        return super().available and self.monitor["enabled"] and not self.monitor["primary"]
    async def async_press(self):
        await self.send(operation="primary", monitor=self.monitor_id)


class DisplayLayout(DisplayEntity, SelectEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, "layout", "Display layout")
    @property
    def choices(self):
        return {l["name"]: l["id"] for l in self.module.get("layouts", []) if l["available"]}
    @property
    def options(self):
        return list(self.choices)
    @property
    def available(self):
        return super().available and bool(self.choices)
    @property
    def current_option(self):
        return next(
            (name for name, identifier in self.choices.items() if identifier == self.module.get("active_layout")),
            None,
        )
    async def async_select_option(self, option):
        if option not in self.choices:
            raise HomeAssistantError(
                "Layout is missing or a required monitor is disconnected",
                translation_domain="what",
                translation_key="layout_is_missing_or_a_required_monitor_is_disconnected",
            )
        await self.send(operation="restore", layout_id=self.choices[option])


class DisplayRecovery(DisplayEntity, ButtonEntity):
    def __init__(self, coordinator, operation):
        super().__init__(
            coordinator,
            operation,
            "Keep display layout" if operation == "keep" else "Restore previous display layout",
        )
        self.operation = operation
    @property
    def available(self):
        return bool(self.coordinator.last_update_success and self.module.get("enabled") and self.module.get("control_enabled")
                    and self.module.get("pending") and not self.module.get("busy"))
    async def async_press(self):
        await self.send(operation=self.operation)
