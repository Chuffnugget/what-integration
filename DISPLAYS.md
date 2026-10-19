# Windows display control and saved layouts

Install compatible releases of WHAT and its Home Assistant integration. After
updating the integration, restart Home Assistant. Enable the Displays submodule
on the PC before using its controls.

## Interactive dashboard editor

See [DISPLAY-CARD.md](DISPLAY-CARD.md) for dashboard editor setup and applying a
complete layout.

## Enable and use

1. Open **Modules → Windows → Displays** and enable the submodule. It is off by default and independent of **Monitors**, which only provides monitor sleep.
2. Enable **Allow Home Assistant to change display layouts** if you want remote controls. This permission is off by default; local GUI controls do not require it.
3. Open **Sensors**, include **Windows** in the source filter, and search for `Displays` or the monitor model. Each monitor has a collapsible category with readings and a **Display controls** row. Clear **Favourites** if it hides the controls.
4. Choose **Extended**, **Disconnected**, or **Mirror [other monitor]**. Use **Make primary** to move the primary desktop to an enabled monitor. The change runs immediately; there is no Apply button.
5. Display changes apply immediately without a Keep prompt or automatic rollback.

**Disconnected** removes an output from the Windows desktop; it does not switch off electrical power. **Make primary** is separate from Extended/Mirrored. A mirrored set shares one primary desktop, so its members can all report primary. The last enabled display cannot be disconnected.

The app can request a mixed arrangement such as two mirrored monitors plus another extended monitor. Windows and the graphics driver must support the requested paths and modes; particularly, cross-adapter mirroring may not be possible. Errors include the Windows error code in the on-screen status and diagnostic log.

## Save a whole arrangement

On the Sensors page, use **Manage Layouts** in the **DISPLAY LAYOUT** row:

1. Arrange the displays in WHAT or Windows Display settings. Resolve any interrupted/failed change first.
2. Enter a descriptive name, such as `Desk`, `Gaming`, or `TV`.
3. Click **Save Current**. The app reads the current Windows configuration afresh and saves it under that name.
4. Select the name in **DISPLAY LAYOUT** to restore it. You can also Restore or Delete it in Manage Layouts.

Up to 32 uniquely named layouts can be saved. To replace one, delete it and save the current arrangement under the same name. Deleting a layout does not change the screens.

Example layouts:

| Name | Arrangement |
| --- | --- |
| Desk | Both desk monitors extended; left monitor primary |
| Gaming | Main monitor enabled; other outputs disconnected from desktop |
| TV | TV enabled and primary; desk monitors disconnected from desktop |

A saved layout applies the complete arrangement in one Windows API call. Connected monitors absent from that layout are disabled. If a required monitor is missing, the app rejects the layout instead of silently applying only part of it. HA excludes such layouts from its selector.

Saved and per-monitor changes apply without confirmation. Driver adjustments are reported without automatic rollback.

## What is saved

Layouts contain enabled monitor identities, mirror-group membership, the primary desktop origin, desktop positions, source dimensions and rotation. Windows chooses compatible output timings. **Exact refresh rate, HDR, VRR, colour settings and Windows UI scaling are not captured or controlled by this feature.** Use Windows Display settings for those. Adding an extended display places it to the right; custom positions can be arranged in Windows and captured with Save Current.

Monitor IDs are hashes of the Windows monitor device path. They do not use unstable `DISPLAY1` numbers or boot-specific adapter identifiers. Reconnecting to the same connector normally retains identity; moving cables between ports, changing a dock/GPU or reinstalling a driver can change it. If a saved layout reports a missing monitor after such a change, recreate the layout. Identical monitor models are distinguished in the GUI with a short identity suffix.

## Home Assistant

After enabling the submodule and HA permission, the existing PC device gains:

- A desktop-mode select and Make primary button for each discovered monitor.
- One **Display layout** select containing available saved layout names.
- **Keep display layout** and **Restore previous display layout** buttons, available only for interrupted/failed changes with recovery data.

The mode selects also expose connected/enabled/primary, width, height, refresh and desktop position as attributes. The Windows sensor browser has separate readings for those values; tick readings you want published as individual HA sensors. Controls are independent of the sensor sharing checkboxes. Entities use confirmed Windows readback, with no optimistic state or automatic command retries.

Example HA action (replace the example entity ID with your actual PC's entity):

```yaml
action: select.select_option
target:
  entity_id: select.your_pc_display_layout
data:
  option: Gaming
```

Use saved, tested layouts for unattended automations. Per-monitor mode changes apply without a confirmation window. Disabled submodules disappear from the Windows sensor browser; existing HA entities become unavailable and keep their entity IDs/history. Unplugged monitors also become unavailable and are rediscovered when they return with the same identity.

## Recovery, persistence and diagnostics

The app saves layouts, module state and HA permission in its existing **QSettings settings.yaml**, whose exact path is logged at startup. A previous layout is persisted before changing the OS configuration. Pending recovery data survives a process interruption; on the next app start, the app offers manual recovery without automatically restoring it. Failed restoration retains the data and reports an error; reconnect missing displays and use Restore Previous again.

The timeout needs the app process to be running; it cannot recover screens while Windows or WHAT is stopped. WHAT does not force a saved layout at every startup. Keep accepts the current session's configuration; it does not explicitly write the Windows display persistence database. Named layouts remain in QSettings and can be reapplied.

Windows display changes run serially on a dedicated **QThread**, separate from telemetry, GUI and WebSocket work. Display/device change events trigger refresh, with a 10-second fallback check. Display renegotiation can briefly blank screens and takes as long as the driver needs; a fast WebSocket does not remove that delay. The app must run in the signed-in console desktop; Remote Desktop sessions can be denied access by Windows.

Implementation uses `QueryDisplayConfig`, `DisplayConfigGetDeviceInfo` and `SetDisplayConfig` with validation before applying. No PowerShell commands, iCUE APIs, external display utilities or registry display edits are required.

## Validation boundaries

Automated tests cover layout planning, mirror-group splitting, primary reassignment, last-display protection, invalid geometry/identities, module filtering, recovery state, protocol validation and HA entity commands. Actual driver compatibility and the visible result must be checked on the target monitor/GPU setup. Compilation and unit tests alone do not prove every physical multi-monitor combination works.

WHAT reads active rotation from Windows. For disconnected monitors, it matches the physical monitor interface to saved Windows display settings and preserves the reported orientation and dimensions when extending. If the driver no longer exposes saved settings, the preferred mode is the fallback. Save a named WHAT layout for exact restoration.
