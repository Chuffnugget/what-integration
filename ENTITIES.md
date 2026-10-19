# Entity and action reference

## Entities

### Corsair iCUE

Enable the Corsair iCUE module on the PC, allow WHAT in iCUE's SDK/software
integration settings, then enable the module's Home Assistant control permission.

- One RGB light per Corsair device, plus individual LED lights disabled by
  default. Enable the LED entities you need from that device's disabled entities
  list in HA. Their SDK LED IDs are stable identifiers, not necessarily key names.
- A **Return lighting to iCUE** button clears WHAT's lighting layer. Turning
  a light off instead sets it black. Whole-device colour is the average of
  readable LEDs; changing it sets all LEDs to the requested colour.
- Battery/device/layout/headset readings appear where supported. Writable
  microphone, surround and sidetone settings become switches; writable equalizer
  presets become a numbered selector. Fan curves, DPI and saved-profile switching
  are not exposed by Corsair's public SDK.

Devices reconnect dynamically and become unavailable when removed or when the
module is disabled. Windows/HWiNFO collection remains independent. RGB state is
refreshed every two seconds, and unchanged iCUE metadata is omitted from deltas.
HA scenes can save combinations of these light entities without modifying saved
iCUE profiles. SDK conflicts and permission errors appear in PC Diagnostics.

### Remote Windows controls

In the Windows app, open **Modules → Windows → Remote control** and enable the
groups you want. All default to off and save immediately. The paired Home
Assistant device exposes these controls:

- **Windows media**: output volume/mute, play/pause/stop, previous/next, seek,
  shuffle and repeat. Playback actions depend on the current Windows media
  session and what its app supports. Track titles retain their separate privacy setting.
- **Power buttons**: sleep, hibernate, reboot, shutdown and lock.
- **Power selectors**: installed power plans, plus plugged-in and battery power
  modes when the Windows API supports them.
- **Open web URL**: set this text entity to an HTTP/HTTPS URL to launch it on
  the PC. For an automation, use `text.set_value` with the entity as target and
  `value: "https://example.com"`. The entity does not retain URL contents.
- **Monitor sleep**: requests display sleep; input can wake it again.

Unavailable control entities mean the PC is disconnected, the corresponding
permission is off, or the Windows feature is unsupported. Turning off the
Windows module blocks all commands. Media control permission also shares its
playback/volume state independently of individual sensor-selection checkboxes.

Sleep/reboot/shutdown/hibernate acknowledge acceptance before the PC disconnects;
acceptance is not confirmation that Windows completed the operation. Local
Diagnostics records failures. Commands are never automatically retried after
timeouts or disconnects. Wake-on-LAN is not included. Shell commands have a
separate permission, described below.

### Shell commands

Enable **Commands > Home Assistant Permissions > Allow Home Assistant to Send
Shell Commands** on the PC. This is also available under **Modules > Windows**.
It allows HA to supply command text, unlike saved commands defined locally in
WHAT. Only enable it if you trust HA with access to your Windows account.
Then use
**WHAT: Run Windows command** from HA's action editor. Select exactly one PC.
Windows PowerShell, PowerShell 7 (`pwsh`, if installed), CMD and WSL are supported.
User-initiated calls require HA administrator access; internal automations work
without a user context. Commands inherit the Windows app's account and elevation.

```yaml
action: what.run_command
target:
  device_id: YOUR_PC_DEVICE_ID
data:
  shell: powershell
  command: Get-Service Spooler | Select-Object Name, Status | ConvertTo-Json
  timeout: 30
response_variable: command_result
```

`command_result` contains `stdout`, `stderr`, `exit_code`, `timed_out`, `cancelled`
and `truncated`. Nonzero exit codes are returned for the automation to handle.
The response variable is optional. CMD accepts multiline batch commands. For WSL:

```yaml
action: what.run_command
target:
  device_id: YOUR_PC_DEVICE_ID
data:
  shell: wsl
  distribution: Ubuntu
  command: uname -a
  timeout: 30
response_variable: linux_result
```

