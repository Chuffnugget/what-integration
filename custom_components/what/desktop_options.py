"""Administrator-managed Desktop permissions and fixed actions for one paired PC."""
from __future__ import annotations

import json
import uuid

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.device_automation import action as device_action
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from .desktop_catalog import (
    ADVANCED, CONTROL, CONTROL_ALL_LIGHTS, DESKTOP_ACCESS, PRESETS, VIEW, VIEW_ALL,
    access_mode, current_id, stable_keys, view_all,
)


def access_selector():
    return selector.SelectSelector(selector.SelectSelectorConfig(
        options=["control", "view", "selected"], translation_key="desktop_access",
    ))


class DesktopOptionsFlow(config_entries.OptionsFlow):
    _editing = None
    _sharing = None

    def _save(self, **changes):
        options = self.config_entry.options
        # Adding the first preset must preserve a legacy pairing's effective grants.
        changes.setdefault(DESKTOP_ACCESS, access_mode(options))
        return self.async_create_entry(title="", data={**options, **changes})

    async def async_step_init(self, user_input=None):
        steps = ["entities", "add_action"]
        if self.config_entry.options.get(PRESETS):
            steps.extend(("edit_action", "remove_action"))
        return self.async_show_menu(step_id="init", menu_options=steps)

    async def async_step_entities(self, user_input=None):
        options = self.config_entry.options
        if user_input is not None:
            self._sharing = {
                DESKTOP_ACCESS: user_input[DESKTOP_ACCESS],
                VIEW_ALL: user_input.get(VIEW_ALL, view_all(options)),
                VIEW: stable_keys(self.hass, user_input.get(VIEW, [])),
                ADVANCED: user_input.get(ADVANCED, False),
            }
            if self._sharing[DESKTOP_ACCESS] == "selected":
                return await self.async_step_selected_controls()
            return self._save(**self._sharing)
        pick = selector.EntitySelector(selector.EntitySelectorConfig(multiple=True))
        schema = vol.Schema({
            vol.Required(DESKTOP_ACCESS, default=access_mode(options)): access_selector(),
            vol.Required(VIEW_ALL, default=view_all(options)): selector.BooleanSelector(),
            vol.Optional(VIEW): pick,
            vol.Required(ADVANCED, default=bool(options.get(ADVANCED))): selector.BooleanSelector(),
        })
        # A cleared optional selector is omitted by the frontend. A schema default
        # would silently restore the previously selected entities on submission.
        return self.async_show_form(step_id="entities", data_schema=self.add_suggested_values_to_schema(
            schema, {VIEW: self._entity_ids(VIEW)}))

    def _entity_ids(self, key):
        return [entity_id for saved in self.config_entry.options.get(key, [])
                if (entity_id := current_id(self.hass, saved))]

    async def async_step_selected_controls(self, user_input=None):
        if user_input is not None:
            return self._save(**self._sharing, **{
                CONTROL: stable_keys(self.hass, user_input.get(CONTROL, [])),
                CONTROL_ALL_LIGHTS: user_input.get(CONTROL_ALL_LIGHTS, False),
            })
        schema = vol.Schema({
            vol.Optional(CONTROL): selector.EntitySelector(selector.EntitySelectorConfig(multiple=True)),
            vol.Required(CONTROL_ALL_LIGHTS, default=self.config_entry.options.get(CONTROL_ALL_LIGHTS) is True):
                selector.BooleanSelector(),
        })
        return self.async_show_form(step_id="selected_controls", data_schema=self.add_suggested_values_to_schema(
            schema, {CONTROL: self._entity_ids(CONTROL)}))

    async def async_step_add_action(self, user_input=None):
        errors = {}
        if user_input is not None:
            sequence = user_input.get("sequence")
            action = sequence[0] if isinstance(sequence, list) and len(sequence) == 1 and isinstance(sequence[0], dict) else {}
            name = user_input["name"].strip()
            try:
                if not name or len(name) > 100 or len(json.dumps(action)) > 8192:
                    raise ValueError
                preset = await self._validate_action(action)
                presets = dict(self.config_entry.options.get(PRESETS, {}))
                if len(presets) >= 128 and self._editing is None:
                    errors["base"] = "preset_limit"
                else:
                    presets[self._editing or "action." + uuid.uuid4().hex] = {"name": name, **preset}
                    return self._save(**{PRESETS: presets})
            except (ValueError, TypeError, HomeAssistantError, vol.Invalid):
                errors["base"] = "invalid_preset"
        fields = {
            vol.Required("name"): selector.TextSelector(),
            vol.Required("sequence"): selector.ActionSelector(),
        }
        preset = self.config_entry.options.get(PRESETS, {}).get(self._editing)
        defaults = {}
        if preset:
            action = preset.get("device_action") or {"action": preset["service"], "target": preset["target"],
                                                     "data": preset["data"]}
            defaults = {"name": preset["name"], "sequence": [action]}
        schema = self.add_suggested_values_to_schema(vol.Schema(fields), user_input or defaults)
        return self.async_show_form(step_id="add_action", data_schema=schema, errors=errors)

    async def _validate_action(self, action):
        if "action" not in action and "service" not in action and "device_id" in action:
            await device_action.async_validate_action_config(self.hass, action)
            return {"device_action": action}
        service = action.get("action") or action.get("service") or ""
        data, target = action.get("data", {}), action.get("target", {})
        if (not isinstance(service, str) or service.count(".") != 1
                or not self.hass.services.has_service(*service.split("."))
                or set(action) - {"action", "service", "data", "target", "metadata", "alias"}
                or not isinstance(data, dict) or not isinstance(target, dict)):
            raise ValueError
        return {"service": service, "data": data, "target": target}

    async def async_step_edit_action(self, user_input=None):
        if user_input is not None:
            self._editing = user_input["preset"]
            return await self.async_step_add_action()
        return self._choose_action("edit_action")

    async def async_step_remove_action(self, user_input=None):
        presets = dict(self.config_entry.options.get(PRESETS, {}))
        if user_input is not None:
            presets.pop(user_input["preset"], None)
            return self._save(**{PRESETS: presets})
        return self._choose_action("remove_action")

    def _choose_action(self, step):
        choices = [{"value": key, "label": row["name"]}
                   for key, row in self.config_entry.options.get(PRESETS, {}).items()]
        return self.async_show_form(step_id=step, data_schema=vol.Schema({
            vol.Required("preset"): selector.SelectSelector(selector.SelectSelectorConfig(options=choices)),
        }))

