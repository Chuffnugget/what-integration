/* WHAT Sensor Panel: dependency-free Lovelace card and visual editor.
 * Keep the original element/resource names so saved dashboards keep working. */
const COLOURS = ['#ff806b', '#62b4ff', '#eac66b', '#83d5ad', '#c4a0ff', '#f39ac7'];
const COLOUR_NAMES = ['Coral', 'Blue', 'Gold', 'Mint', 'Violet', 'Rose'];
const DEFAULTS = {
  title: 'Sensors', icon: 'mdi:chart-box-outline', hostname: '', last_seen_entity: '', hours_to_show: 1,
  points_per_hour: 30, height: 100, line_width: 3, update_interval: 1,
  history_interval: 60, scale: 'unit', show_legend: false,
};
const LIMITS = {
  hours_to_show: [0.1, 24], points_per_hour: [1, 120], height: [60, 300],
  line_width: [1, 8], update_interval: [1, 60], history_interval: [15, 3600],
};
const SVG = 'http://www.w3.org/2000/svg';
let cardSequence = 0;
// The display registry changes independently of sensor states. Share this small
// index across cards instead of scanning thousands of entities on each update.
const lastSeenIndexes = new WeakMap();

function lastSeenIndex(registry) {
  if (!registry) return new Map();
  if (!lastSeenIndexes.has(registry)) {
    const index = new Map();
    for (const [id, entry] of Object.entries(registry)) {
      if (entry.platform !== 'what' || entry.translation_key !== 'app_last_seen') continue;
      const ids = index.get(entry.device_id) ?? [];
      ids.push(id); index.set(entry.device_id, ids);
    }
    lastSeenIndexes.set(registry, index);
  }
  return lastSeenIndexes.get(registry);
}

export function lastSeenTimestamp(value, now = Date.now()) {
  if (typeof value !== 'string' || !/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/i.test(value)) return null;
  const time = Date.parse(value);
  // A timestamp sensor's value is the reception time. Its last_changed or
  // last_updated describes HA's state object and must never substitute for it.
  return Number.isFinite(time) && time > 0 && time <= now + 60000 ? time : null;
}

export function lastSeenAge(time, now = Date.now(), language = 'en') {
  const seconds = Math.max(0, (now - time) / 1000);
  if (seconds < 60) return 'less than a minute ago';
  const [size, unit] = seconds < 3600 ? [60, 'minute'] : seconds < 86400 ? [3600, 'hour'] : [86400, 'day'];
  let formatter;
  try { formatter = new Intl.RelativeTimeFormat(language, { numeric: 'always' }); }
  catch (_) { formatter = new Intl.RelativeTimeFormat('en', { numeric: 'always' }); }
  return formatter.format(-Math.floor(seconds / size), unit);
}

