# WHAT Protocol v2

Use compatible Windows app and integration builds. Protocol v1 is not supported.

Local WHAT API connections use TLS 1.2+ WebSockets (`wss://PC:48765/`). Home Assistant initiates connections and
pins the manually copied SHA-256 certificate fingerprint BEFORE sending the
random 256-bit token. Normal Windows profiles encrypt the token/private key with
DPAPI. No plaintext fallback is offered. Remote controls use the optional,
explicitly permitted commands_v1 extension described below.

Authenticate within five seconds:
`{"type":"auth","protocol":2,"token":"<64 hex characters>"}`.
The server sends `hello` (protocol, device_id, name, app_version), then a full snapshot. Changed readings are pushed promptly; the configured
1 to 300 second interval is a heartbeat. Maximum eight clients.

Snapshot fields:
- `type: snapshot`, `protocol: 2`, `device_id`: persistent PC UUID.
- `healthy`: HWiNFO freshness only; never global PC/Windows availability.
- `hwinfo_enabled`: user source setting; false is not a HWiNFO problem.
- `windows_healthy`: background Windows collector has completed recently.
- `status`: HWiNFO state or a disabled explanation.
- `sample_time`: snapshot Unix time; `sample_age`: HWiNFO age, or -1.
- `publish_seconds`, `license`: self-reported unknown/free/pro, `sensors`: list.

Each selected reading has `id`, `source` (windows/hwinfo), `group`, `name`, `unit`,
legacy integer `type`, `kind` (number/text/binary/timestamp), `available`, `value`,
and optional `state_class` (empty/measurement/total_increasing).
Numbers must be finite; binary values are JSON booleans; strings are at most 255
characters. Timestamps are ISO 8601 with a timezone. Unavailable values MUST be
null. Non-numeric readings have empty units/state classes. Additional UI metadata
such as `recommended` is permitted. Limits: 8 MiB/message and 10,000 readings.
The integration rejects duplicate JSON keys, non-finite numbers and nesting
beyond 32 container levels before applying a frame. Hello capability flags must
be booleans when present.

HA creates numeric/text/timestamp sensor entities and binary_sensor entities.
Per-reading availability is independent of HWiNFO health. An empty text value is
valid. Wrong types, invalid timestamps, NaN, duplicate IDs and identity changes
are rejected. Changing an established reading's kind/unit makes it unavailable
to avoid corrupting its history. Full snapshot payloads are complete replacements.
Unselected/missing readings become unavailable, never zero.

HWiNFO IDs remain three 8-digit hexadecimal components: sensor ID, instance and
reading ID. Windows IDs have win_ prefixes; volume and adapter identities use
hashes of Windows volume/interface GUIDs, never the drive letter. App diagnostics
use app_ prefixes. HA unique IDs additionally include the PC UUID. Volume IDs
survive unplugging, reinsertion and app restart; formatting a volume may give it
a new identity. Catalog metadata is retained locally without sampled values.

Reconnect receives a full snapshot. HA detects silent servers after
max(30, 3 × publish_seconds + 10) seconds and reconnects with exponential backoff
up to 30 seconds. An authenticated alternate route can interrupt a stalled
direct connection attempt. Command replies do not reset the telemetry deadline.
HWiNFO freshness uses its poll timestamp; unchanged
values are not stale. Windows freshness uses monotonic completion age, allowing
at least 30 seconds or three collection intervals.

mDNS `_what._tcp.local.` advertises id, version=2, tls=1. Discovery is only a
hint: it never conveys credentials or silently changes a paired address/pin.
Use manual pairing or reconfiguration where multicast/DHCP changes require it.

Service Stop and port changes close existing clients. Start/Restart keep identity and credentials. Reconfigure the paired HA port after changing it.


## Optional delta_v1 extension

Clients request delta streaming with `"delta_v1": true` in auth. The server confirms it in hello.
Without confirmation, the client accepts the original full-snapshot format;
without an auth request, the server sends the original format. Protocol stays 2.

