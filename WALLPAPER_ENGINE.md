# Wallpaper Engine

Enable **Modules → Wallpaper Engine**. Wallpaper Engine must already be running in the same Windows user session. WHAT discovers its running executable or Steam installation/library folders; **Browse** selects wallpaper32.exe or wallpaper64.exe for a manual override. **Auto-detect** clears that override. Invalid overrides produce an error rather than silently selecting another installation.

The module runs discovery and CLI requests on a worker, with bounded timeouts. Polling is every 15 seconds; it does not start Wallpaper Engine, alter its config.json, download Workshop items or issue commands automatically. Pause, stop, play, mute and unmute operate globally. The advanced action form supports wallpaper files, saved playlist/profile names, next/remove wallpaper, desktop icons, property JSON and monitor/location/window targeting. Monitor indices start at 0; the default target depends on the operation. Use **Share JSON** in Wallpaper Engine to obtain a wallpaper's property names. There is no universal property catalog: it differs by wallpaper.

Enable **Allow Home Assistant control** to accept commands. Cloud access is separately disabled by default and applies to both metadata and commands, including a cloud-scoped MQTT broker. **Share current wallpaper path** is separately opt-in: absolute paths can include personal folder names. The executable override is never sent to HA. Current wallpaper is normally queried for the default display; an explicit getWallpaper action can query another target. The next scheduled refresh returns to the default target.

## Home Assistant

Update both WHAT and its integration, then restart Home Assistant. A Wallpaper Engine status sensor and play/pause/stop/mute/unmute/next/icons buttons appear under the existing PC. They are unavailable when the module/permissions are disabled. No entity recreation is needed when switching HA API/MQTT.

For complete targeting/options, use the **WHAT: Wallpaper Engine** action (`what.wallpaper_engine`):

```yaml
action: what.wallpaper_engine
data:
  device_id: YOUR_HOME_ASSISTANT_PC_DEVICE_ID
  operation: openPlaylist
  options:
    playlist: Evening
    monitor: 0
```

Other examples for `options`:

- openWallpaper: `file: 'C:/wallpapers/example/project.json'`, optional `preset`, `monitor` or `location`.
- openProfile: `profile: Gaming` (an existing saved Wallpaper Engine display profile).
- applyProperties: `properties: {rate: 10}`, optional monitor/location. Enter the properties as an object; WHAT adds Wallpaper Engine's required raw-JSON delimiters itself.
- Pop-out wallpaper: openWallpaper with file, `playInWindow: My wallpaper`, optional width/height/x/y and boolean activate/borderless.
- nextWallpaper, closeWallpaper, getWallpaper: optional monitor or location. closeWallpaper removes the active wallpaper, not the wallpaper file.
- play, pause, stop, mute, unmute, hideIcons, showIcons: no options.

Use either monitor or location, not both. File paths refer to the Windows PC and must exist there. User-originated advanced HA action calls require an HA administrator; automations can call the action. PC-side permission checks still apply. Arguments are validated and passed directly to QProcess, not through PowerShell/cmd. Unknown operations/arguments and invalid JSON/target dimensions are rejected.

## Limits and state accuracy

Wallpaper Engine reports the current wallpaper path, but not its pause, mute
or playback state. WHAT exposes these commands as buttons. A successful command
confirms dispatch; it does not confirm the resulting state. Commands are not
retried after a timeout or disconnect. The current path is shared only when its
sensor attribute is enabled.

The official interface does not document wallpaper/playlist/profile enumeration, so names and files are supplied explicitly. Internal monitor location IDs can be obtained from Wallpaper Engine when needed.

Reference: https://help.wallpaperengine.io/en/functionality/cli.html
Wallpaper Engine is a separate proprietary application. WHAT is not affiliated with its developers; the logo identifies the optional module.
