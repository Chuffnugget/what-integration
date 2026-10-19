"""Entity cleanup: preview stale entities, then remove them on request (from HA or the PC)."""
import asyncio
import logging
import time
import uuid

from .entity_cleanup import cleanup_candidates, selection_is_fresh

_LOGGER = logging.getLogger(__name__)
_ERRORS = {
    "fresh_selection_required": "Connect WHAT using local sharing and request a fresh preview.",
    "preview_expired": "The cleanup preview has expired. Request a new preview before removing entities.",
}


class EntityMaintenance:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.seen = set()
        self.previews = {}
        self.task = None
        self.pending_request = None
        self.device_preview = None
        self.state = {"status": "Not checked", "count": None, "entities": []}
        self._expiry = None

    @property
    def can_remove(self):
        preview = self.previews.get(self.device_preview)
        return bool(preview and preview[0] > time.monotonic() and preview[1]
                    and selection_is_fresh(self.coordinator))

    def _clear_previews(self):
        self.previews.clear()
        self.device_preview = None
        if self._expiry:
            self._expiry.cancel()
            self._expiry = None

    def _expire(self):
        self._clear_previews()
        self.state = {"status": "Preview expired", "count": None, "entities": []}
        self.coordinator.async_update_listeners()

    def _execute(self, operation, token=None):
        from homeassistant.helpers import entity_registry as er

        registry = er.async_get(self.coordinator.hass)
        entries = er.async_entries_for_config_entry(registry, self.coordinator.entry.entry_id)
        current = set(cleanup_candidates(self.coordinator, entries))
        if operation == "preview":
            self._clear_previews()
            token = uuid.uuid4().hex
            self.previews[token] = (time.monotonic() + 120, current)
            self._expiry = asyncio.get_running_loop().call_later(120, self._expire)
            result = {"status": "preview", "token": token, "count": len(current),
                      "entities": sorted(current)[:20]}
            self.state = {"status": "Review required" if current else "Nothing to remove",
                          "count": len(current), "entities": result["entities"]}
            return result

        preview = self.previews.get(token)
        if preview is None or preview[0] <= time.monotonic():
            raise ValueError("preview_expired")
        # Recheck the selection and never expand the set that the user reviewed.
        remove = current & preview[1]
        self._clear_previews()
        for entity_id in sorted(remove):
            registry.async_remove(entity_id)
        self.state = {"status": "Removed", "count": 0, "entities": [], "removed": len(remove)}
        return {"status": "removed", "count": len(remove)}

    async def async_device_action(self, operation):
        from homeassistant.exceptions import HomeAssistantError

        try:
            result = self._execute(operation, self.device_preview)
        except ValueError as err:
            self._expire()
            raise HomeAssistantError(_ERRORS.get(str(err), "Cleanup could not be completed.")) from err
        if operation == "preview":
            self.device_preview = result["token"]
        self.coordinator.async_update_listeners()
        if operation == "remove" and result["count"]:
            await self.coordinator.hass.config_entries.async_reload(self.coordinator.entry.entry_id)

    def receive(self, request):
        if not isinstance(request, dict):
            return
        identifier = request.get("id")
        if (not isinstance(identifier, str) or len(identifier) != 32
                or any(c not in "0123456789abcdef" for c in identifier)
                or identifier in self.seen or request.get("operation") not in {"preview", "remove"}):
            return
        if self.task and not self.task.done():
            self.pending_request = dict(request)
            return
        if len(self.seen) >= 128:
            self.seen.clear()
        self.seen.add(identifier)
        self.task = self.coordinator.hass.async_create_task(self._run(dict(request)))
        self.task.add_done_callback(self._finished)

    def _finished(self, task):
        if task.cancelled():
            return
        if error := task.exception():
            _LOGGER.error("WHAT cleanup task failed", exc_info=(type(error), error, error.__traceback__))
        request, self.pending_request = self.pending_request, None
        if request:
            self.receive(request)

    def stop(self):
        self.pending_request = None
        if self.task and not self.task.done():
            self.task.cancel()
        self._clear_previews()

    async def _run(self, request):
        result = {"request_id": request["id"], "status": "error", "count": 0}
        try:
            token = request.get("token")
            if request["operation"] == "remove" and not isinstance(token, str):
                raise ValueError("preview_expired")
            result.update(self._execute(request["operation"], token))
        except ValueError as err:
            result["message"] = _ERRORS.get(str(err), "Cleanup could not be completed.")
        except Exception:
            result["message"] = "Cleanup failed. Check Home Assistant logs before trying again."
            _LOGGER.exception("WHAT entity cleanup failed")
        self.coordinator.async_update_listeners()
        try:
            await self.coordinator.client.command("entity_cleanup_result", result)
        except (ConnectionError, TimeoutError, OSError) as err:
            # Report a lost result, but never retry a removal automatically.
            _LOGGER.warning("Could not deliver WHAT cleanup result (%s): %s", result["status"], err)
        if result["status"] == "removed" and result["count"]:
            self.coordinator.hass.async_create_task(
                self.coordinator.hass.config_entries.async_reload(self.coordinator.entry.entry_id),
            )
