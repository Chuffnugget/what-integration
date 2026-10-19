"""HTTP endpoint the PC connects to for remote access (a reverse WebSocket into Home Assistant).

The PC authenticates with its pairing token, not a Home Assistant account.
"""
import asyncio
import hmac
import logging
import time
from aiohttp import web, ClientError
from .api import AuthenticationError
from homeassistant.components.http import HomeAssistantView
from .protocol import MAX_MESSAGE, ProtocolError, decode_json

_LOGGER = logging.getLogger(__name__)

REGISTRY = "what_remote_coordinators"

class RemoteView(HomeAssistantView):
    url = "/api/what/connect"
    name = "api:what:connect"
    requires_auth = False  # Authentication below is mandatory and PC-scoped.

    def __init__(self, hass):
        self.hass = hass
        self.pending = 0
        self.attempts = {}

    async def get(self, request):
        if request.headers.get("Origin") or self.pending >= 16:
            raise web.HTTPForbidden()
        now = time.monotonic()
        self.attempts = {ip: item for ip, item in self.attempts.items() if now - item[0] < 60}
        ip = request.remote or "unknown"
        start, count = self.attempts.get(ip, (now, 0))
        if count >= 20 or (ip not in self.attempts and len(self.attempts) >= 256):
            raise web.HTTPTooManyRequests()
        self.attempts[ip] = (start, count + 1)
        self.pending += 1
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=MAX_MESSAGE, compress=False)
        try:
            await ws.prepare(request)
            async with asyncio.timeout(5):
                message = await ws.receive()
                if not isinstance(message.data, str) or len(message.data) > 4096:
                    raise ValueError("Invalid auth")
                auth = decode_json(message.data)
                if (
                    not isinstance(auth, dict)
                    or set(auth) != {"type", "protocol", "device_id", "token"}
                    or auth.get("type") != "remote_auth"
                    or type(auth.get("protocol")) is not int
                    or auth["protocol"] != 2
                ):
                    raise ValueError("Invalid auth")
                device = auth.get("device_id")
                secret = auth.get("token")
                if (
                    not isinstance(device, str)
                    or len(device) > 128
                    or not isinstance(secret, str)
                    or len(secret) != 64
                ):
                    raise ValueError("Invalid auth")
                c = self.hass.data[REGISTRY].get(device)
                expected = c.entry.data.get("token", "") if c and c.entry.options.get("remote_enabled", False) else ""
                if not expected or not hmac.compare_digest(secret.encode(), expected.encode()):
                    raise ValueError("Invalid auth")
                # Shared proxy/NAT addresses must not accumulate failures for
                # successful reconnects. Failed attempts and concurrent limits remain.
                current = self.attempts.get(ip)
                if current and current[0] == start:
                    self.attempts[ip] = (start, max(0, current[1] - 1))
        except (ValueError, TypeError, TimeoutError, ConnectionError, ProtocolError):
            await ws.close(code=1008, message=b"Authentication failed")
            return ws
        finally:
            self.pending -= 1
        try:
            _LOGGER.debug("WHAT %s: authenticated reverse WebSocket offered", device)
            await c.offer_remote(ws)
        except (ConnectionError, TimeoutError, ValueError, OSError, ClientError, AuthenticationError, ProtocolError) as err:
            _LOGGER.debug("WHAT %s: reverse WebSocket ended (%s)", device, type(err).__name__)
            await ws.close(code=1008, message=b"Connection ended")
        return ws


def setup_remote(hass):
    if REGISTRY not in hass.data:
        hass.data[REGISTRY] = {}
        hass.http.register_view(RemoteView(hass))