For negotiated streams the first snapshot has `sequence: 1`. Every subsequent
telemetry frame increments sequence by one. A delta has `type: "delta"`, `sequence`,
`base_sequence` equal to the preceding sequence, `sensors` containing only changed
or newly selected rows, and `removed` containing IDs no longer shared. Other
envelope fields retain their snapshot meanings; `publish_seconds` is the heartbeat.
A heartbeat can be a delta with empty sensors/removed arrays.

HA validates each frame before applying it. Missing baselines, gaps, malformed
rows, invalid removals and an accumulated catalog above 10,000 readings cause a
disconnect and a fresh authenticated connection/full snapshot. Reconnect does not
erase entity identity/history. Each client has its own last-sent baseline, so
skipping telemetry while a slow socket drains cannot corrupt its next delta.

Telemetry uses a non-restarting 50 ms batching timer. Native events bypass that
timer and have a maximum of 32 snapshots queued per client. A client exceeding
that bound is disconnected for resynchronisation; this is a state stream, not a
durable event journal. The producer-to-network event queue is also bounded. No
delivery guarantees apply across disconnects, suspension or app shutdown.
There is at most one outstanding WebSocket frame per client. A blocked client
is disconnected after 30 seconds. Frame count/bytes/delta count are logged about
once per minute while clients are receiving data; no sensor values are logged.

## Optional commands_v1 extension

Auth includes `"commands_v1": true`; hello must confirm it before commands are
sent. Older peers continue to exchange telemetry without controls. Commands
travel over the same pinned, authenticated TLS connection; no additional port
or credentials are needed.

Request example:
`{"type":"command","id":"unique-request-id","action":"volume","args":{"value":0.5}}`.
Reply: `{"type":"command_result","id":"unique-request-id","ok":true,"message":"Completed"}`.
Replies are separate from telemetry and do not advance its sequence number.

Snapshot/delta envelopes include `controls` with version 1, five boolean
`permissions` (media, power, power_plan, urls, monitor), media/audio `values`,
and `power` (installed plans, active plan GUID, supported AC/DC modes).
Media values require media permission; titles still obey Windows privacy
settings. Power data requires power_plan permission. Disabling the Windows
module makes all permissions false.

Actions: volume (0..1), volume_up/down (5 percentage points), mute (boolean),
play/pause/stop/next/previous, seek (seconds), shuffle (boolean), repeat
(off/one/all), sleep/hibernate/reboot/shutdown/lock, power_plan (installed GUID),
power_mode_ac/dc (Best power efficiency/Balanced/Best performance), open_url
(HTTP/HTTPS without embedded credentials), monitor_sleep. Parameterized actions
take only args.value; other actions require an empty args object.

Commands are limited to 32 KiB, 30 per ten seconds and 4,096 unique IDs per
connection. Duplicate IDs are rejected. Only one Windows command executes at a
time; busy requests are rejected rather than queued. HA allows at most eight
pending requests and waits up to 12 seconds. Timeout/disconnection does not imply
that an action failed: commands must never be automatically replayed.

Sleep/hibernate/reboot/shutdown replies report acceptance before execution,
because the PC may disconnect. Later failures appear in local diagnostics.
Session invalidation, permission changes and expired dispatch deadlines cancel
commands that have not begun. A command already executing cannot be undone by
disconnecting. The open_url action accepts only HTTP/HTTPS. Arbitrary shell
execution requires the separate extension below.

## Optional iCUE data and controls

An icue envelope contains enabled, connected, control_enabled, status and devices.
At most 64 devices each contain a bounded SDK ID/model, type, channel count, up
to 512 LEDs (numeric ID and RGB triple or null), and up to seven scalar device
properties (ID, name, value and writable flag). Selected readings may use source
icue. Update both peers before enabling this new source.

Full snapshots replace iCUE state; delta frames may omit unchanged icue data.
Missing icue in a delta retains the previous validated value. Disabling the
module sends an explicit disabled envelope with an empty device array.

Commands use the existing command/result messages and require the independent
iCUE control permission: icue_colors takes device, rgb and optional leds (omit
for all LEDs); icue_release takes device; icue_property takes device, property
and value. RGB channels and LED IDs are strictly validated, and device/LED
existence and property writability are checked again at execution. These commands
run on the SDK worker independently of Windows shell/media tasks, with no retries.