export function numericState(value) {
  if (typeof value !== 'number' && typeof value !== 'string') return null;
  if (typeof value === 'string' && !value.trim()) return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function normaliseConfig(config) {
  if (!config || typeof config !== 'object') throw new Error('Card configuration is required.');
  const result = { ...DEFAULTS, ...config, type: 'custom:what-sensor-graph-card' };
  for (const [key, [min, max]] of Object.entries(LIMITS)) {
    const n = numericState(result[key] ?? DEFAULTS[key]);
    if (n === null || n < min || n > max) throw new Error(`${key} must be between ${min} and ${max}.`);
    result[key] = n;
  }
  for (const key of ['title', 'icon', 'hostname']) result[key] = String(result[key] ?? '').slice(0, 200);
  result.last_seen_entity = String(result.last_seen_entity ?? '').trim();
  if (result.last_seen_entity && !/^sensor\.[a-z0-9_]+$/.test(result.last_seen_entity)) throw new Error('Choose a last-seen timestamp sensor.');
  if (!['unit', 'shared'].includes(result.scale)) throw new Error('Choose unit or shared graph scales.');
  result.show_legend = Boolean(result.show_legend);
  const slots = config.slots ?? [{}, {}, {}];
  if (!Array.isArray(slots) || slots.length < 1 || slots.length > 6) throw new Error('Choose between one and six slots.');
  result.slots = slots.map((slot, i) => {
    if (!slot || typeof slot !== 'object') throw new Error('Each slot must be an object.');
    const entity = String(slot.entity ?? '').trim();
    if (entity && !/^(sensor|number|input_number)\.[a-z0-9_]+$/.test(entity)) throw new Error('Choose a numeric sensor or number entity.');
    const colour = slot.colour ?? COLOURS[i];
    if (!/^#[0-9a-f]{6}$/i.test(colour)) throw new Error('Slot colours must be six-digit hex colours.');
    const decimals = slot.decimals ?? 1;
    if (!Number.isInteger(decimals) || decimals < 0 || decimals > 4) throw new Error('Decimals must be between zero and four.');
    return {
      entity, label: String(slot.label ?? '').slice(0, 80),
      colour, decimals, unit: String(slot.unit ?? '').slice(0, 24),
      graph: slot.graph !== false, fill: slot.fill ?? i === 0,
    };
  });
  return result;
}

export function slotLabel(slot, index, hass) {
  return slot.label.trim() || hass?.states[slot.entity]?.attributes?.friendly_name
    || (slot.entity ? slot.entity.split('.')[1].replaceAll('_', ' ') : `Sensor ${index + 1}`);
}

// Suggestions are optional shortcuts, never fixed slot roles or auto-selected
// entities. Only scan when the user opens this menu, not on every hass update.
export function sensorSuggestions(hass) {
  const groups = [
    ['CPU temperature', name => /\b(cpu|core)\b/.test(name) && /\b(temp|temperature|thermal|max)\b/.test(name)],
    ['CPU load', name => /\bcpu\b/.test(name) && /\b(load|usage|utilization)\b/.test(name)],
    ['Memory usage', name => /\b(ram|memory)\b/.test(name) && /\b(load|usage|used|utilization)\b/.test(name)],
    ['GPU temperature', name => /\bgpu\b/.test(name) && /\b(temp|temperature|hotspot|hot spot)\b/.test(name)],
    ['Power', name => /\bpower\b/.test(name)],
    ['Fan speed', name => /\bfan\b/.test(name)],
  ];
  const candidates = Object.entries(hass?.states ?? {}).filter(([id, state]) =>
    /^(sensor|number|input_number)\./.test(id) && (numericState(state.state) !== null || state.attributes?.unit_of_measurement))
    .map(([id, state]) => ({ id, label: state.attributes?.friendly_name || id,
      search: `${id} ${state.attributes?.friendly_name ?? ''}`.toLowerCase().replaceAll('_', ' ') }));
  const seen = new Set();
  return groups.flatMap(([category, matches]) => candidates.filter(item => !seen.has(item.id) && matches(item.search))
    .sort((a, b) => a.label.localeCompare(b.label)).slice(0, 2).map(item => {
      seen.add(item.id);
      return { entity: item.id, label: `${category}: ${item.label}`, category };
    }));
}

// Keep the first, lowest, highest and last sample in each time bucket. This
// bounds DOM/path work without averaging away short temperature or load peaks.
// Preserve a gap marker even when it falls between samples in the same bucket.
export function compactPoints(points, start, end, bucketMs) {
  const ordered = [...new Map(points.filter(p => Number.isFinite(p.t) && p.t <= end)
    .map(p => [p.t, { t: p.t, v: numericState(p.v) }])).values()].sort((a, b) => a.t - b.t);
  const visible = ordered.filter(p => p.t >= start);
  const previous = ordered.findLast(p => p.t < start);
  if (previous) visible.unshift({ ...previous, t: start });
  const buckets = new Map();
  for (const point of visible) {
    const key = Math.floor(point.t / bucketMs);
    const bucket = buckets.get(key) ?? [];
    bucket.push(point);
    buckets.set(key, bucket);
  }
  const result = [];
  for (const bucket of buckets.values()) {
    const valid = bucket.filter(p => p.v !== null);
    const keep = new Set([bucket[0], bucket.at(-1)]);
    if (valid.length) {
      keep.add(valid.reduce((a, b) => a.v <= b.v ? a : b));
      keep.add(valid.reduce((a, b) => a.v >= b.v ? a : b));
    }
    const selected = [...keep].sort((a, b) => a.t - b.t);
    let previousIndex = -1;
    for (const point of selected) {
      const index = bucket.indexOf(point);
      const gap = bucket.slice(previousIndex + 1, index).find(p => p.v === null);
      if (gap) result.push(gap);
      result.push(point);
      previousIndex = index;
    }
  }
  return result;
}

function element(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}
function svgElement(tag, attributes = {}) {
  const el = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attributes)) el.setAttribute(key, value);
  return el;
}

const STYLE = `
  :host { display:block; }
  ha-card { display:block; overflow:hidden; border-radius:20px; border:1px solid var(--divider-color,#42464e);
    background:var(--ha-card-background,var(--card-background-color,#202329)); box-shadow:none; color:var(--primary-text-color,#eee); }
  .header { display:flex; align-items:center; gap:12px; padding:14px 16px 0; min-height:24px; }
  h2 { font-size:14px; font-weight:650; letter-spacing:.08em; margin:0; flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  ha-icon { color:var(--secondary-text-color,#9da3af); --mdc-icon-size:22px; flex:none; }
  .tiles { display:grid; grid-template-columns:repeat(var(--columns),minmax(0,1fr)); gap:8px; padding:12px 16px; container-type:inline-size; }
  .tile { font:inherit; text-align:left; border:0; border-radius:12px; background:rgba(127,127,127,.07);
    color:var(--slot-colour); min-width:0; padding:8px 10px 9px; cursor:pointer; }
  .tile:disabled { cursor:default; }
  .tile:focus-visible { outline:2px solid var(--primary-color,#62b4ff); outline-offset:2px; }
  .label { display:block; font-size:10px; font-weight:600; letter-spacing:.08em; color:var(--secondary-text-color,#9da3af);
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap; margin-bottom:5px; }
  .reading { display:flex; flex-wrap:wrap; align-items:baseline; column-gap:2px; min-height:33px; }
  .value { font-size:clamp(20px,7cqi,28px); font-weight:600; line-height:1.15; letter-spacing:-.04em; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }
  .unit { font-size:12px; opacity:.7; overflow-wrap:anywhere; }
  .tile.invalid .value { font-size:13px; letter-spacing:0; color:var(--secondary-text-color,#9da3af); }
  .graph { position:relative; margin-bottom:4px; height:var(--graph-height); }
  svg { display:block; width:100%; height:100%; }
  .trace { vector-effect:non-scaling-stroke; fill:none; stroke-linejoin:round; stroke-linecap:round; }
  .grid { stroke:var(--divider-color,#42464e); stroke-opacity:.35; vector-effect:non-scaling-stroke; }
  .offline svg { opacity:.16; }
  .graph.offline { min-height:120px; }
  .message { position:absolute; inset:0; display:flex; align-items:center; justify-content:center; flex-direction:column; text-align:center; gap:5px; padding:8px 16px; }
  .message strong { font-size:15px; font-weight:600; overflow-wrap:anywhere; }
  .message small { font-size:11px; color:var(--secondary-text-color,#9da3af); }
  .offline .message { background:radial-gradient(ellipse at center,rgba(255,128,107,.09),transparent 75%); }
  .signal { width:7px; height:7px; border-radius:50%; background:#ff806b; box-shadow:0 0 0 5px #ff806b15,0 0 18px #ff806b50; margin-bottom:6px; }
  .history-note { font-size:11px; padding:0 16px 8px; color:var(--secondary-text-color,#9da3af); }
  .legend { display:flex; flex-wrap:wrap; gap:8px 14px; padding:0 16px 10px; font-size:11px; }
  .legend span::before { content:''; display:inline-block; width:12px; height:3px; background:var(--slot-colour); margin-right:5px; vertical-align:middle; }
  [hidden] { display:none !important; }
  @media (prefers-reduced-motion:reduce) { * { animation:none; transition:none; } }
`;

