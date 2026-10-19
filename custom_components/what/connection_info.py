"""Values for the connection diagnostic sensors."""
import ipaddress
import time

APP_SENSORS = {
    "app_uptime": "App uptime",
    "app_last_seen": "Last seen",
    "app_cpu": "WHAT CPU usage",
    "app_memory": "WHAT memory usage",
}

CONNECTION_SENSORS = {
    "connection_uptime": "Connection uptime",
    "connection_transport": "Connection transport",
    "connection_mode": "Remote access mode",
    "connection_active_mode": "Active connection mode",
    "published_entities": "Published readings",
    "unavailable_readings": "Unavailable readings",
    "beacn_unavailable_controls": "Unavailable BEACN controls",
}

def connection_info(coordinator):
    connected = coordinator.last_update_success
    transport = getattr(coordinator, "transport", "local")
    scope = "Unknown"
    if transport == "local":
        host = coordinator.entry.data.get("host", "").strip().strip("[]").lower()
        if host == "localhost":
            scope = "Localhost"
        else:
            try:
                address = ipaddress.ip_address(host)
                scope = "Localhost" if address.is_loopback else "LAN" if address.is_private else "Public address"
            except ValueError:
                scope = "Hostname (network scope unknown)"
    # Describe the observed route, never infer it from remote_enabled.
    # MQTT and unresolved hostnames do not establish an Internet/LAN route.
    active_mode = "Unknown"
    if not connected:
        active_mode = "Disconnected"
    elif transport == "remote":
        active_mode = "Remote"
    elif transport == "local":
        if scope in ("Localhost", "LAN"):
            active_mode = "Local"
        elif scope == "Public address":
            active_mode = "Online"
    started = getattr(coordinator, "connection_started", None)
    return {
        "connection_uptime": max(0, int(time.monotonic() - started)) if connected and started is not None else 0,
        "connection_transport": ({"local": "Direct TLS WebSocket", "remote": "Reverse WebSocket", "mqtt": "MQTT"}.get(
            transport,
            "Unknown",
        ) if connected else "Disconnected"),
        "connection_active_mode": active_mode,
        "connection_mode": "Remote enabled" if coordinator.entry.options.get("remote_enabled", False) else "Remote disabled",
        **coordinator.reading_counts,
        "endpoint_scope": scope if connected else "Disconnected",
    }
