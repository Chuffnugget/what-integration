"""Serve and automatically load WHAT's bundled dashboard card."""
import hashlib
import logging
from pathlib import Path
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig

_LOGGER = logging.getLogger(__name__)
CARD_PATH = Path(__file__).parent / "frontend" / "display-layout-card.js"
CARD_URL = "/what/display-layout-card.js"
DATA_KEY = "what_frontend_card"


async def async_setup_frontend(hass):
    """Register once per HA instance, independently of individual PC entries."""
    if DATA_KEY in hass.data:
        return
    try:
        content = await hass.async_add_executor_job(CARD_PATH.read_bytes)
    except OSError:
        _LOGGER.error(
            "WHAT display card is missing or unreadable; reinstall the integration to restore the bundled frontend",
        )
        return
    revision = hashlib.sha256(content).hexdigest()[:16]
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(CARD_PATH), False)])
    url = f"{CARD_URL}?v={revision}"
    add_extra_js_url(hass, url)
    hass.data[DATA_KEY] = url