Omit distribution to use WSL's default. WSL requires its Linux `timeout` utility.
Commands are non-interactive, limited to 2,048 characters and 1 to 300 seconds.
Output is UTF-8 with up to 64 KiB retained per stream. By default, Windows child
processes stop when the command ends. For Windows shells, `leave_running: true`
keeps launched applications open only after the shell exits successfully within
its timeout. Failure, cancellation or timeout still stops the command and its
children. WHAT cannot cancel applications after releasing them.

For example, launch an application without making the shell wait for it:

```yaml
action: what.run_command
target:
  device_id: YOUR_PC_DEVICE_ID
data:
  shell: powershell
  command: Start-Process notepad
  leave_running: true
```

In CMD, use `start "" "app.exe"`. Running an application directly can keep the
shell waiting and cause a timeout. `leave_running` is not supported for WSL or
custom sensors. WSL receives a guest-side deadline, but detached Linux processes
and externally launched services may survive it. Commands do not elevate or retry
automatically. Home Assistant configuration and traces can retain command text
and output.

### Saved commands

Create named commands on WHAT's **Commands** page. Allow Home Assistant execution
globally and for each command to expose it as a button. Cloud execution also needs
the command's Cloud permission and the Custom module's cloud permission.

Set **Leave Launched Apps Running** in the saved command's editor when needed.
Home Assistant cannot replace its command text or override its saved options.

### Telemetry

Selected numbers/text/timestamps become sensor entities. On/off values become
binary_sensor entities. Windows names explicitly include Windows, separate from
HWiNFO data on the same PC device. Timestamps use HA's timestamp class; only
numeric readings receive measurement statistics and units. Recognised units use
temperature, power, voltage, current, frequency, data size/rate and duration classes.

Windows readings remain available if HWiNFO closes, becomes stale or is disabled.
Diagnostic entities distinguish PC connection, HWiNFO enabled/source problem,
Windows collection problem, HWiNFO status and self-reported edition. Disabled
HWiNFO is not reported as a source problem. Zero is never substituted for failure.

New selected readings appear dynamically. Volumes use GUID-based IDs, so USB
removal/reinsertion and drive-letter changes retain entity identities/history.
The Windows app can auto-share newly detected drives' connected and used-% rows.
Unchecked or missing readings become unavailable. After a HA reload, unselected
entities are no longer instantiated; registry records remain for manual cleanup.
Existing entity names may stay as named in HA's registry after hardware relabeling.
Changing a reading's type/unit makes that entity unavailable to protect history.

Configure polling, source/privacy switches and selection in the Windows GUI.
Collection, publishing and heartbeat intervals are separate. HWiNFO checks
shared memory every 100 ms without changing HWiNFO's own sampling rate. Windows
events and slower sources have their own schedules. See the Windows app user
guide for the available readings and update settings.
Notifications require its optional installed package and Windows permission.
Window titles, notification contents, event messages and track metadata default
off. HA recorder may retain sensitive data already shared; disabling collection
does not erase that history. Use a modest selected set to limit recorder growth.

## Connection / troubleshooting

Reconnects automatically after sleep, app restart or network loss. Use HA's
Reconfigure for changed IP, port or fingerprint; a rejected token prompts reauth.
Certificate pinning happens before the token is sent. Discovery never silently
replaces a trusted address/pin. Manual IP/hostname pairing works without multicast.

- Cannot connect: check app, matching versions, port, routing and Windows Private
  network firewall allowance. Quit the previous Windows app before launching the new one.
- Only HWiNFO unavailable: check its Sensors and Shared Memory Support. Other
  Windows readings should continue. Setup steps are in the Windows app help.
- Optional Windows reading unavailable: enable that source under Modules > Windows and inspect
  its collection status. Missing devices, denied access and unsupported APIs stay
  unavailable. Notification access is a separate Windows permission.
- Fingerprint changed: verify the intended PC, then copy its fingerprint again.
  Creating a new Windows app profile creates a new identity/certificate.

WHAT is free and open source; its developer receives no money from HWiNFO
licences. HWiNFO's free shared-memory limit is its own restriction. Edition is
self-reported; WHAT cannot reliably identify Free/Pro through shared memory.

