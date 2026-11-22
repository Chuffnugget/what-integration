"""On-demand, bounded detail views for an approved Desktop entity."""
from __future__ import annotations

import base64
from datetime import timedelta
from functools import partial
from io import BytesIO

from PIL import Image, ImageOps
from homeassistant.util import dt as dt_util

from .desktop_controls import DesktopError, numeric


def group_members(catalog, binding):
    """Read each direct member under this PC's grants, without expanding nested groups."""
    group = catalog.row(binding)
    if group["domain"] != "group" or not group["available"]:
        raise DesktopError("The group is unavailable.")
    state = catalog.hass.states.get(group["entity_id"])
    members = state.attributes.get("entity_id", ())
    items = []
    seen = {group["entity_id"]}
    if isinstance(members, (tuple, list)):
        for entity_id in members:
            if not isinstance(entity_id, str) or entity_id in seen:
                continue
            seen.add(entity_id)
            try:
                row = catalog.row({"entity_id": entity_id})
            except DesktopError:
                continue
            if row["available"] and row["domain"] != "action":
                items.append({key: row[key] for key in ("entity_id", "name", "state", "unit")})
                if len(items) == 50:
                    break
    return {"items": items}


def _image_preview(image):
    """Return a bounded still thumbnail without source metadata."""
    formats = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP", "image/gif": "GIF"}
    fmt = formats.get(image.content_type)
    if not fmt or len(image.content) > 8 * 1024 * 1024:
        raise DesktopError("The image is too large or has an unsupported format.")
    try:
        with Image.open(BytesIO(image.content), formats=[fmt]) as source:
            if source.width * source.height > 16_000_000:
                raise DesktopError("The image exceeds the preview resolution limit.")
            source.thumbnail((320, 320), Image.Resampling.LANCZOS)
            pixels = ImageOps.exif_transpose(source).convert("RGBA" if source.has_transparency_data else "RGB")
            pixels.thumbnail((320, 180), Image.Resampling.LANCZOS)
            pixels.info.clear()
            output = BytesIO()
            content_type = "image/png" if pixels.mode == "RGBA" else "image/jpeg"
            pixels.save(output, format=formats[content_type])
    except (OSError, ValueError, Image.DecompressionBombError) as err:
        raise DesktopError("The image could not be decoded.") from err
    if output.tell() > 240_000:
        raise DesktopError("The image exceeds the preview size limit.")
    return {"image": f"data:{content_type};base64," + base64.b64encode(output.getvalue()).decode("ascii")}


async def detail(hass, state, kind):
    entity_id = state.entity_id
    if kind == "history":
        from homeassistant.components.recorder import get_instance, history

        now = dt_util.utcnow()
        records = await get_instance(hass).async_add_executor_job(partial(
            history.get_significant_states, hass, now - timedelta(hours=1), now, [entity_id],
            significant_changes_only=False, no_attributes=True,
        ))
        values = records.get(entity_id, [])
        # Evenly sample the entire interval and keep its final observation.
        samples = [values[i * (len(values) - 1) // 119] for i in range(120)] if len(values) > 120 else values
        return {"points": [{"time": point.last_updated.isoformat(), "value": value}
                           for point in samples if (value := numeric(point.state)) is not None]}
    if kind in ("camera", "image") and state.domain == kind:
        if kind == "camera":
            from homeassistant.components.camera import async_get_image

            image = await async_get_image(hass, entity_id, width=320, height=180)
        else:
            from homeassistant.components.image import async_get_image

            image = await async_get_image(hass, entity_id)
        return await hass.async_add_executor_job(_image_preview, image)
    if kind == "todo" and state.domain == "todo":
        result = await hass.services.async_call("todo", "get_items", {"status": ["needs_action", "completed"]},
            target={"entity_id": entity_id}, blocking=True, return_response=True)
        entries = result[entity_id]["items"]
        items = []
        for entry in entries[:50]:
            summary, uid, status = entry.get("summary"), entry.get("uid"), entry.get("status")
            if not isinstance(summary, str) or status not in ("needs_action", "completed"):
                continue
            items.append({"summary": summary[:256], "status": status,
                          "uid": uid if isinstance(uid, str) and len(uid) <= 255 else ""})
        return {"items": items, "has_more": len(entries) > 50}
    if kind == "calendar" and state.domain == "calendar":
        now = dt_util.utcnow()
        result = await hass.services.async_call("calendar", "get_events", {
            "start_date_time": now.isoformat(), "end_date_time": (now + timedelta(days=7)).isoformat(),
        }, target={"entity_id": entity_id}, blocking=True, return_response=True)
        return {"events": [{k: str(event[k])[:256] for k in ("start", "end", "summary") if k in event}
                           for event in result[entity_id]["events"][:32]]}
    if kind == "weather" and state.domain == "weather":
        features = state.attributes.get("supported_features", 0)
        forecast_type = next((name for flag, name in ((1, "daily"), (2, "hourly"), (4, "twice_daily")) if features & flag), None)
        if not forecast_type:
            raise DesktopError("This weather entity does not provide forecasts.")
        result = await hass.services.async_call("weather", "get_forecasts", {"type": forecast_type},
            target={"entity_id": entity_id}, blocking=True, return_response=True)
        return {"forecast": [{k: row[k] for k in ("datetime", "condition", "temperature", "templow",
                             "precipitation", "precipitation_probability") if k in row}
                             for row in result[entity_id]["forecast"][:32]]}
    raise DesktopError("This detail view is unavailable for the selected entity.")

