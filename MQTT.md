# MQTT Transport and Sensor Sharing

WHAT keeps its existing Home Assistant integration and entities. WebSockets and MQTT carry the same WHAT protocol underneath that integration. This is ordinary MQTT, not Zigbee2MQTT and not MQTT Discovery. Switching does not rename devices/entities or replace their unique IDs, so existing automations, dashboards and history remain attached.

Home Assistant API is recommended for most users because it connects without a
broker. MQTT is useful when you already manage a central broker, including one
shared with Zigbee2MQTT. Zigbee2MQTT is a broker client, not a broker. WHAT uses its
own authenticated protocol; other APIs or services need a compatible client or
custom bridge. The current transport is not a generic sensor-export interface.

## Setup

1. Update both WHAT components. Keep the PC profile and existing Home Assistant entry to preserve entity IDs and history.
2. Configure Home Assistant's MQTT integration with the broker you want to use. WHAT requires Home Assistant 2026.9 or newer.
3. In WHAT, open **Settings > Home Assistant Installations > Add Installation**. Give it a name and choose **MQTT**.
4. Enter the same broker's hostname and port. TLS defaults on, with port 8883. Plaintext is allowed only for explicitly trusted Local brokers. Cloud requires TLS.
5. Enter the broker username and password if needed. Under Certificates, optionally supply a CA file or matching client certificate and private key. A blank CA uses Windows trust. Certificate and hostname verification stay enabled. Client keys must not require a passphrase. Passwords are protected by Windows DPAPI; leaving the password blank preserves it, and Clear Saved Password removes it.
6. Save, then click **Pairing** on the installation. In Home Assistant, add WHAT, choose **MQTT**, and enter the copied PC device ID, token, fingerprint and a PC name. The entry waits for an authenticated stream. Initial pairing does not need direct PC access.

Each installation has its own token, client, session, retry state and stream baseline. Multiple installations can use different brokers or share one broker. A stopped or failing installation does not stop the others. Start and Stop in WHAT control all installations; each row's enable switch controls just that installation.

Save both connection configurations in the same installation, then choose **Active Connection** in WHAT. The integration handles the switch without re-pairing or reloading. WHAT verifies the replacement before releasing the existing route. If verification fails within 30 seconds, it retains the previous connection and shows the reason. Other installations continue streaming. WHAT does not modify Home Assistant's broker credentials or install a broker.

## Sensor permissions and precision

The sensor table has Local, Cloud and Fast updates columns. No route ticks means no sensor sharing. The category row applies each tick to that entire category, including rows hidden by the current search. The Share menu offers bulk actions; All sensors / Shared / Unshared filters control visibility and filtered bulk actions.

- **Local** permits readings on the direct LAN connection and brokers you explicitly classify as Local.
- **Cloud** permits readings on the remote HA connection and brokers classified as Cloud.
- **Fast updates** selects the faster numeric publishing interval. Defaults: normal 1,000 ms; high 100 ms. Settings allow normal 100 to 60,000 ms and high 50 ms up to the normal interval. This controls publishing, not how frequently a hardware sensor samples. State changes such as locking, display power, volume events and availability remain immediate.

Selected readings without an existing route choice start Local-only, including when upgrading an older profile. Review Cloud choices before using a cloud-classified route. A sensor withheld on the active route becomes unavailable in HA; its entity identity/history remain. Module and command permissions remain separate controls on the Modules page. Disabling sensor sharing does not revoke permission to control the PC. Explicit sensor denials also remove copies of those readings from media-control attributes.

Percentages are rounded to at most **two decimal places before delta comparison and serialization**, on both transports. Precision is handled internally; there is no precision selector. Other numeric readings retain their existing source/precision policy. Trailing zeros are not added to wire values. HA also limits percentage entity states to two decimals for compatibility with older WHAT senders.

## Performance

Unchanged rounded values are suppressed. Normal numeric changes are coalesced to their selected rate; a scheduled flush sends the latest pending value when due. Heartbeats remain separate. Real event frames do not bypass the rate limit for unrelated numeric sensors. Each peer has its own filtered baseline, so switching routes cannot reuse values from a more permissive connection.