class WhatSensorGraphCard extends HTMLElement {
  static getConfigElement() { return document.createElement('what-sensor-graph-editor'); }
  static getStubConfig() { return normaliseConfig({}); }
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._id = ++cardSequence;
    this._series = new Map();
    this._generation = 0;
    this._onVisibility = () => this._schedule();
  }
  setConfig(config) {
    const next = normaliseConfig(config);
    if (JSON.stringify(next) === JSON.stringify(this._config)) return;
    const historyKey = c => c && JSON.stringify([
      [...new Set(c.slots.filter(s => s.graph).map(s => s.entity).filter(Boolean))].sort(),
      c.hours_to_show, c.points_per_hour,
    ]);
    const reload = historyKey(next) !== historyKey(this._config);
    const intervalChanged = this._config && next.history_interval !== this._config.history_interval;
    this._config = next;
    clearTimeout(this._paintTimer);
    clearTimeout(this._ageTimer);
    this._paintTimer = null;
    const selected = new Set(this._historyEntities());
    for (const id of this._series.keys()) if (!selected.has(id)) this._series.delete(id);
    if (reload) {
      ++this._generation;
      this._cancelHistory?.();
      this._pending = null;
      this._lastHistory = 0;
      this._nextHistory = 0;
      this._historyError = false;
    } else if (intervalChanged) {
      this._nextHistory = Date.now() + next.history_interval * 1000;
    }
    this._signature = '';
    this._build();
    this._capture();
    this._paint();
    this._schedule();
  }
  set hass(hass) {
    this._hass = hass;
    if (!this._active()) return;
    if (this._capture()) this._queuePaint();
    if (this._historyEntities().length && !this._nextHistory) this._schedule();
  }
  getCardSize() {
    const c = this._config;
    return Math.ceil((110 + Math.max(c?.height ?? 100, 120) + (Math.ceil((c?.slots.length ?? 3) / 3) - 1) * 74 + (c?.show_legend ? 20 : 0)) / 50);
  }
  getGridOptions() { return { columns: 12, rows: this.getCardSize(), min_columns: 6, min_rows: this.getCardSize() }; }
  connectedCallback() {
    // Wait for the first intersection result. Assuming visible here starts
    // history requests for every off-screen card in a large dashboard.
    this._inView = false;
    document.addEventListener('visibilitychange', this._onVisibility);
    this._observer = new IntersectionObserver(entries => {
      this._inView = entries[0].isIntersecting;
      this._schedule();
    });
    this._observer.observe(this);
    this._schedule();
  }
  disconnectedCallback() {
    document.removeEventListener('visibilitychange', this._onVisibility);
    this._observer?.disconnect();
    clearTimeout(this._timer);
    clearTimeout(this._paintTimer);
    clearTimeout(this._ageTimer);
    this._paintTimer = null;
    ++this._generation;
    this._cancelHistory?.();
    this._pending = null;
    this._nextHistory = 0;
  }
  _active() { return this.isConnected && this._inView && !document.hidden && this._hass && this._config; }
  _entities() { return [...new Set(this._config.slots.map(s => s.entity).filter(Boolean))]; }
  _historyEntities() { return [...new Set(this._config.slots.filter(s => s.graph).map(s => s.entity).filter(Boolean))]; }
  _lastSeenEntity() {
    if (this._config.last_seen_entity) return this._config.last_seen_entity;
    const parents = new Set();
    for (const id of this._entities()) {
      const deviceId = this._hass?.entities?.[id]?.device_id;
      const device = this._hass?.devices?.[deviceId];
      if (!device) return '';
      parents.add(device.via_device_id || deviceId);
    }
    if (parents.size !== 1) return '';
    const matches = lastSeenIndex(this._hass?.entities).get([...parents][0]) ?? [];
    return matches.length === 1 ? matches[0] : '';
  }
  _updateLastSeen() {
    clearTimeout(this._ageTimer);
    this._ageTimer = null;
    if (!this._lastSeenText || !this._offline) return;
    const entityId = this._lastSeenEntity();
    const time = lastSeenTimestamp(this._hass?.states[entityId]?.state);
    this._lastSeenText.textContent = time === null ? 'Last seen: unknown'
      : `Last seen: ${lastSeenAge(time, Date.now(), this._hass?.locale?.language)}`;
    let exact = '';
    if (time !== null) {
      try { exact = new Date(time).toLocaleString(this._hass?.locale?.language); }
      catch (_) { exact = new Date(time).toISOString(); }
    }
    this._lastSeenText.title = time === null
      ? 'No last-communication timestamp is available. Choose a timestamp sensor in the card editor if needed.'
      : `Last communication: ${exact}`;
    // Only the relative label needs to age. Do not redraw/fetch the graph for it.
    if (this._active() && time !== null) this._ageTimer = setTimeout(() => this._updateLastSeen(), 30000);
  }
  _hostname() {
    if (this._config.hostname.trim()) return this._config.hostname.trim();
    const hosts = new Set();
    for (const entityId of this._entities()) {
      const entry = this._hass?.entities?.[entityId];
      let device = this._hass?.devices?.[entry?.device_id];
      if (device?.via_device_id) device = this._hass.devices[device.via_device_id] ?? device;
      // WHAT's parent device is named "WHAT · <hostname>". Do not guess a PC
      // name from an arbitrary non-WHAT sensor's friendly name or entity ID.
      const match = device?.name?.match(/^WHAT\s*·\s*(.+)$/);
      if (match) hosts.add(match[1]);
      else return 'Device';
    }
    return hosts.size === 1 ? [...hosts][0] : 'Device';
  }
  _capture() {
    if (!this._hass) return false;
    const offline = this._entities().every(id => numericState(this._hass.states[id]?.state) === null);
    const lastSeenId = offline ? this._lastSeenEntity() : '';
    const signature = JSON.stringify([this._hostname(), this._hass.locale, lastSeenId,
      this._hass.states[lastSeenId]?.state, ...this._entities().map(id => {
      const state = this._hass.states[id];
      return [id, state?.state, state?.last_updated, state?.attributes?.unit_of_measurement, state?.attributes?.friendly_name];
    })]);
    if (signature === this._signature) return false;
    this._signature = signature;
    const now = Date.now();
    for (const id of this._historyEntities()) {
      const state = this._hass.states[id];
      const v = numericState(state?.state);
      const points = this._series.get(id) ?? [];
      const t = Math.min(now, Date.parse(state?.last_updated) || now);
      if (!points.length || points.at(-1).v !== v) points.push({ t, v });
      // Accumulate cheap live samples between paints. Do not sort an entire
      // history on every state event; the visible display is rate limited.
      const limit = Math.ceil(this._config.hours_to_show * this._config.points_per_hour) * 7 + 16;
      this._series.set(id, points.length > limit ? this._compact(points, now) : points);
    }
    return true;
  }
  _compact(points, now) {
    return compactPoints(points, now - this._config.hours_to_show * 3600000, now,
      3600000 / this._config.points_per_hour);
  }
  _queuePaint() {
    if (!this._active() || this._paintTimer) return;
    const delay = Math.max(0, this._config.update_interval * 1000 - (Date.now() - (this._paintedAt ?? 0)));
    this._paintTimer = setTimeout(() => { this._paintTimer = null; if (this._active()) this._paint(); }, delay);
  }
  _schedule() {
    clearTimeout(this._timer);
    if (!this._active()) {
      clearTimeout(this._paintTimer); this._paintTimer = null;
      clearTimeout(this._ageTimer); this._ageTimer = null;
      return;
    }
    this._capture();
    this._queuePaint();
    if (!this._historyEntities().length) return;
    if (this._pending == null && Date.now() >= (this._nextHistory ?? 0)) this._loadHistory();
    // No animation loop: only catch up history and the time window once a minute
    // (or the user's history interval), while visible.
    this._timer = setTimeout(() => this._schedule(), this._config.history_interval * 1000);
  }
  async _loadHistory() {
    const generation = this._generation;
    const now = Date.now();
    const ids = this._historyEntities();
    const start = Math.max(now - this._config.hours_to_show * 3600000, (this._lastHistory || 0) - 1000);
    this._nextHistory = now + this._config.history_interval * 1000;
    this._pending = generation;
    let deadline;
    const timeout = new Promise((_, reject) => {
      this._cancelHistory = () => reject(new Error('History request cancelled'));
      deadline = setTimeout(() => reject(new Error('History request timed out')), 30000);
    });
    try {
      const response = await Promise.race([timeout, this._hass.callWS({
        type: 'history/history_during_period', start_time: new Date(start).toISOString(),
        end_time: new Date(now).toISOString(), entity_ids: ids,
        include_start_time_state: true, significant_changes_only: false,
        minimal_response: true, no_attributes: true,
      })]);
      if (generation !== this._generation || !this.isConnected) return;
      for (const id of ids) {
        const samples = (Array.isArray(response?.[id]) ? response[id] : []).map(row => ({
          t: Number(row.lu ?? row.lc) * 1000, v: numericState(row.s),
        }));
        // Live samples win when the same instant arrives through both routes.
        this._series.set(id, this._compact([...samples, ...(this._series.get(id) ?? [])], Date.now()));
      }
      this._lastHistory = now;
      this._historyError = false;
    } catch (_) {
      if (generation !== this._generation) return;
      this._historyError = true;
    } finally {
      clearTimeout(deadline);
      if (generation === this._generation) {
        this._pending = null;
        this._cancelHistory = null;
        this._queuePaint();
      }
    }
  }
  _build() {
    const c = this._config;
    this.shadowRoot.replaceChildren(element('style', '', STYLE));
    const card = element('ha-card');
    card.style.setProperty('--graph-height', `${c.height}px`);
    const header = element('div', 'header');
    const title = element('h2', '', c.title);
    title.title = c.title;
    const icon = element('ha-icon'); icon.setAttribute('icon', c.icon); icon.hidden = !c.icon;
    header.append(title, icon); header.hidden = !c.title && !c.icon;
    const tiles = element('div', 'tiles');
    tiles.style.setProperty('--columns', Math.min(c.slots.length, 3));
    this._tiles = c.slots.map(slot => {
      const tile = element('button', 'tile'); tile.type = 'button'; tile.disabled = !slot.entity;
      tile.style.setProperty('--slot-colour', slot.colour);
      const label = element('span', 'label');
      const reading = element('span', 'reading');
      const value = element('span', 'value'), unit = element('span', 'unit');
      reading.append(value, unit); tile.append(label, reading); tiles.append(tile);
      tile.addEventListener('click', () => this.dispatchEvent(new CustomEvent('hass-more-info', {
        detail: { entityId: slot.entity }, bubbles: true, composed: true,
      })));
      return { tile, value, unit, label };
    });
    this._graph = element('div', 'graph');
    const svg = svgElement('svg', { viewBox: '0 0 600 100', preserveAspectRatio: 'none', 'aria-hidden': 'true' });
    for (const y of [25, 50, 75]) svg.append(svgElement('path', { d: `M0 ${y} H600`, class: 'grid' }));
    const defs = svgElement('defs'); svg.append(defs);
    this._paths = c.slots.map((slot, i) => {
      const id = `what-fill-${this._id}-${i}`;
      const gradient = svgElement('linearGradient', { id, x1: '0', x2: '0', y1: '0', y2: '1' });
      gradient.append(svgElement('stop', { offset: '0%', 'stop-color': slot.colour, 'stop-opacity': '.18' }),
        svgElement('stop', { offset: '100%', 'stop-color': slot.colour, 'stop-opacity': '0' }));
      defs.append(gradient);
      const fill = svgElement('path', { fill: `url(#${id})` });
      const line = svgElement('path', { class: 'trace', stroke: slot.colour, 'stroke-width': c.line_width });
      svg.append(fill, line);
      return { line, fill };
    });
    this._message = element('div', 'message'); this._message.setAttribute('role', 'status');
    this._signal = element('span', 'signal');
    this._messageTitle = element('strong'); this._messageDetail = element('small');
    this._lastSeenText = element('small', 'last-seen');
    this._message.append(this._signal, this._messageTitle, this._messageDetail, this._lastSeenText);
    this._graph.append(svg, this._message);
    this._historyNote = element('div', 'history-note'); this._historyNote.setAttribute('role', 'status');
    this._legend = element('div', 'legend'); this._legend.hidden = !c.show_legend;
    this._legendItems = c.slots.map(slot => {
      const item = element('span'); item.hidden = !slot.graph;
      item.style.setProperty('--slot-colour', slot.colour); this._legend.append(item);
      return item;
    });
    card.append(header, tiles, this._graph, this._historyNote, this._legend);
    this.shadowRoot.append(card);
  }
  _paint() {
    if (!this._config || !this._tiles) return;
    this._paintedAt = Date.now();
    const c = this._config, now = Date.now(), start = now - c.hours_to_show * 3600000;
    const values = c.slots.map(s => numericState(this._hass?.states[s.entity]?.state));
    const sourceUnits = c.slots.map(s => this._hass?.states[s.entity]?.attributes?.unit_of_measurement || '');
    const units = c.slots.map((s, i) => s.unit || sourceUnits[i]);
    let language = this._hass?.locale?.language;
    try { new Intl.NumberFormat(language); } catch (_) { language = 'en'; }
    const formatter = d => new Intl.NumberFormat(language, { minimumFractionDigits: d, maximumFractionDigits: d });
    c.slots.forEach((slot, i) => {
      const { tile, value, unit, label } = this._tiles[i];
      label.textContent = slotLabel(slot, i, this._hass);
      label.title = label.textContent;
      this._legendItems[i].textContent = label.textContent;
      const valid = values[i] !== null;
      let text = 'Choose sensor';
      if (slot.entity) {
        const state = this._hass?.states[slot.entity]?.state;
        text = !this._hass ? 'Loading' : state === undefined ? 'Not found' : state === 'unknown' ? 'Unknown' : 'Unavailable';
      }
      value.textContent = valid ? formatter(slot.decimals).format(values[i]) : text;
      unit.textContent = valid ? units[i] : '';
      tile.classList.toggle('invalid', !valid);
      tile.setAttribute('aria-label', `${label.textContent}: ${value.textContent} ${unit.textContent}`.trim());
    });
    const selected = c.slots.filter(s => s.entity);
    const offline = Boolean(this._hass && selected.length && values.every(v => v === null));
    this._offline = offline;
    this._lastSeenText.hidden = !offline;
    this._updateLastSeen();
    const graphEnabled = c.slots.some(s => s.entity && s.graph);
    this._graph.hidden = !graphEnabled && !offline && selected.length > 0;
    this._graph.classList.toggle('offline', offline);
    this._message.hidden = !offline && selected.length > 0 && Boolean(this._hass);
    this._signal.hidden = !offline;
    this._messageTitle.textContent = offline ? `${this._hostname()} is offline` : selected.length ? 'Connecting to Home Assistant' : 'Choose your sensors';
    this._messageDetail.textContent = offline ? 'Selected readings are unavailable.' : selected.length ? 'Waiting for current readings.' : 'Select a sensor for each slot in the card editor.';
    this._historyNote.hidden = !this._historyError || !graphEnabled;
    this._historyNote.textContent = this._historyError ? 'History unavailable. Live readings still update; retrying automatically.' : '';
    const groups = new Map();
    const series = c.slots.map(slot => this._compact(this._series.get(slot.entity) ?? [], now));
    c.slots.forEach((slot, i) => { if (slot.entity && slot.graph) this._series.set(slot.entity, series[i]); });
    c.slots.forEach((slot, i) => {
      if (!slot.graph) return;
      const key = c.scale === 'shared' ? '' : sourceUnits[i];
      const range = groups.get(key) ?? [Infinity, -Infinity];
      for (const p of series[i]) if (p.v !== null) { range[0] = Math.min(range[0], p.v); range[1] = Math.max(range[1], p.v); }
      groups.set(key, range);
    });
    c.slots.forEach((slot, i) => {
      const { line, fill } = this._paths[i];
      const range = groups.get(c.scale === 'shared' ? '' : sourceUnits[i]);
      if (!slot.graph || !range || !Number.isFinite(range[0])) { line.setAttribute('d', ''); fill.setAttribute('d', ''); return; }
      // Normalise before subtracting: even two finite values can overflow when
      // their difference is computed (for example -1e308 and +1e308).
      const magnitude = Math.max(Math.abs(range[0]), Math.abs(range[1]), 1);
      const min = range[0] / magnitude, max = range[1] / magnitude;
      const pad = Math.max((max - min) * .08, 1 / magnitude, Number.EPSILON * 8);
      const low = min - pad, high = max + pad;
      const x = t => ((t - start) / (now - start) * 600).toFixed(2);
      const y = v => (94 - (v / magnitude - low) / (high - low) * 88).toFixed(2);
      const segments = []; let segment = [];
      for (const point of series[i]) {
        if (point.v === null) { if (segment.length) segments.push(segment); segment = []; }
        else segment.push(point);
      }
      if (segment.length) {
        if (values[i] !== null) segment.push({ t: now, v: segment.at(-1).v });
        segments.push(segment);
      }
      line.setAttribute('d', segments.map(points => points.map((p, n) => `${n ? 'L' : 'M'}${x(p.t)} ${y(p.v)}`).join(' ')).join(' '));
      fill.setAttribute('d', slot.fill ? segments.map(points => `M${x(points[0].t)} 100 ${points.map(p => `L${x(p.t)} ${y(p.v)}`).join(' ')} L${x(points.at(-1).t)} 100 Z`).join(' ') : '');
    });
  }
}

