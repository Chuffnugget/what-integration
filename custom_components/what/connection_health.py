"""Recent connection failures per route, and repair issues for the ones the user must fix."""
from collections import deque
from datetime import datetime, timezone

import aiohttp
from homeassistant.helpers import issue_registry as ir

from .api import AuthenticationError
from .const import DOMAIN
from .protocol import IdentityMismatchError, IncompatibleProtocolError, ProtocolError

ROUTES = ("local", "remote", "mqtt")
REPAIR_REASONS = ("certificate_mismatch", "identity_mismatch", "protocol_mismatch", "invalid_data")


def failure_reason(error):
    """Return a safe category, never exception text that may contain private data."""
    for kind, reason in (
        (aiohttp.ServerFingerprintMismatch, "certificate_mismatch"),
        (IdentityMismatchError, "identity_mismatch"),
        (IncompatibleProtocolError, "protocol_mismatch"),
        (ProtocolError, "invalid_data"),
        (AuthenticationError, "authentication_rejected"),
        (TimeoutError, "timeout"),
    ):
        if isinstance(error, kind):
            return reason
    return "connection_error"


def clear_repairs(hass, entry, route=None):
    for source in (route,) if route else ROUTES:
        for reason in REPAIR_REASONS:
            ir.async_delete_issue(hass, DOMAIN, f"{entry.entry_id}_{source}_{reason}")


class ConnectionHealth:
    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        self.failures = deque(maxlen=12)
        self.active_route = None
        self.issues = set()

    def failed(self, route, error):
        reason = failure_reason(error)
        now = datetime.now(timezone.utc).isoformat()
        # Coalesce retries so a long outage cannot bury useful route changes.
        if self.failures and (self.failures[-1]["route"], self.failures[-1]["reason"]) == (route, reason):
            self.failures[-1]["last_seen"] = now
            self.failures[-1]["count"] += 1
        else:
            self.failures.append({"route": route, "reason": reason, "first_seen": now,
                                  "last_seen": now, "count": 1})
        if reason not in REPAIR_REASONS or (route, reason) in self.issues:
            return
        self.issues.add((route, reason))
        ir.async_create_issue(
            self.hass, DOMAIN, f"{self.entry.entry_id}_{route}_{reason}",
            is_fixable=False, severity=ir.IssueSeverity.ERROR, translation_key=reason,
            translation_placeholders={"name": self.entry.title},
            learn_more_url="https://github.com/Chuffnugget/what-integration#connection-recovery",
        )

    def recovered(self, route):
        # A healthy MQTT connection does not prove a changed direct certificate is trusted.
        clear_repairs(self.hass, self.entry, route)
        self.issues = {issue for issue in self.issues if issue[0] != route}
        self.active_route = route

    def diagnostics(self):
        return {"recent_failures": [dict(item) for item in self.failures],
                "attention_required": sorted({reason for _, reason in self.issues})}