MQTT is an alternative connection path, not a performance shortcut. Both paths share the same rounding, batching and changed-reading layer. The authenticated MQTT JSON envelope base64-encodes the protocol body, adding roughly one third to the body size. A broker also adds another hop. HA still processes the entity state changes that you permit; restricting high-frequency publishing is what reduces that load.

## Authentication and topics

For PC ID `<device_id>`:

| Client | Publish | Subscribe |
| --- | --- | --- |
| WHAT | `what/<device_id>/announce`, `what/<device_id>/out/+` (one concrete session topic at a time) | `what/<device_id>/in` |
| HA | `what/<device_id>/in` | `what/<device_id>/announce`, `what/<device_id>/out/+` (one concrete session topic at a time) |

Configure broker ACLs for the corresponding publish/subscribe rights. Broker packet limits must accommodate the selected sensor snapshot (protocol body capped at 8 MiB; authenticated MQTT envelope capped at 12 MiB). Normal updates contain only changed readings.

Every MQTT envelope has an HMAC-SHA256 using that installation's pairing secret, direction, connection epoch, logical session and increasing sequence. The secret is not sent to the broker. Fresh epochs/sessions prevent old-session replay; sequence checks reject duplicates. Peers supporting `discovery_probe_v1` verify discovery with a fresh signed probe before switching routes, so clock drift does not prevent connection. Peers without it reject announcements more than 30 seconds old and need synchronized clocks. Retained inbound messages are rejected. Frames use QoS 0 with no retained state, offline persistence or automatic command retry. A lost delta causes a full reconnect/resync rather than applying it to the wrong baseline. Queues are bounded and slow consumers reconnect.

TLS protects each broker connection; the broker can read sensor payloads. HMAC authenticates WHAT messages but is not end-to-end encryption. Commands retain the existing WHAT permissions, rate limits and deadlines. If a connection drops while a command is running, its outcome can be unknown; WHAT does not replay it.

HA requires consecutive MQTT envelope sequences within each session. A gap or
full receive queue closes that session and requires a fresh authenticated
snapshot. During a planned handover, HA gives pending command replies up to
three seconds to finish on the old route. It keeps that route if the replacement
has already closed. Commands still awaiting a reply when their route closes
report an unknown outcome and are not retried.

## Status and recovery

The footer shows active Local/Cloud connections and negotiated TLS 1.2/1.3 for WebSockets. Paho's public API does not expose the MQTT connection's negotiated version, so MQTT reports `TLS encrypted (version unavailable)` or `unencrypted`. It never labels plaintext MQTT as encrypted. Broker setup/connection failures appear beside its settings and in Diagnostics.

If a connection cannot start, check the installation's status, Home Assistant's MQTT integration, broker credentials, ACLs and certificates. Update both WHAT components. Change **Active Connection** on the existing installation in WHAT. Keep the existing entry to preserve its devices and entities.

Each installation uses its selected transport. MQTT-only Home Assistant entries wait for their broker connection and do not probe a placeholder PC address. A broker outage does not stop other installations. API installations can use their own direct and outbound WebSocket routes; they do not borrow another installation's connection.

Implementation references: [HA optional dependencies and MQTT readiness](https://developers.home-assistant.io/docs/creating_integration_manifest/), [HA subscription acknowledgement](https://developers.home-assistant.io/blog/2025/11/23/mqtt-subscribe-wait/), [Paho C asynchronous API](https://eclipse-paho.github.io/paho.mqtt.c/MQTTAsync/html/).

## Discovery with unsynchronised clocks

Peers advertising `discovery_probe_v1` verify discovery without comparing system
clocks. Before switching routes, the integration sends a signed probe on a fresh
random session topic and requires a signed reply within 15 seconds. The reply
confirms the live PC's transport preference without changing its active session.
Peers without this capability require clocks within 30 seconds. Sequence gaps
continue to trigger a fresh session so missing deltas cannot corrupt state.