const GENERAL_SCHEMA = [
  { name: 'title', selector: { text: {} } }, { name: 'icon', selector: { icon: {} } },
  { name: 'hostname', selector: { text: {} } },
  { name: 'last_seen_entity', selector: { entity: { filter: [{ domain: 'sensor', device_class: 'timestamp' }] } } },
];
const ADVANCED_SCHEMA = [
  ...Object.entries(LIMITS).map(([name, [min, max]]) => ({ name, selector: { number: { min, max, step: name === 'hours_to_show' ? .1 : 1, mode: 'box' } } })),
  { name: 'scale', selector: { select: { options: [{ value: 'unit', label: 'Separate range for each unit' }, { value: 'shared', label: 'Shared range for all lines' }] } } },
  { name: 'show_legend', selector: { boolean: {} } },
];
const SLOT_SCHEMA = [
  { name: 'entity', selector: { entity: { filter: [{ domain: ['sensor', 'number', 'input_number'] }] } } },
  { name: 'label', selector: { text: {} } },
];
const SLOT_OPTIONS_SCHEMA = [
  { name: 'decimals', selector: { number: { min: 0, max: 4, step: 1, mode: 'box' } } },
  { name: 'unit', selector: { text: {} } },
  { name: 'graph', selector: { boolean: {} } }, { name: 'fill', selector: { boolean: {} } },
];
const FIELD_LABELS = {
  title: 'Card title', icon: 'Icon', hostname: 'Hostname (optional)', entity: 'Sensor', label: 'Custom label (optional)',
  last_seen_entity: 'Last seen sensor (optional)',
  colour: 'Colour', decimals: 'Decimal places', unit: 'Unit label (optional)', graph: 'Show graph line', fill: 'Fade below line',
  hours_to_show: 'History (hours)', points_per_hour: 'Graph resolution (buckets per hour)', height: 'Graph height (px)',
  line_width: 'Line width (px)', update_interval: 'Live display interval (seconds)', history_interval: 'History refresh (seconds)',
  scale: 'Graph scales', show_legend: 'Show legend',
};
const HELP = {
  hostname: 'Shown as "Hostname is offline" when all selected readings are unavailable. Blank detects a WHAT device name, or uses Device.',
  last_seen_entity: 'Blank uses the WHAT Last seen sensor for the selected PC, even if renamed. Choose a timestamp sensor to override it. Shown while offline; unknown if no timestamp is available.',
  entity: 'Any numeric reading, for example CPU temperature, load, memory, power or fan speed.',
  label: 'Blank uses the selected sensor name. Every slot can show any reading.',
  unit: 'Blank uses the sensor unit. This changes the label, not the value or temperature conversion.',
  update_interval: 'Limits live value redraws. The sensor itself must publish updates; this does not poll the PC.',
  history_interval: 'Recorder history is fetched separately from live values. No history requests while the card is hidden.',
  points_per_hour: 'Preserves low and high readings in each bucket. More buckets draw more detail.',
  scale: 'Different units have separate ranges by default. Heights across different units are not directly comparable.',
};