## Streaming updates

The stream supports negotiated deltas and full snapshots. No MQTT broker is required.

Changed readings arrive promptly over the existing TLS WebSocket. Sequence gaps
or invalid deltas reconnect for a full snapshot. The coordinator reconstructs the
catalog and writes only changed hardware entities; errors and recovery update
availability across all entities. Storage, slow Windows sources and HWiNFO have
separate collection schedules; HWiNFO's own sampling interval still applies.
Native lock/display/output-volume events bypass the telemetry batching window.


Disabling HWiNFO or Windows on the Modules page removes that module's readings from the stream; HA entities become unavailable without losing
history. Re-enable the module to resume its saved WHAT selection.

## iCUE profiles and presets

Enable **Modules > Corsair iCUE > Profiles & device controls**, then its **Allow
Home Assistant** checkbox. Approve **SDK: New Client Access Confirmation** inside
iCUE when requested. The integration discovers the available select entities:

- **iCUE profile** selects a complete existing profile and reports its active name.
  WHAT's direct RGB overlays are cleared first so the profile's effects appear.
  Other profile settings, including cooling, can change too.
- **iCUE [device]: Apply cooling preset**, **Apply EQ preset** and **Apply DPI preset**
  appear only where iCUE reports devices with presets. They apply existing presets;
  their current state remains unknown because this API provides no verified readback.

No entity IDs are hard-coded: use HA's entity picker in dashboards and automations.
A standard **Select: Select option** action selects an option by its displayed name.
Duplicate option names are disambiguated by ID. Disabling a Windows submodule or its
HA permission makes these entities unavailable; re-enabling does not duplicate them.
The Automation API is experimental and separate from Corsair's public RGB SDK.

### iCUE Lighting Link controls

Enable Corsair iCUE > Profiles & device controls and its Home Assistant permission
on the PC. Approve WHAT in iCUE's SDK client access prompt.

- **iCUE Lighting Link**: select a native effect; set its first colour/brightness
  where supported. iCUE generates all animation frames.
- **iCUE Lighting Link second colour**: colour/brightness for Color2 on effects
  that expose it. It is unavailable for effects with one/no colour slots.
- Speed, direction, rotation and random-colour selectors appear separately and are
  available only when the current effect supports that parameter.
- **Stop iCUE Lighting Link override**, or turning either light off, stops the
  same global effect and returns control to profile lighting. Off does not mean
  turning every LED black. This releases WHAT direct RGB overrides too.

Both colour entities refer to ONE global effect, not separate devices. Numeric
speed/direction/rotation codes reflect the verified API; no unverified names or
physical units are assigned. Brightness adjusts colour intensity only; it is not
available for a rainbow effect with no colour slot. Selecting RGB on the main light
without an explicit effect changes a colourless effect to Static colour.

Readback is confirmed, not optimistic. Missing parameters remain unknown; unsupported
controls are unavailable. Settings changes that race an external effect change fail
with a refresh message. An ambiguous write is not automatically retried.

## Windows display control

Optional per-monitor extend/mirror/disconnect and primary-display controls, plus named whole-PC layouts. Enable **Modules → Windows → Displays** in the Windows app and its separate HA permission. Changes apply without a timed confirmation. See [the display setup and automation guide](DISPLAYS.md).

## Send notifications to Windows

Use `what.send_notification` with a PC device, title and message. Enable **Modules → Windows → Notifications from HA** first. [Setup and examples](NOTIFICATIONS.md).

Keyboard and mouse actions: [instructions](INPUT.md).

## NVIDIA

Enable Modules > NVIDIA in WHAT to share driver-native GPU temperature, utilisation, memory, power and other supported readings. NVIDIA is independent of HWiNFO; existing module selections still control which readings HA receives. Unsupported or disconnected readings are unavailable.

## Visual display arrangement

The bundled [WHAT display layout card](DISPLAY-CARD.md) adds drag-and-drop positioning, rotation, inactive displays, mode selection and one Apply layout action to a Home Assistant dashboard. The integration loads the card automatically. After updating the integration, restart Home Assistant and refresh the browser.
