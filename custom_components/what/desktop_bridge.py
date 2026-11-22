"""A per-route Desktop session with scoped subscriptions and actions."""
from __future__ import annotations

import asyncio
from collections import deque
import json
import logging
import re

from homeassistant.core import Context, SupportsResponse, callback
from homeassistant.components.device_automation import action as device_action
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.service import async_get_all_descriptions
from yarl import URL

from .desktop_catalog import ADVANCED, CONTROL, CONTROL_ALL_LIGHTS, PRESETS, DesktopCatalog, access_mode, current_id, view_all
from .desktop_controls import DesktopError, validate_fields
from .desktop_details import detail, group_members

_LOGGER = logging.getLogger(__name__)
REQUEST_ID = re.compile(r"[a-f0-9]{32}")


class DesktopBridge:
    """One authenticated, selected route. No state or pending work crosses PCs."""

    def __init__(self, hass, entry, ws, *, remote=False):
        self.hass, self.ws = hass, ws
        self.remote = remote
        self.catalog = DesktopCatalog(hass, entry)
        self.tasks = set()
        self.cancelable = {}
        self.bindings = []
        self.watched_bindings = {}
        self.unlisten = None
        self.registry_unlisten = None
        self.timer = None
        self.subscription_id = ""
        self.latest_subscription = ""
        self.initial_snapshot = ""
        self.next_flush = 0
        self.dirty = set()
        self.reindex = False
        self.refreshing = False
        self.action_ids = set()
        self.recent = deque()
        self.closed = False
        self.send_lock = asyncio.Lock()
        self._script_options = None
        self._script_listeners = []

    async def start(self):
        ready = {"type": "desktop_ready"}
        try:
            options = self.catalog.entry.options
            mode = access_mode(options)
            if (view_all(options) or self.catalog.grants()[0]
                    or (mode == "selected" and options.get(CONTROL_ALL_LIGHTS) is True)
                    or (mode != "view" and options.get(ADVANCED) is True and options.get(PRESETS))):
                url = URL(get_url(self.hass, prefer_external=self.remote))
                if url.scheme in ("https", "http") and url.host and not url.user and not url.password:
                    ready["web_url"] = str(url.origin())
        except (NoURLAvailableError, ValueError):
            pass
        await self._send(ready)

    async def close(self):
        self.closed = True
        self._clear_subscription()
        for unsubscribe in self._script_listeners:
            unsubscribe()
        self._script_listeners.clear()
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _track(self, coro):
        task = self.hass.async_create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def _send(self, message, *, revision=None, group_binding=None):
        async with self.send_lock:
            if self.closed:
                return
            if revision is not None and self.catalog.entry.options is not revision:
                if message["type"] != "desktop_result":
                    return
                message = {"type": "desktop_result", "id": message["id"], "ok": False,
                           "message": "Sharing changed. Request the current selection again."}
            elif group_binding is not None and message.get("ok"):
                # Membership, disabled entries and password modes can change while the
                # socket is busy, independently of a change to this PC's grants.
                try:
                    message["data"] = group_members(self.catalog, group_binding)
                except DesktopError as err:
                    message = {"type": "desktop_result", "id": message["id"], "ok": False, "message": str(err)}
            await self.ws.send_json(message)

    def submit(self, msg):
        identifier = msg.get("id")
        if self.closed or not isinstance(identifier, str) or not REQUEST_ID.fullmatch(identifier):
            return
        now = self.hass.loop.time()
        while self.recent and self.recent[0] <= now - 1:
            self.recent.popleft()
        if len(self.tasks) >= 12 or len(self.recent) >= 40:
            return
        self.recent.append(now)
        if identifier in self.cancelable:
            return
        if msg.get("op") == "subscribe":
            self.latest_subscription = identifier
        task = self._track(self._process(identifier, msg))
        if msg.get("op") in ("search", "detail"):
            self.cancelable[identifier] = task
            task.add_done_callback(lambda done: self.cancelable.pop(identifier, None))

    async def _process(self, identifier, msg):
        reply = {"type": "desktop_result", "id": identifier}
        extra_rows = []
        group_binding = None
        revision = self.catalog.entry.options
        try:
            async with asyncio.timeout(15):
                op = msg.get("op")
                if op in ("search", "subscribe", "action") and self._script_options is not revision:
                    mode = access_mode(revision)
                    if mode == "control" or (mode == "selected" and
                            any(current_id(self.hass, key).startswith("script.") for key in revision.get(CONTROL, ()))):
                        # Newly added scripts are automatically controllable on a full-access
                        # pairing. Invalidate their cached field schemas when services change.
                        if self.hass.services.has_service("script", "turn_on"):
                            self.catalog.descriptions = await async_get_all_descriptions(self.hass)
                        if not self._script_listeners:
                            self._script_listeners = [self.hass.bus.async_listen(event, self._script_changed)
                                                      for event in ("service_registered", "service_removed")]
                    self._script_options = revision
                if op == "search":
                    data = await self.catalog.search(msg.get("query", ""), msg.get("kind", ""),
                                                     msg.get("offset", 0), msg.get("limit", 25),
                                                     msg.get("area", ""), msg.get("domain", ""))
                    # Keep at least the first matching row when sizing the browse menus.
                    first_size = len(json.dumps(data["rows"][0])) + 2 if data["rows"] else 0
                    row_budget = 480_000
                    for choices in (data["domains"], data["areas"]):
                        for index, choice in enumerate(choices):
                            size = len(json.dumps(choice)) + 2
                            if row_budget - size < first_size:
                                del choices[index:]
                                break
                            row_budget -= size
                    data["rows"] = next(self._chunks(data["rows"], budget=row_budget), [])
                elif op == "cancel":
                    target = msg.get("target")
                    task = self.cancelable.get(target) if isinstance(target, str) else None
                    if task:
                        task.cancel()
                    data = {}
                elif op == "subscribe":
                    if self.latest_subscription and self.latest_subscription != identifier:
                        raise DesktopError("The subscription was superseded.")
                    data = self._subscribe(identifier, msg.get("bindings"))
                    chunks = iter(self._chunks(data["rows"]))
                    data["rows"] = next(chunks, [])
                    extra_rows = chunks
                elif op == "action":
                    if identifier in self.action_ids or len(self.action_ids) >= 65536:
                        raise DesktopError("Action already submitted or session limit reached.")
                    self.action_ids.add(identifier)
                    data = await self._action(msg)
                elif op == "detail":
                    entity_id, _ = self.catalog.resolve(msg.get("binding"))
                    state = self.hass.states.get(entity_id)
                    if not state or not self.catalog.row(msg["binding"])["available"]:
                        raise DesktopError("The entity is unavailable.")
                    if msg.get("kind") == "group":
                        group_binding = msg["binding"]
                        data = {}
                    else:
                        data = {"data": await detail(self.hass, state, msg.get("kind"))}
                    current = self.catalog.row(msg["binding"])
                    if current["entity_id"] != entity_id or not current["available"]:
                        raise DesktopError("The entity is unavailable.")
                else:
                    raise DesktopError("Unsupported Desktop operation.")
                reply.update(ok=True, **data)
                if len(json.dumps(reply).encode()) > 500_000:
                    raise DesktopError("This selection is too large. Choose fewer entities.")
        except DesktopError as err:
            reply = {"type": "desktop_result", "id": identifier, "ok": False, "message": str(err)[:256]}
        except TimeoutError:
            message = ("Home Assistant could not confirm the action. Check the target before trying again."
                       if msg.get("op") == "action" else "Home Assistant did not respond in time.")
            reply = {"type": "desktop_result", "id": identifier, "ok": False, "message": message}
        except Exception as err:
            # Some provider exceptions contain user data or camera credentials.
            _LOGGER.debug("WHAT Desktop request failed (%s)", type(err).__name__)
            reply = {"type": "desktop_result", "id": identifier, "ok": False,
                     "message": "Home Assistant could not complete the request. Check the entity and action settings."}
        try:
            await self._send(reply, revision=revision, group_binding=group_binding)
            for chunk in extra_rows:
                if not reply.get("ok") or self.subscription_id != identifier:
                    break
                await self._send({"type": "desktop_states", "id": identifier, "rows": chunk}, revision=revision)
        except Exception as err:
            _LOGGER.debug("WHAT Desktop reply could not be delivered (%s)", type(err).__name__)
        finally:
            if msg.get("op") == "subscribe" and self.initial_snapshot == identifier:
                self.initial_snapshot = ""
                if self.dirty or self.reindex:
                    self._schedule()

    @callback
    def _script_changed(self, event):
        if event.data.get("domain") == "script":
            self._script_options = None

    async def _action(self, msg):
        binding = msg.get("binding")
        row = self.catalog.row(binding)
        if not row["available"]:
            raise DesktopError("The entity or action is unavailable.")
        requested, data = msg.get("action"), msg.get("data", {})
        spec = next((action for action in row["actions"] if action["id"] == requested), None)
        if not spec:
            raise DesktopError("This PC has no access to this action.")
        validate_fields(spec["fields"], data)
        if row["domain"] == "action":
            preset = self.catalog.preset(row["entity_id"])
            if configured := preset.get("device_action"):
                config = await device_action.async_validate_action_config(self.hass, configured)
                if self.catalog.preset(row["entity_id"]) != preset:
                    raise DesktopError("The saved action changed.")
                await device_action.async_call_action_from_config(self.hass, config, {}, Context())
                return {"message": "Device action accepted by Home Assistant."}
            domain, service = preset["service"].split(".", 1)
            target, data = preset.get("target") or None, preset.get("data", {})
        else:
            entity_id, _ = self.catalog.resolve(binding, write=True)
            target = {"entity_id": entity_id}
            domain, service = requested.split(".", 1)
            if requested in ("light.set_effect", "remote.select_activity"):
                service = "turn_on"
            if requested == "light.turn_on" and "rgb_color" in data and "color_temp_kelvin" in data:
                raise DesktopError("Choose either a colour or a colour temperature.")
            if requested == "climate.set_temperature_range":
                if data["target_temp_low"] > data["target_temp_high"]:
                    raise DesktopError("The low temperature must be at or below the high temperature.")
                service = "set_temperature"
            if domain == "script" and service == entity_id.split(".", 1)[1]:
                service, data = "turn_on", {"variables": data}
            if requested == "automation.trigger":
                data = {"skip_condition": False}
        await self.hass.services.async_call(domain, service, data, target=target, blocking=True,
            return_response=self.hass.services.supports_response(domain, service) == SupportsResponse.ONLY)
        return {"message": "Action accepted by Home Assistant."}

    def _clear_subscription(self):
        for name in ("unlisten", "registry_unlisten", "timer"):
            cancel = getattr(self, name)
            if cancel:
                cancel.cancel() if name == "timer" else cancel()
                setattr(self, name, None)
        self.bindings = []
        self.watched_bindings.clear()
        self.subscription_id = ""
        self.initial_snapshot = ""
        self.next_flush = 0
        self.dirty.clear()
        self.reindex = False

    def _safe_row(self, binding):
        try:
            return self.catalog.row(binding, include_capabilities=True)
        except ValueError:
            # Echo only a binding the PC supplied. Do not reveal revoked entities.
            return {**binding, "state": "unavailable", "available": False, "actions": [], "attributes": {}}

    def _subscribe(self, identifier, bindings):
        if not isinstance(bindings, list) or len(bindings) > 128:
            raise DesktopError("Select at most 128 entities per installation.")
        cleaned = []
        for binding in bindings:
            if not isinstance(binding, dict) or not all(isinstance(v, str) and len(v) <= 256 for v in
                    (binding.get("entity_id", ""), binding.get("registry_id", ""))):
                raise DesktopError("Invalid entity binding.")
            cleaned.append({k: binding[k] for k in ("entity_id", "registry_id") if binding.get(k)})
        self._clear_subscription()
        self.subscription_id = identifier
        # A delta that overtakes a later snapshot chunk could restore an old value.
        self.initial_snapshot = identifier
        self.bindings = cleaned
        rows = self._watch()
        if cleaned:
            self.registry_unlisten = self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_changed)
        return {"rows": rows}

    def _watch(self):
        if self.unlisten:
            self.unlisten()
            self.unlisten = None
        rows = [self._safe_row(b) for b in self.bindings]
        # Only listen to entities actually shared with this PC.
        self.watched_bindings = {}
        for binding, row in zip(self.bindings, rows, strict=True):
            if row.get("domain") not in (None, "action"):
                self.watched_bindings.setdefault(row["entity_id"], []).append(binding)
        if self.watched_bindings:
            self.unlisten = async_track_state_change_event(self.hass, self.watched_bindings, self._changed)
        return rows

    @callback
    def _changed(self, event):
        self.dirty.add(event.data["entity_id"])
        self._schedule()

    @callback
    def _registry_changed(self, event):
        ids = {event.data.get("entity_id"), event.data.get("old_entity_id")} - {None}
        if self.watched_bindings.keys() & ids or any(b.get("entity_id") in ids for b in self.bindings):
            self.reindex = True
            self._schedule()

    @staticmethod
    def _chunks(rows, *, budget=480_000):
        chunk, size = [], 0
        for row in rows:
            row_size = len(json.dumps(row).encode()) + 2
            if chunk and size + row_size > budget:
                yield chunk
                chunk, size = [], 0
            chunk.append(row)
            size += row_size
        if chunk:
            yield chunk

    def _schedule(self):
        if self.timer is None and not self.refreshing and not self.initial_snapshot and not self.closed:
            # The first update after idle goes out immediately. A burst still
            # coalesces to the latest values at most ten times per second.
            self.timer = self.hass.loop.call_at(max(self.hass.loop.time(), self.next_flush), self._flush_later)

    @callback
    def _flush_later(self):
        self.timer = None
        self.refreshing = True
        self.next_flush = self.hass.loop.time() + 0.1
        self._track(self._flush(self.subscription_id))

    async def _flush(self, identifier):
        try:
            if identifier != self.subscription_id:
                return
            revision = self.catalog.entry.options
            changed, self.dirty = self.dirty, set()
            if self.reindex:
                self.reindex = False
                rows = self._watch()
            else:
                rows = [self._safe_row(binding) for entity_id in changed
                        for binding in self.watched_bindings.get(entity_id, ())]
            # Keep large multi-widget subscriptions below the transport envelope.
            for chunk in self._chunks(rows):
                if identifier != self.subscription_id or self.closed:
                    return
                await self._send({"type": "desktop_states", "id": identifier, "rows": chunk}, revision=revision)
        except Exception as err:
            _LOGGER.debug("WHAT Desktop state updates stopped (%s)", type(err).__name__)
        finally:
            self.refreshing = False
            if self.dirty or self.reindex:
                self._schedule()

