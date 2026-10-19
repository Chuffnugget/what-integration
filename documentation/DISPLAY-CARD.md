# Interactive WHAT display layout card

The card displays monitor rectangles, names and modes using the Display arrangement sensor for a paired PC. It does not capture or stream screen contents.

## Install

1. Install compatible releases of the Windows app and Home Assistant integration using the [setup guide](../README.md#install-and-pair). When updating, keep the existing WHAT entry and PC profile, then restart Home Assistant.
2. Enable **Modules > Windows > Displays** and its **Home Assistant display-control permission** on the PC. The card can show metadata without control permission, but editing is disabled.
3. Refresh the Home Assistant browser page after restarting HA. WHAT automatically registers and loads its bundled card; no dashboard resource entry is needed. Its URL includes a content hash so updated JavaScript replaces cached copies.
4. Add **WHAT display layout** through the card picker, or keep your existing manual card YAML:

```yaml
type: custom:what-display-layout-card
entity: sensor.my_pc_display_arrangement
title: Monitor manager
```


Select the actual Display arrangement sensor belonging to your PC. Entity names depend on your existing HA naming settings; the example is a placeholder. The card's visual editor lets you choose this sensor. Each card controls one PC. You must be a Home Assistant administrator to apply an arrangement, matching WHAT's other explicit PC-command services.

## Edit

- Drag monitor rectangles to move them. Edges snap and alignment guides appear. Coordinates describe desktop pixels, not monitor physical dimensions or DPI scaling.
- Drag a monitor's rotation handle for 90-degree steps, or use Rotate / Orientation. A duplicated desktop group rotates together.
- Drag an inactive, physically connected display onto the canvas, or select it and choose Enable / Extend desktop. A physically unplugged display cannot be activated.
- Select a monitor for resolution, refresh rate and Make primary. Options come from Windows' reported modes; Windows/graphics drivers may reject or adjust a requested combination. Current values remain available when enumeration supplies no alternatives.
- Duplicate and Disable are explicit actions. Overlapping independent monitors is rejected; dropping outside the canvas does not silently disable a display. At least one active display is required.
- On phones, use the monitor selector, orientation selector and direction buttons. A focused monitor also accepts arrow keys (10 pixels, Shift for 100) and R to rotate.
- Apply layout sends one complete layout. Dragging and other edits never send individual display commands. Discard changes restores the current Windows arrangement in the editor.

The preview updates when Windows changes. A change while editing produces a conflict notice; reload before applying. The PC independently checks the expected arrangement immediately before planning the change. A successful change has no confirmation countdown or automatic rollback. Failed/interrupted changes retain the existing explicit recovery mechanism on the PC. If a connection times out, the outcome may be unknown: reload and inspect; the card never resends automatically.

## Availability and setup problems

An unavailable sensor means the PC is disconnected or Displays is disabled/unavailable. A read-only card can also mean HA display control is off, a change is busy, or recovery needs attention. The Windows app must support the `layout_editor` capability. If it is missing, update the Windows app as well as the integration.

This is a dashboard card, not a replacement for Home Assistant's built-in device page. It uses the existing WHAT connection, whether direct WebSocket or MQTT; no extra PC port or credentials are introduced.

## Developer checks

Run the integration's pytest suite. The card browser test uses Playwright (`npm install --no-save playwright`, followed by `npx playwright install chromium`), then `node tests/test_display_card.cjs`. Optionally set `WHAT_TEST_BROWSER` to a Chromium-based browser executable and `WHAT_CARD_ARTIFACTS` to an existing output directory. Tests use a synthetic HA object and intercept all browser requests; they never contact or change a real PC.

An old manual dashboard resource entry is no longer needed. You can remove it;
leaving it in place does not load a second copy of the card.
