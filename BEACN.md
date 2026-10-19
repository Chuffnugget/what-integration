# BEACN

Enable **Modules > BEACN** in WHAT and keep the official BEACN app running. The
module defaults to off. Enable its Home Assistant control permission if you want
remote controls. Cloud sharing and control need the module's separate Cloud
permission as well.

The Windows module targets BEACN 1.4.2.12 with the 6.16.0.0 x64 driver (API 5.16).
Other versions undergo compatibility and live mixer checks. Compatible versions
can work with a warning; failed checks identify the affected controls. The module
shows the detected app and driver versions. See the
[Windows BEACN guide](https://github.com/Chuffnugget/what-windows/blob/main/docs/BEACN.md)
for connection setup, compatibility checks and diagnostic tools.

## Readings and controls

The integration creates number, switch, select and button entities for the
available controls, plus a connection status sensor. Controls include linked
Personal/Audience volume, knob mute, audience buttons, submix, knob pages,
Personal A/B devices, Audience output, profiles, default-device assignments,
automatic default reset, screen theme, brightness and dim timeout.

Channel readings include independent Personal/Audience levels, mute state and
linking. Other readings include page, dimming, sample rate and observed Windows
defaults. Select these readings in WHAT's sensor browser using their Local and
Cloud checkboxes. The module's Cloud permission also applies to individually
selected BEACN readings.

Independent unlinked fader writes, Aux mapping, mute configuration editing,
per-app routing and microphone DSP/EQ are not supported. Unlinked channel volumes
remain visible, but the linked-volume control is unavailable.

## Connection problems

Open **Modules > BEACN** for detected file paths, versions and the reason a
connection or control is unavailable. **Auto-detect** finds the installed files;
**Reconnect** checks them again. Snapshots older than 15 seconds become unavailable
until fresh readings arrive.

Commands are not automatically retried. Check the result in BEACN before repeating
a command whose outcome is uncertain. Activity logs are kept in the PC profile's
`logs/beacn` folder for seven days, up to 30 files. Volume CSV and state JSON
exports require verbose BEACN logging.
