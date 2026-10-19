<p align="center">
  <img src="assets/what-logo.gif" width="140" alt="Animated WHAT logo">
</p>
<h1 align="center">WHAT</h1>
<p align="center"><strong>Windows Home Assistant Toolkit</strong></p>

<p align="center">
  <a href="#install-and-pair">
    <img src="https://img.shields.io/badge/HACS-Custom-orange.svg" alt="HACS Custom">
  </a>
  <a href="https://github.com/Chuffnugget/what-integration/releases">
    <img src="https://img.shields.io/github/downloads/Chuffnugget/what-integration/total?label=release%20downloads" alt="Release downloads">
  </a>
  <a href="https://github.com/Chuffnugget/what-integration/actions/workflows/validate.yml">
    <img src="https://github.com/Chuffnugget/what-integration/actions/workflows/validate.yml/badge.svg" alt="Integration validation, including HACS and hassfest">
  </a>
  <a href="https://github.com/Chuffnugget/what-integration/releases">
    <img src="https://img.shields.io/github/v/release/Chuffnugget/what-integration" alt="Latest release">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-LGPL--3.0--or--later-blue.svg" alt="Licence: LGPL-3.0-or-later">
  </a>
</p>
<p align="center">
  <a href="https://www.paypal.me/chuffnugget">
    <img src="https://img.shields.io/badge/PayPal.Me-chuffnugget-00457C?logo=paypal" alt="PayPal.Me">
  </a>
  <a href="https://coff.ee/chuffnugget">
    <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="20">
  </a>
</p>

