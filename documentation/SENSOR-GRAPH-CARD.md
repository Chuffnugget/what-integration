# WHAT Sensor Panel

A compact card with up to six selectable readings, live values and a shared graph.
The default layout has three generic, unassigned slots with coral, blue and gold
lines. Each slot uses its selected sensor's name unless you enter a custom label.
It is included in WHAT and needs neither mini-graph-card nor
card-mod. Any numeric Home Assistant sensor or number entity can be selected.

## Add it through the dashboard

1. Update the WHAT integration and restart Home Assistant. Refresh your browser.
2. Edit a dashboard, choose **Add card**, and search for **WHAT Sensor Panel**.
3. Open each **Slot** and choose its sensor. Set its label, colour, decimal places,
   and whether it draws a line or a faded fill. Use **Add sensor slot**, **Remove
   slot**, or **Move up/down** to change the layout.
4. Set the title, icon and optional hostname. Open **Graph and updates** to change
   history length, resolution, height, line width, scales, legend and update rates.
5. Save. Clicking a reading opens Home Assistant's more-info dialog for that entity.

**Suggested readings** offers matching CPU temperatures, CPU load, memory usage,
GPU temperatures, power and fan sensors when you open it. Suggestions are optional:
the main sensor selector always lets you choose any numeric reading. Nothing is
assigned automatically, and the slots have no fixed CPU, load or memory roles.

Colours have a compact picker, editable hex value, preset swatches and Reset.
Invalid hex text does not overwrite the last valid colour. Decimal places, unit
labels and line/fill switches are under each slot's **Formatting and graph** section.

All options are in the visual editor. WHAT automatically loads and versions the
card resource; no resource URL or YAML setup is required. Existing WHAT display
layout cards continue to work. Each graph card has its own settings.

The internal type remains `custom:what-sensor-graph-card` so existing cards still
load. Existing titles and custom labels are preserved; clear a custom label to
use the selected sensor's name.

## Offline readings

When every selected reading is missing, unavailable, unknown or non-numeric,
the graph dims and displays **Hostname is offline**, with a quiet coral status
light. Old history stays visible behind the message. Live readings restore the
normal card automatically. One unavailable sensor only affects its own tile.
The card never displays `NaN` or replaces an unavailable value with zero.

Set **Hostname** to the PC name you want shown. Left blank, the card uses the
WHAT parent device name when Home Assistant has supplied its device registry;
otherwise it says **Device is offline**. For readings from several PCs, use a
separate card per PC or a suitable custom name.

Offline is inferred from the selected readings, not a network ping. Disabled
modules, unshared readings and removed entities can produce the same status.

The offline message also shows **Last seen: 5 minutes ago**. For readings from
one WHAT PC, the card automatically finds that PC's **Last seen** diagnostic,
including when its entity ID has been renamed. This is the last communication
received by Home Assistant, not the last time a temperature changed. Hover over
the label for the exact date and time.

**Last seen sensor** in the visual editor optionally selects another timestamp
sensor. Without a usable timestamp the card says **Last seen: unknown**; it does
not invent a time from unavailable state changes or old graph samples. Automatic
selection requires all slots to belong to the same PC. The age label updates
every 30 seconds while visible and offline, without network requests or graph
redraws, and pauses when the card or browser tab is hidden.

## History and performance

- Defaults: one hour, 30 time buckets per hour, 100px graph, 3px lines and one
  decimal place. Lines are straight and do not animate.
- Live display updates are limited to once a second by default. Readings arrive
  through Home Assistant's state feed. This does not change the sensor's
  sampling interval or make WHAT poll the PC more frequently.
- Recorder history loads initially, then incrementally every 60 seconds. Live
  updates do not issue history requests. Requests and redraw timers pause when
  the card is off screen or the browser tab is hidden, and stop when removed.
- Editing titles, colours or labels preserves the graph and does not refetch
  history. Slots with their graph line disabled do not request recorder history.
- Time buckets preserve first/last values and extremes rather than averaging
  away spikes. Missing data breaks the line. History is bounded to 24 hours
  and 120 buckets per hour, with a maximum of six slots.
- Different units have separate graph ranges by default. Compare trends, not
  line heights across different units. Choose **Shared range for all lines**
  when a common numeric scale is appropriate.
- Unit overrides only change the displayed label; they do not convert values.
- If recorder history is unavailable, current values still work and the card
  retries history automatically. Sensors excluded from Recorder have only the
  live samples collected while the card is present.

## Optional YAML

The visual editor writes the same configuration. Entity IDs below are placeholders.

```yaml
type: custom:what-sensor-graph-card
title: CPU
icon: mdi:cpu-64-bit
hostname: Gaming PC
hours_to_show: 1
points_per_hour: 30
height: 100
line_width: 3
update_interval: 1
history_interval: 60
scale: unit
show_legend: false
slots:
  - entity: sensor.my_pc_core_max
    label: CPU TEMP
    colour: '#ff806b'
    decimals: 1
    graph: true
    fill: true
  - entity: sensor.my_pc_cpu_usage
    label: LOAD
    colour: '#62b4ff'
    decimals: 1
    graph: true
    fill: false
  - entity: sensor.my_pc_memory_load
    label: RAM
    colour: '#eac66b'
    decimals: 1
    graph: true
    fill: false
```

## Developer checks

Integration validation and release workflows run both bundled cards' browser
tests, plus Python registration and packaging tests. Locally:

```sh
python -m pytest -q tests --ignore=tests/ha_runtime
npm install --no-save --package-lock=false playwright
npx playwright install chromium
node --test tests/test_display_card.cjs tests/test_sensor_graph_card.cjs
```

Browser fixtures contain synthetic readings. They do not connect to a real PC or
Home Assistant. `WHAT_CARD_ARTIFACTS` selects a folder for visual test screenshots;
`WHAT_TEST_BROWSER` optionally selects an installed Chromium executable.