class WhatSensorGraphEditor extends HTMLElement {
  constructor() { super(); this.attachShadow({ mode: 'open' }); this._forms = []; this._summaries = []; }
  setConfig(config) {
    const next = normaliseConfig(config);
    if (JSON.stringify(next) === JSON.stringify(this._config)) return;
    this._config = next;
    this._build();
  }
  set hass(hass) {
    this._hass = hass;
    for (const form of this._forms) form.hass = hass;
    this._updateSummaries();
  }
  connectedCallback() {
    // HA lazily loads its native form/selectors. Loading a built-in editor uses
    // the frontend's own modules, with no external library or pinned HA bundle.
    if (!customElements.get('ha-form') && window.loadCardHelpers) {
      window.loadCardHelpers().then(async helpers => {
        const card = await helpers.createCardElement({ type: 'entities', entities: [] });
        await card.constructor.getConfigElement?.();
      }).catch(() => { /* HA also loads ha-form as the card editor opens. */ });
    }
  }
  _emit() {
    try {
      this._config = normaliseConfig(this._config);
      this._error.hidden = true;
    } catch (error) {
      this._error.textContent = error.message;
      this._error.hidden = false;
      return;
    }
    this.dispatchEvent(new CustomEvent('config-changed', {
      detail: { config: { ...this._config, type: 'custom:what-sensor-graph-card' } }, bubbles: true, composed: true,
    }));
  }
  _updateSummaries() {
    this._summaries.forEach((summary, i) => {
      const slot = this._config.slots[i];
      summary.textContent = `Slot ${i + 1}${slot.entity || slot.label ? ': ' + slotLabel(slot, i, this._hass) : ''}`;
    });
  }
  _setSlot(index, patch) {
    this._config = { ...this._config, slots: this._config.slots.map((slot, i) => i === index ? { ...slot, ...patch } : slot) };
    this._updateSummaries();
    this._emit();
  }
  _colourControl(index) {
    const field = element('fieldset', 'colour-field');
    field.append(element('legend', '', 'Colour'));
    const row = element('div', 'colour-row');
    const picker = element('input', 'colour-swatch'); picker.type = 'color';
    picker.setAttribute('aria-label', `Choose colour for slot ${index + 1}`);
    const label = element('label', 'hex-label', 'Hex');
    const hex = element('input', 'hex-value'); hex.type = 'text'; hex.maxLength = 7; hex.spellcheck = false;
    hex.setAttribute('aria-label', `Hex colour for slot ${index + 1}`);
    const reset = element('button', '', 'Reset'); reset.type = 'button';
    reset.setAttribute('aria-label', `Reset colour for slot ${index + 1}`);
    const error = element('div', 'colour-error'); error.hidden = true; error.setAttribute('role', 'status');
    error.id = `colour-error-${index}`; hex.setAttribute('aria-describedby', error.id);
    const palette = element('div', 'palette');
    const refresh = colour => {
      picker.value = colour; hex.value = colour.toUpperCase();
      for (const button of palette.children) button.setAttribute('aria-pressed', String(button.dataset.colour === colour.toLowerCase()));
      error.hidden = true; hex.removeAttribute('aria-invalid');
    };
    const choose = colour => { refresh(colour); this._setSlot(index, { colour }); };
    for (let i = 0; i < COLOURS.length; i++) {
      const swatch = element('button', 'palette-swatch'); swatch.type = 'button';
      swatch.dataset.colour = COLOURS[i]; swatch.style.setProperty('--swatch', COLOURS[i]);
      swatch.setAttribute('aria-label', COLOUR_NAMES[i]); swatch.title = COLOUR_NAMES[i];
      swatch.addEventListener('click', () => choose(COLOURS[i])); palette.append(swatch);
    }
    picker.addEventListener('input', () => { hex.value = picker.value.toUpperCase(); });
    picker.addEventListener('change', () => choose(picker.value));
    const commitHex = () => {
      const candidate = '#' + hex.value.trim().replace(/^#/, '').toLowerCase();
      if (!/^#[a-f0-9]{6}$/.test(candidate)) {
        error.textContent = 'Enter six hex digits, for example #62B4FF.';
        error.hidden = false; hex.setAttribute('aria-invalid', 'true'); return;
      }
      choose(candidate);
    };
    hex.addEventListener('change', commitHex);
    hex.addEventListener('keydown', event => {
      if (event.key === 'Enter') { event.preventDefault(); commitHex(); }
      if (event.key === 'Escape') refresh(this._config.slots[index].colour);
    });
    reset.addEventListener('click', () => choose(COLOURS[index]));
    label.append(hex); row.append(picker, label, reset);
    field.append(row, palette, error);
    refresh(this._config.slots[index].colour);
    return field;
  }
  _form(schema, data, onChange) {
    const form = element('ha-form');
    form.hass = this._hass; form.schema = schema;
    // Forms own only their fields. A later title edit must not restore the
    // original slot list from a stale copy of the full card configuration.
    form.data = Object.fromEntries(schema.map(field => [field.name, data[field.name]]));
    form.computeLabel = field => FIELD_LABELS[field.name] ?? field.name;
    form.computeHelper = field => HELP[field.name];
    form.addEventListener('value-changed', event => {
      event.stopPropagation();
      form.data = event.detail.value;
      onChange(event.detail.value);
    });
    this._forms.push(form);
    return form;
  }
  _build(openSlots = new Set([...this.shadowRoot.querySelectorAll('details[data-slot]')].filter(el => el.open).map(el => Number(el.dataset.slot)))) {
    this._forms = [];
    this._summaries = [];
    this.shadowRoot.replaceChildren(element('style', '', `
      :host{display:block} details{border-top:1px solid var(--divider-color);padding:12px 0}
      summary{cursor:pointer;font-weight:600;padding:4px 0 12px;overflow-wrap:anywhere} ha-form{display:block}
      .actions{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}
      button{font:inherit;padding:8px 12px;color:var(--primary-text-color);background:var(--secondary-background-color);border:1px solid var(--divider-color);border-radius:8px;cursor:pointer}
      button:disabled{opacity:.45;cursor:default} p{color:var(--secondary-text-color);font-size:13px;line-height:1.5}
      [hidden]{display:none!important} button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--primary-color,#62b4ff);outline-offset:2px}
      .colour-field{border:0;padding:0;margin:20px 0;color:var(--primary-text-color);min-width:0}
      legend{font-weight:500;font-size:14px;margin-bottom:10px}
      .colour-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
      .colour-swatch{width:44px;height:38px;box-sizing:border-box;padding:3px;border:1px solid var(--divider-color);border-radius:8px;background:transparent;cursor:pointer}
      .hex-label{display:flex;gap:8px;align-items:center;font-size:13px;color:var(--secondary-text-color)}
      .hex-value{box-sizing:border-box;width:110px;padding:9px;font:14px ui-monospace,monospace;color:var(--primary-text-color);background:var(--secondary-background-color);border:1px solid var(--divider-color);border-radius:8px}
      .palette{display:flex;gap:6px;margin-top:10px;flex-wrap:wrap}
      .palette-swatch{position:relative;width:32px;height:32px;padding:6px;border:2px solid transparent;background:transparent;border-radius:50%}
      .palette-swatch::after{content:'';display:block;background:var(--swatch);border-radius:50%;width:100%;height:100%}
      .palette-swatch[aria-pressed=true]{border-color:var(--primary-text-color)}
      .colour-error,.error{color:var(--error-color,#ff806b);font-size:13px;margin-top:8px}
      .suggestions{display:block;margin-top:16px;font-size:13px;color:var(--secondary-text-color)}
      select{box-sizing:border-box;display:block;width:100%;font:inherit;color:var(--primary-text-color);background:var(--secondary-background-color);border:1px solid var(--divider-color);border-radius:8px;padding:10px;margin-top:6px}
    `));
    this.shadowRoot.append(element('p', '', 'Each slot can show any numeric sensor. Choose your readings below, or use the optional suggestions.'));
    this._error = element('p', 'error'); this._error.hidden = true; this._error.setAttribute('role', 'status');
    this.shadowRoot.append(this._error);
    this.shadowRoot.append(this._form(GENERAL_SCHEMA, this._config, value => {
      this._config = { ...this._config, ...value }; this._emit();
    }));
    this._config.slots.forEach((slot, index) => {
      const section = element('details'); section.dataset.slot = index; section.open = !slot.entity || openSlots.has(index);
      const summary = element('summary'); section.append(summary); this._summaries.push(summary);
      const form = this._form(SLOT_SCHEMA, slot, value => this._setSlot(index, { entity: value.entity ?? '', label: value.label ?? '' }));
      section.append(form);
      const suggestions = element('label', 'suggestions', 'Suggested readings (optional)');
      const select = element('select'); select.setAttribute('aria-label', `Suggested readings for slot ${index + 1}`);
      const placeholder = () => { const option = element('option', '', 'Choose a suggestion'); option.value = ''; return option; };
      select.append(placeholder());
      select.addEventListener('focus', () => {
        select.replaceChildren(placeholder());
        const candidates = sensorSuggestions(this._hass);
        for (const candidate of candidates) {
          const option = element('option', '', `${candidate.label} (${candidate.entity})`);
          option.value = candidate.entity; select.append(option);
        }
        if (!candidates.length) {
          const option = element('option', '', 'No suggestions found. Choose any sensor above.'); option.disabled = true; select.append(option);
        }
      });
      select.addEventListener('change', () => {
        if (!select.value) return;
        form.data = { ...form.data, entity: select.value };
        this._setSlot(index, { entity: select.value }); select.value = '';
      });
      suggestions.append(select); section.append(suggestions, this._colourControl(index));
      const options = element('details'); options.append(element('summary', '', 'Formatting and graph'));
      options.append(this._form(SLOT_OPTIONS_SCHEMA, slot, value => this._setSlot(index, { ...value, unit: value.unit ?? '' })));
      section.append(options);
      const actions = element('div', 'actions');
      for (const [label, disabled, action] of [
        ['Move up', index === 0, () => this._move(index, -1)],
        ['Move down', index === this._config.slots.length - 1, () => this._move(index, 1)],
        ['Remove slot', this._config.slots.length === 1, () => {
          this._config = { ...this._config, slots: this._config.slots.filter((_, i) => i !== index) };
          this._build(new Set()); this._emit();
        }],
      ]) {
        const button = element('button', '', label); button.type = 'button'; button.disabled = disabled;
        button.addEventListener('click', action); actions.append(button);
      }
      section.append(actions); this.shadowRoot.append(section);
    });
    const add = element('button', '', 'Add sensor slot'); add.type = 'button'; add.disabled = this._config.slots.length >= 6;
    add.addEventListener('click', () => {
      this._config = normaliseConfig({ ...this._config, slots: [...this._config.slots, {}] }); this._build(); this._emit();
    });
    this.shadowRoot.append(add);
    const advanced = element('details'); advanced.append(element('summary', '', 'Graph and updates'));
    advanced.append(this._form(ADVANCED_SCHEMA, this._config, value => {
      this._config = { ...this._config, ...value }; this._emit();
    }));
    this.shadowRoot.append(advanced);
    this._updateSummaries();
  }
  _move(index, offset) {
    const slots = [...this._config.slots];
    [slots[index], slots[index + offset]] = [slots[index + offset], slots[index]];
    this._config = { ...this._config, slots }; this._build(); this._emit();
  }
}

if (!customElements.get('what-sensor-graph-card')) customElements.define('what-sensor-graph-card', WhatSensorGraphCard);
if (!customElements.get('what-sensor-graph-editor')) customElements.define('what-sensor-graph-editor', WhatSensorGraphEditor);
window.customCards = window.customCards || [];
if (!window.customCards.some(card => card.type === 'what-sensor-graph-card')) window.customCards.push({
  type: 'what-sensor-graph-card', name: 'WHAT Sensor Panel', preview: true,
  description: 'Choose any numeric sensors, with optional CPU, load and memory suggestions, history and offline status.',
  documentationURL: 'https://github.com/Chuffnugget/what-integration/blob/main/documentation/SENSOR-GRAPH-CARD.md',
});