This repository contains the WHAT Home Assistant integration. Install the
[Windows application](https://github.com/Chuffnugget/what-windows#readme) on each PC
you want to connect. The integration requires Home Assistant 2026.9+.

Each PC has a parent WHAT device, with module devices beneath it. Entity IDs
remain stable when switching between WHAT API and MQTT connections.

## Install and pair

In HACS, open **Custom repositories**, add
`https://github.com/Chuffnugget/what-integration` with category **Integration**,
then download WHAT and restart Home Assistant.

For manual installation, copy `custom_components/what` into
`/config/custom_components/what` and restart Home Assistant.

1. In WHAT, open **Settings > Home Assistant Installations**, configure an
   installation, then open its **Pairing** details.
2. In Home Assistant, open **Settings > Devices & services**. Configure the discovered
   PC, or choose **Add integration > WHAT**. Select **WHAT API (Recommended)** or
   **MQTT** to match the active connection in WHAT.
3. Copy the PC device ID, name, token and certificate fingerprint. WHAT API supports
   local and remote access: enter the PC address for local access, or leave it blank
   when WHAT connects to Home Assistant's HTTPS address. Discovery fills in the local
   address but still requires pairing. MQTT needs no PC address or port.
4. Enable modules and select readings in WHAT's sensor browser. Enable any required
   Home Assistant control permissions on the PC.

Discovery uses local multicast. If it is blocked, use manual pairing. Keep the
private certificate key on the PC.

## Multiple Home Assistant Installations

Use **WHAT > Settings > Home Assistant Installations** to add, name, edit,
enable, disable or delete API and MQTT connections. Each has separate pairing
details and connects independently. Both API and MQTT settings can be saved in
the same installation. Choose **Active Connection** in WHAT to switch; the
integration verifies the new route without re-pairing or reloading. Failed checks
retain the previous connection. Use one entry per Home Assistant server. Select the matching installation before
copying its token. Remote API and MQTT pairing can be completed without direct
PC access. See [remote API setup](REMOTE.md) and
[MQTT setup](MQTT.md).

## Sensors and controls

WHAT supports Windows readings and controls, HWiNFO, Core Temp, NVIDIA NVML,
BEACN, iCUE RGB, iCUE Automation and Wallpaper Engine. Availability depends on
hardware, source applications and the permissions enabled in WHAT.

Controls include media and volume, power plans, display layouts, notifications,
lighting and optional keyboard, mouse or shell actions. Individual LED entities
are disabled by default. Disconnected or unsupported features become unavailable.

Create custom readings on WHAT's **Sensors** page and saved commands on its
**Commands** page. Their IDs survive renaming. HA receives selected readings;
scripts and definitions stay on the PC. Saved commands start local-only. Allow
HA execution both globally and for each command you want exposed as an HA button.
Cloud execution also needs permission for that command and the Custom module.
HA-supplied shell commands use a separate, broader permission.

`what.send_notification` supports images, action buttons and replies with the
installed app. Button replies fire `what_notification_action`, including the
PC device ID, notification ID, action and reply. Reusing a notification ID
replaces its previous notification. Old action frames are not replayed when HA
reloads or changes transport.

Enable Speech and Audio in the Windows module to use `what.speak` with installed
Windows voices, or send HA TTS and media-source audio to the separate
**Announcements** media player. Audio URLs must be reachable from the PC;
reverse WebSocket connections use HA's external HTTPS URL for HA-hosted audio.
Update both the app and integration to use these features.

Core Temp supplies CPU readings. HWiNFO supports a broader range of motherboard,
memory, GPU and drive sensors. WHAT reads their shared memory without changing
their hardware polling rate. Temperatures are normalised to Celsius; distance to
TjMax has its own reading.

See [Entities and actions](ENTITIES.md) for automation examples and control limits.

## Connections

Local authenticated TLS WebSockets are the default. Optional remote access uses
your HA HTTPS endpoint, including Nabu Casa. Configure both connection types in
WHAT. HA applies those choices over the paired local connection; there is no
separate connection menu in the integration. MQTT still requires HA's MQTT
integration and uses the same WHAT entities.

Keep the existing WHAT entry when changing transport. This preserves the PC's
entity IDs, automations and history. See [Remote setup](REMOTE.md) and
[MQTT setup](MQTT.md).

## Monitor manager

The display layout card loads automatically. Add a manual dashboard card using
the PC's Display arrangement entity:

```yaml
type: custom:what-display-layout-card
entity: sensor.YOUR_PC_display_arrangement
title: Monitor manager
```

Drag monitors to arrange them, choose modes and rotation, or enable mirroring.
**Apply layout** sends the staged changes to Windows. See the
[card guide](DISPLAY-CARD.md) and [display controls](DISPLAYS.md).

## Updates and troubleshooting

Update through HACS when available, or replace `custom_components/what`, then
restart HA. Keep existing WHAT entries. Refresh the browser after display-card
updates, and update both components when a feature requires a newer protocol.

For unavailable entities, check that WHAT is running, the connection is
authenticated, and the source module and permissions are enabled. WHAT's
Diagnostics page shows connection errors and can prepare a report for review.
Reports are not uploaded automatically.

The parent PC device shows connection uptime, transport, remote-access permission,
endpoint scope and published/unavailable counts. Uptime resets after a disconnect
or reload. Remote-access permission does not establish Internet reachability.

## Connection recovery

WHAT creates a notice in **Settings > System > Repairs** if the paired PC presents
an unexpected certificate or identity, uses an incompatible protocol, or sends
invalid data. Ordinary offline PCs continue reconnecting without a repair notice.
A working MQTT or reverse connection can remain available while the direct route
needs attention. Each notice clears when its affected route recovers.

For a changed certificate, check the intended PC in person and copy its current
fingerprint from WHAT. Choose **Reconfigure** on the existing WHAT entry in HA.
Do not delete the entry or accept a certificate from an unfamiliar PC. A rejected
token starts HA's reauthentication flow. Both flows retain device IDs, entity IDs
and custom names. Protocol mismatches require compatible app and integration
versions. Replacing a PC with a different identity requires a separate pairing.

Discovery can update a paired PC's address after a DHCP change, but only after
verifying its existing certificate, token and PC identity. An unverified
advertisement cannot replace the stored address or credentials.

## Download diagnostics

Open WHAT under **Settings > Devices & services**, open the entry's menu and
choose **Download diagnostics**. The report includes connection modes, recent
failure categories, module availability and counts, permissions, app statistics,
and reported version numbers. Missing versions appear as `null`; the integration
does not guess versions that the Windows app has not supplied.

Reports exclude addresses, tokens, fingerprints, sensor values, command contents,
notification text and file paths. They remain available if setup fails. To capture
a connection failure in more detail, use the debug logging steps below.

## Remove WHAT

In **Settings > Devices & services > WHAT**, delete each PC entry you no longer
need. This unloads its connections and removes its entity registrations and
customizations. It also clears that PC's repair notices. Recorder history follows
HA's normal retention policy.

After removing every WHAT entry, remove the integration through HACS, or delete
`/config/custom_components/what` for a manual installation, then restart HA.
Windows app settings are separate and are kept. To keep a PC paired and remove
only unshared entities, use the cleanup controls instead.

## Entity cleanup

Open the parent PC device, for example **WHAT · DESKTOP-PC**, and press
**Preview Entity Cleanup**. Open its **Entity Cleanup** diagnostic to review the
count and first 20 candidates, then press **Remove Previewed Entities**. A preview
with no candidates says **Nothing to remove**. Removal is unavailable until a
preview finds candidates, and the preview expires after two minutes. You can
also use **WHAT Settings > Home Assistant Entities** while connected locally.

Cleanup preserves selected readings even when unavailable, supported controls,
diagnostics and other PCs. Only the reviewed entities can be removed; selection
is checked again before removal. Removed entities lose their registrations and
custom settings, but recorder history is not erased. See
[Modules and cleanup](MODULES-AND-CLEANUP.md).

## Validation and releases

The fast tests run with `python -m pytest -q tests --ignore=tests/ha_runtime`.
For real entity registration, reconnect metadata, reload and unload checks, use
a separate Linux environment with Python 3.14, `homeassistant==2026.9.0`,
`pytest` and `pytest-asyncio`. Run `python scripts/install_ha_test_dependencies.py`
to install the matching Home Assistant frontend, then run
`python -m pytest -q tests/ha_runtime`.
The tests use a temporary HA configuration and simulated PCs. They cover setup,
reauthentication, discovery changes, recovery notices, diagnostics and lifecycle.
CI runs them against both the minimum supported HA and the latest stable release.

Validation runs on pushes to `main`, pull requests targeting `main`, and manual
dispatch. It covers Python tests, the display card, Home Assistant's Hassfest and
HACS repository validation. The release workflow runs the same checks before
publishing a version tag.

Tag the version in `custom_components/what/manifest.json`. A validated tag run
creates a release and attaches `what.zip`, which contains the integration files
HACS needs. Manual branch runs validate without publishing. HACS download counts
come from the selected GitHub release asset and are not unique-user counts.

Before tagging both repositories, run Windows Build-All with both checkouts
present. It compares the bundled integration contract and tests the new Windows
executable against it. Windows CI uses the committed contract copy; no
cross-repository token is required.

## Debug Logging

For connection troubleshooting, open WHAT's integration menu in Home Assistant
and choose **Enable debug logging**. Reproduce the problem, then disable debug
logging to download the log. WHAT logs connection attempts, transport changes,
protocol failures and command timing. It does not log pairing tokens, command
arguments, command output or sensor values. A healthy-stream summary appears at
most once a minute; malformed MQTT frame reports are limited to once every
30 seconds per session. The WHAT device's Diagnostic Logging switch controls the
Windows app's log separately.

## Documentation

- [BEACN](BEACN.md), [Wallpaper Engine](WALLPAPER_ENGINE.md)
- [Notifications](NOTIFICATIONS.md), [Keyboard and mouse](INPUT.md)
- [Protocol](PROTOCOL.md), [Release history](https://github.com/Chuffnugget/what-integration/releases)
- [Windows app](https://github.com/Chuffnugget/what-windows#readme)

## Licence

The integration is [LGPL-3.0-or-later](LICENSE), as declared in [COPYRIGHT](COPYRIGHT).
[COPYING.GPL-3.0](COPYING.GPL-3.0) supplies the GPL terms incorporated by LGPL v3.
Third-party assets retain their own terms.

WHAT is an independent project. It is not affiliated with or endorsed by Microsoft,
NVIDIA, HWiNFO/REALiX, Corsair, BEACN, the Wallpaper Engine developers or Home
Assistant. Product names and logos belong to their respective owners.

## WHAT App Controls in Home Assistant

The parent PC device, such as **WHAT · DESKTOP-PC**, includes:

- **Connection - Name**: one status diagnostic for every installation configured in WHAT, including other Home Assistant servers. Attributes show its transport, sharing scope, connection start and last reply. Addresses and credentials are not shared. These reported states become unavailable if this Home Assistant loses contact with WHAT. Renaming preserves entity IDs; deleting an installation leaves its diagnostic unavailable and keeps its history.
- **Diagnostic Logging**: changes WHAT's activity logging. Memory dumps remain a separate local setting.
- **Publishing Interval**: the normal sensor publishing interval in milliseconds, from 100 to 60000. It cannot be shorter than the high-priority interval. This does not change collection rates or the connection heartbeat.
- **WHAT Update**: installed version, latest checked release, update channel, last check time and a GitHub release link. It follows the app's update checks. Install updates from WHAT on the PC.
- **App Uptime** and **Last Seen**: WHAT's running time and the time HA last received data. Last Seen remains visible after disconnection.
- **WHAT CPU Usage** and **WHAT Memory Usage**: sampled every five seconds for WHAT.exe. CPU uses a 0-100% scale across all logical processors; memory is the working set in MiB. Helper processes are excluded.
- **Restart WHAT**: restarts the app, keeping its profile and settings. It does not restart Windows. Restart is unavailable during an update operation.

To allow setting changes and restarts, enable **Allow Home Assistant to Manage WHAT**
in the Windows app under **Settings > Pairing and Security**. It is off by default
and applies to authenticated local, remote and MQTT connections. Diagnostics do not
need this permission. Module permissions and transport setup remain in the app.

Update both WHAT and the integration to use these entities. App Uptime retains
its existing unique ID when moved from the Windows module to the parent PC.

## Leaving launched applications running

Saved commands have a **Leave Launched Apps Running** option in WHAT's command
editor. The `what.run_command` action accepts `leave_running: true` for inline
PowerShell, PowerShell 7 and CMD commands. It is off by default and is not supported
for WSL or custom sensors. Set the option in WHAT for named commands; Home Assistant
cannot override their saved definitions.

The shell must exit successfully within its timeout. Only then may its child
applications remain running. Failure, cancellation, permission revocation before
completion and timeout still terminate the command and its children. After a
successful release, WHAT stops collecting output and cannot cancel those apps.

For example, `Start-Process notepad` with `leave_running: true` keeps the launched
application open after PowerShell exits. A command that never finishes still times out.