## Shell commands

`controls.shell_enabled` is an optional boolean outside the original five-key
permissions object, preserving compatibility with older integrations. It defaults
to false. `run_command` requires it and takes args.shell (powershell/pwsh/cmd/wsl),
args.command (1 to 2048 characters), args.timeout (integer seconds 1 to 300), and an
optional args.distribution for WSL. Windows shells also accept the optional
args.leave_running boolean, defaulting to false. It is not supported for WSL.
Arguments are validated on both peers.

A successful transport/execution request returns command_result with a result
object: stdout/stderr (each at most 65,536 characters after bounded UTF-8 decoding),
exit_code (unsigned Windows exit code), timed_out/cancelled/truncated (booleans).
Nonzero exit status and timeouts remain results with ok=true; startup/permission
failures use ok=false. HA waits timeout+15 seconds for a reply and never replays.
The existing telemetry stream continues independently while the command worker
is busy. By default, the Windows process tree is terminated when the command
ends. With leave_running enabled, child processes are released only after a
successful shell exit within the timeout. Failure, cancellation and timeout
still terminate them. Released processes cannot be cancelled through WHAT.
WSL uses Linux timeout with a two-second kill grace period; detached Linux
processes may survive it.

## Optional iCUE Automation metadata

The optional `icue_automation` snapshot object contains `enabled`, `connected`,
`control_enabled` booleans, `status` string (max 2000), and `controls` (max 193).
Each control has a stable `id` (`profile` or `cooling_`/`eq_`/`dpi_` plus 24 hex
characters), `name`, `device`, `confirmed`, `current` (option ID or null), and
`options` (1..256 objects with unique `id` and `name`, each max 256 characters).
Only confirmed current states may be non-null. Currently only the profile has
confirmed feedback. Optional `version` identifies the local iCUE server.
Unchanged metadata may be omitted from negotiated deltas; receivers retain it
until replaced or a fresh snapshot resets it.

Command `icue_automation` accepts exactly `{control: id, option: id}`. Windows
checks module/remote permission, active client lease and freshness, then
revalidates the option with iCUE before dispatch. There is no raw-method escape
hatch. Profile commands release this app's SDK lighting layer before switching;
other applications' layers are untouched. No write is automatically retried.

### Optional Lighting Link metadata and command

`icue_automation.lighting` contains `available` (bool), `effect` (supported type ID
or null), `parameters` (current normalized values only), and `effects` (up to ten
items with `id`, `name`, and a bounded `parameters` array of supported keys).
Speeds are integers 0..2, directions 0..3, rotations 0..1, RandomColor is boolean,
and Color1/Color2 are #RRGGBB strings. Omitted parameters are not known values.
The existing automation metadata delta baseline preserves this optional section.

`icue_lighting` arguments are one of:
- `{ "stop": true }`: stop the global override.
- `{ "effect": "RainbowWave", "parameters": { "Speed": 1, "Direction": 0 } }`
- `{ "expected_effect": "ColorWave", "parameters": { "Color1": "#204060" } }`

Parameters are optional on effect selection; the PC initializes a new effect with
explicit defaults, or preserves the current parameters when selecting the same
effect. Partial edits require `expected_effect`, checked again against fresh iCUE
state. Unknown keys, unsupported effects/parameters and out-of-range values fail.
The PC converts colours to signed packed ARGB and wraps activation in `effect`.
It releases its SDK colour overrides, verifies get-active readback and does not
retry writes. Both module enable and Automation HA permission are required.

## Keyboard/mouse commands

Optional controls.input_enabled is a boolean (absent means unsupported/disabled). The existing permissions object retains its original keys. Command action keyboard accepts exactly one of keys (1-6 supported unique key names) or text (1-1024 UTF-16 units). Command action mouse accepts integer physical desktop x/y, optional button (none/left/right/middle), and optional clicks (1/2, only with a button). Both require the independent input permission. Standard command replies and session cancellation apply; never retry automatically.

### Display editor extension

