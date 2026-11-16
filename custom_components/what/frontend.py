"""Serve and automatically load WHAT's bundled dashboard cards."""
import asyncio
import hashlib
import logging
from pathlib import Path
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig

_LOGGER = logging.getLogger(__name__)
CARD_FILES = {
    "/what/display-layout-card.js": Path(__file__).parent / "frontend" / "display-layout-card.js",
    "/what/sensor-graph-card.js": Path(__file__).parent / "frontend" / "sensor-graph-card.js",
}
DATA_KEY = "what_frontend_card"
LOCK_KEY = "what_frontend_lock"


async def async_setup_frontend(hass):
    """Register once per HA instance, independently of individual PC entries."""
    # Multiple PC entries can set up concurrently. Missing assets can be retried
    # without registering a second HTTP route for cards that already loaded.
    lock = hass.data.setdefault(LOCK_KEY, asyncio.Lock())
    async with lock:
        registered = hass.data.setdefault(DATA_KEY, {})
        for route, path in CARD_FILES.items():
            if route in registered:
                continue
            try:
                content = await hass.async_add_executor_job(path.read_bytes)
            except OSError:
                _LOGGER.error("WHAT card %s is missing or unreadable; reinstall the integration", route)
                continue
            revision = hashlib.sha256(content).hexdigest()[:16]
            await hass.http.async_register_static_paths([StaticPathConfig(route, str(path), False)])
            url = f"{route}?v={revision}"
            add_extra_js_url(hass, url)
            registered[route] = url
