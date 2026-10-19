# Send Windows notifications from Home Assistant

Use compatible releases of the WHAT Windows app and integration. Restart Home Assistant after replacing the integration files.

1. On the PC, enable **Modules → Windows → Notifications from HA**. It is off by default and remembered in QSettings.
2. In HA, open **Developer tools → Actions** and choose **WHAT: Send Windows notification** (`what.send_notification`).
3. Select the WHAT PC device. Enter a title and message, then run the action. The same action can be used in automations and scripts.

```yaml
action: what.send_notification
target:
  device_id: YOUR_WHAT_PC_DEVICE_ID
data:
  title: Washing finished
  message: "Your washing machine has finished.\nPlease empty it."
  level: information
```

`device_id` is the Home Assistant device-registry ID of your PC, not its hostname, IP address or WHAT protocol UUID. Selecting the device in the action editor fills this correctly.

- `title`: optional; defaults to `Home Assistant`. One line, maximum 63 Windows UTF-16 text units.
- `message`: required; maximum 255 Windows UTF-16 text units. Line breaks are allowed. Emoji outside the basic Unicode plane count as two units.
- `level`: `information` (default), `warning`, or `error`; selects the notification icon. It does not override Windows notification preferences.

Text is passed as plain text. No HTML rendering, commands, executable links or remote images are evaluated. The app rejects oversized messages instead of truncating them and accepts at most one notification per second. The HA action does not retry after a timeout or connection failure.

The app uses Windows system-tray notification support through Qt. It must be running in the signed-in desktop session, with its tray icon available. **Works in Qt Creator/debug builds and ordinary installed/portable builds. No MSIX package or notification-reading permission is needed.** The separate Notification collection submodule reads other applications' notifications and still has its own requirements; it can remain disabled when sending notifications.

Windows settings, Do not disturb and OS notification throttling can suppress or replace messages. A successful action means the request was submitted, not that Windows displayed it or somebody read it. Optional response data contains `submitted: true` and explains this distinction. Notifications are not a persistent delivery queue. Clicking a notification opens WHAT.

The diagnostic log records submission/rejection, severity and text lengths, not the message contents. Disabling the Windows module or Notifications from HA immediately blocks subsequent remote notification requests. In explicit `--test-mode`, validation runs but notifications are not displayed; a normal debug build is not test mode.