`displays.layout_editor: true` advertises whole-arrangement editing. Monitors optionally include `modes` (up to 256 `{width,height,refresh}` choices in unrotated orientation). The `display` command accepts `{operation:"apply", layout:[...], expected:[...]}`. Both arrays contain 1 to 32 active rows with `id`, `group`, integer `x/y/width/height/rotation` and optional numeric `refresh`. Rotation uses CCD values 1 to 4. `expected` must match the current Windows arrangement. The requested layout must contain one desktop group at 0,0; independent groups cannot overlap. Missing monitors, invalid geometry, changed base layouts and revoked permissions are rejected. Existing recovery rules and command leases remain in force.

## Multiple Installations

The Windows configuration assigns each installation a stable local ID, name,
transport, enable switch and separate pairing token. Pairing credentials are
never included in telemetry. The PC device ID remains unchanged. Protocol version
stays 2.

Incoming TLS authentication selects the installation by token. Outbound API and
MQTT sessions belong to that installation. Each owns retry timers, command leases,
sequence numbers and delivery baselines on the existing network thread. Changing
identity or permissions, or deleting an installation, revokes its queued command
leases before the worker processes the change. Renaming and editing connection
settings keep the existing route alive during handover. Global policy revocations
still invalidate every affected stream.

`connection_options` describes only the receiving installation. Cleanup requests
are sent only to the chosen installation; its authenticated session must return
the preview and removal replies. UI entity lookups retain their installation name.

Remote-only onboarding stores explicitly supplied pairing credentials and enables
the selected receiver. It creates no trusted readings until a full authenticated
hello and validated snapshot arrive. MQTT HMAC checks use that installation's token,
including when several installations share a broker and PC topic namespace.

Snapshots and deltas can include a `connections` array describing all named
installations. The network worker attaches the current report to every outgoing
reading or event frame. Each row contains only:

| Field | Meaning |
| --- | --- |
| `id` | Stable installation ID, 32 lowercase hexadecimal characters |
| `name` | User-supplied name, 1 to 80 characters |
| `type` | `api` or `mqtt` |
| `status` | `connected`, `disabled`, `stopped`, `waiting`, `connecting`, `retrying` or `error` |
| `scope` | `none`, `local`, `cloud` or `hybrid` |
| `connected_since` | UTC Unix milliseconds, or 0 while disconnected |
| `last_reply` | UTC Unix milliseconds, or 0 before the first confirmed reply |

The array has unique IDs and uses the existing 10,000-entry protocol safety limit.
Unknown row fields, invalid values and duplicate IDs reject the frame. Older peers
can omit the array. There are no addresses, usernames, credentials or raw error
messages in this report.

The integration creates one diagnostic status sensor per installation under the
PC device. Renaming keeps its entity ID. Removed installations become unavailable;
cleanup leaves diagnostic registrations and history intact. All reported states
become unavailable when the receiving Home Assistant loses contact with WHAT.

### Coordinated Transport Changes

Peers negotiate `transport_switch_v1` during authentication. Each installation
keeps its API and MQTT configurations with one selected transport. Its frames
include `connection_config`: a fresh 32-character hex `revision`, `preferred`
(`api` or `mqtt`), boolean `remote_enabled` and `mqtt_enabled`, and the PC's
`direct_host`, `direct_port` and SHA-256 `fingerprint`. This profile describes only
the receiving installation. It contains no token or broker credentials.

The integration applies this PC-authenticated configuration without reloading its
entry. A manually changed pairing or credential still triggers the normal reload.
For an initial MQTT-only pairing, the profile supplies the direct endpoint and
certificate pin; an existing manually configured direct address stays unchanged.

A replacement receives one snapshot for validation and cannot issue commands or
receive the ongoing stream yet. After draining outstanding command replies, Home
Assistant sends `transport_activate` with a fresh 32-character hex `id` and the
profile `revision`. WHAT checks that the candidate matches the current selection,
revokes the old peers and their command leases, and replies with a `command_result`.
Home Assistant then adopts the candidate. Commands are never replayed.

Failed validation or a changed revision leaves the current stream active. After
30 seconds without activation, WHAT restores its previous transport selection and
reports the failure. Disabling or deleting the installation cancels the handover.
Older peers can keep their existing connection; automatic handover requires both
updated components.
