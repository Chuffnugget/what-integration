/* Headless browser coverage; synthetic HA state/history only, no live server. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { chromium } = require('playwright');

test('WHAT Sensor Panel', async t => {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.WHAT_TEST_BROWSER ? { executablePath: process.env.WHAT_TEST_BROWSER } : {}) });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 700, height: 900 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const source = fs.readFileSync(path.join(__dirname, '../custom_components/what/frontend/sensor-graph-card.js'), 'utf8');
  await page.route('https://fixture.test/**', route => route.fulfill({
    contentType: route.request().url().includes('.js') ? 'text/javascript' : 'text/html',
    body: route.request().url().includes('.js') ? source : `<!doctype html><html><body style="margin:20px;background:#111318;color:#eee;--primary-text-color:#eee;--secondary-text-color:#a0a7b3;--divider-color:#363b43;--secondary-background-color:#242830;font-family:Arial">
    <what-sensor-graph-card style="display:block;max-width:550px;margin:auto"></what-sensor-graph-card>
    <script type="module" src="/card.js"></script></body></html>`,
  }));
  await page.goto('https://fixture.test/');
  await page.waitForFunction(() => customElements.get('what-sensor-graph-card'));
  await page.clock.install();
  await page.evaluate(() => {
    window.card = document.querySelector('what-sensor-graph-card');
    window.calls = [];
    const now = Date.now();
    const state = (value, unit) => ({ state: String(value), last_updated: new Date(now).toISOString(), attributes: { unit_of_measurement: unit } });
    window.hass = {
      states: { 'sensor.temp': state(59.3284, '°C'), 'sensor.load': state(24.2, '%'), 'sensor.ram': state(42.1, '%') },
      locale: { language: 'en' },
      entities: Object.fromEntries(['sensor.temp', 'sensor.load', 'sensor.ram'].map(id => [id, { device_id: 'module' }])),
      devices: { module: { name: 'WHAT · RAR-D1 · HWiNFO', via_device_id: 'pc' }, pc: { name: 'WHAT · RAR-D1' } },
      callWS: async request => {
        calls.push(request);
        return Object.fromEntries(request.entity_ids.map((id, j) => [id, Array.from({ length: 61 }, (_, i) => ({
          s: String(40 + j * 7 + Math.sin(i / 3) * (5 + j * 2)), lu: (now - 3600000 + i * 59000) / 1000,
        }))]));
      },
    };
    window.config = { slots: [{ entity: 'sensor.temp' }, { entity: 'sensor.load' }, { entity: 'sensor.ram' }] };
    card.setConfig(config); card.hass = hass;
  });
  await page.clock.runFor(1100);

  await t.test('picker, numeric values, real compressed history and desktop layout', async () => {
    assert.deepEqual(await page.locator('.value').allTextContents(), ['59.3', '24.2', '42.1']);
    assert.equal(await page.locator('h2').textContent(), 'Sensors');
    assert.equal(await page.evaluate(() => calls.length), 1);
    const request = await page.evaluate(() => calls[0]);
    assert.equal(request.type, 'history/history_during_period');
    assert.deepEqual(request.entity_ids, ['sensor.temp', 'sensor.load', 'sensor.ram']);
    assert.equal(request.minimal_response, true);
    assert.equal(request.significant_changes_only, false);
    assert.equal(await page.evaluate(() => card._hostname()), 'RAR-D1');
    const lines = await page.locator('.trace').evaluateAll(nodes => nodes.map(n => n.getAttribute('d')));
    assert.ok(lines.every(line => line.includes('L') && !/NaN|Infinity/.test(line)));
    assert.equal(await page.evaluate(() => window.customCards.filter(c => c.type === 'what-sensor-graph-card').length), 1);
    await page.evaluate(() => import('/card.js?second'));
    assert.equal(await page.evaluate(() => window.customCards.filter(c => c.type === 'what-sensor-graph-card').length), 1);
    const output = process.env.WHAT_CARD_ARTIFACTS || os.tmpdir();
    await page.locator('what-sensor-graph-card').screenshot({ path: path.join(output, 'sensor-graph-desktop.png') });
  });

  await t.test('mobile layout and keyboard more-info action', async () => {
    await page.setViewportSize({ width: 320, height: 800 });
    assert.ok(await page.evaluate(() => {
      const root = card.shadowRoot;
      return [...root.querySelectorAll('.tile')].every(el => el.scrollWidth <= el.clientWidth)
        && root.querySelector('ha-card').getBoundingClientRect().width <= 280;
    }));
    await page.evaluate(() => card.addEventListener('hass-more-info', e => { window.moreInfo = e.detail.entityId; }));
    await page.locator('.tile').first().focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.evaluate(() => moreInfo), 'sensor.temp');
    await page.evaluate(() => card.shadowRoot.activeElement?.blur());
    await page.locator('what-sensor-graph-card').screenshot({ path: path.join(process.env.WHAT_CARD_ARTIFACTS || os.tmpdir(), 'sensor-graph-mobile.png') });
    await page.setViewportSize({ width: 700, height: 900 });
  });

  await t.test('unavailable slots and hostname offline recover without losing history', async () => {
    await page.evaluate(() => {
      hass.states['sensor.temp'] = { ...hass.states['sensor.temp'], state: 'unavailable', last_updated: new Date().toISOString() };
      card.hass = hass;
    });
    await page.clock.runFor(1100);
    assert.equal(await page.locator('.value').first().textContent(), 'Unavailable');
    assert.equal(await page.locator('.graph').evaluate(el => el.classList.contains('offline')), false);
    await page.evaluate(() => {
      hass.states['sensor.load'] = { state: 'NaN', attributes: {} };
      hass.states['sensor.ram'] = { state: 'unknown', attributes: {} };
      card.hass = hass;
    });
    await page.clock.runFor(1100);
    assert.equal(await page.locator('.message strong').textContent(), 'RAR-D1 is offline');
    assert.equal(await page.locator('.message').isVisible(), true);
    assert.ok((await page.locator('.trace').first().getAttribute('d')).includes('L'));
    assert.ok(!(await page.locator('ha-card').textContent()).includes('NaN'));
    await page.locator('what-sensor-graph-card').screenshot({ path: path.join(process.env.WHAT_CARD_ARTIFACTS || os.tmpdir(), 'sensor-graph-offline.png') });
    await page.evaluate(() => {
      hass.states['sensor.temp'] = { state: '61.14', last_updated: new Date().toISOString(), attributes: { unit_of_measurement: '°C' } };
      card.hass = hass;
    });
    await page.clock.runFor(1100);
    assert.equal(await page.locator('.message').isVisible(), false);
    assert.equal(await page.locator('.value').first().textContent(), '61.1');
    assert.equal(await page.evaluate(() => calls.length), 1, 'State updates must not fetch history');
  });

  await t.test('idle/unrelated state changes, incremental history and hidden-card suspension', async () => {
    const paint = await page.evaluate(() => {
      for (let i = 0; i < 100; i++) card.hass = { ...hass, states: { ...hass.states, 'sensor.unrelated': { state: String(i) } } };
      return card._paintedAt;
    });
    await page.clock.runFor(5000);
    assert.equal(await page.evaluate(() => card._paintedAt), paint);
    await page.clock.runFor(60000);
    assert.equal(await page.evaluate(() => calls.length), 2);
    const [first, second] = await page.evaluate(() => calls);
    assert.equal(Date.parse(second.start_time), Date.parse(first.end_time) - 1000);
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, value: true });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await page.clock.runFor(180000);
    assert.equal(await page.evaluate(() => calls.length), 2);
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, value: false });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await page.clock.runFor(1100);
    assert.equal(await page.evaluate(() => calls.length), 3);
    await page.evaluate(() => { card.style.marginTop = '2000px'; });
    await page.waitForFunction(() => !card._inView);
    await page.clock.runFor(180000);
    assert.equal(await page.evaluate(() => calls.length), 3);
    await page.evaluate(() => { card.style.marginTop = ''; });
    await page.waitForFunction(() => card._inView);
    await page.clock.runFor(1100);
    assert.equal(await page.evaluate(() => calls.length), 4);
  });

  await t.test('failed history keeps current values, retry and stale-response protection', async () => {
    await page.evaluate(() => {
      window.originalCall = hass.callWS;
      hass.callWS = async () => { throw new Error('recorder unavailable'); };
      card.hass = hass; card.setConfig({ ...config, hostname: 'Office PC', hours_to_show: 2 });
    });
    await page.clock.runFor(1100);
    assert.equal(await page.locator('.history-note').isVisible(), true);
    assert.equal(await page.locator('.value').first().textContent(), '61.1');
    await page.evaluate(() => { hass.callWS = originalCall; });
    await page.clock.runFor(60000);
    assert.equal(await page.locator('.history-note').isVisible(), false);
    await page.evaluate(() => {
      window.pendingHistory = [];
      hass.callWS = request => new Promise(resolve => pendingHistory.push({ request, resolve }));
      card.setConfig({ ...config, title: 'Old request' });
      card.setConfig({ ...config, title: 'New request', slots: [{ entity: 'sensor.load' }] });
      pendingHistory[0].resolve({ 'sensor.temp': [{ s: '999', lu: Date.now() / 1000 }] });
    });
    await page.clock.runFor(1100);
    assert.equal(await page.evaluate(() => card._series.has('sensor.temp')), false);
    await page.evaluate(() => {
      card.remove();
      pendingHistory.at(-1).resolve({ 'sensor.load': [{ s: '999', lu: Date.now() / 1000 }] });
    });
    await page.clock.runFor(180000);
    assert.ok(!(await page.evaluate(() => card._series.get('sensor.load'))).some(p => p.v === 999));
    await page.evaluate(() => {
      hass.callWS = originalCall;
      document.body.prepend(card); card.setConfig(config); card.hass = hass;
    });
    await page.clock.runFor(1100);
    assert.equal(await page.locator('h2').textContent(), 'Sensors');
  });

  await t.test('cards initially outside the viewport make no history requests', async () => {
    await page.evaluate(() => {
      window.offscreenCalls = 0;
      window.offscreen = document.createElement('what-sensor-graph-card');
      offscreen.style.cssText = 'display:block;position:absolute;top:100000px;width:400px';
      offscreen.setConfig(config);
      offscreen.hass = { ...hass, callWS: async () => { offscreenCalls++; return {}; } };
      document.body.append(offscreen);
    });
    await page.clock.runFor(1500);
    assert.equal(await page.evaluate(() => offscreenCalls), 0);
    await page.evaluate(() => offscreen.remove());
  });

  await t.test('a stalled history request times out and resumes on the next refresh', async () => {
    await page.evaluate(() => {
      hass.callWS = () => new Promise(() => {});
      card.hass = hass; card.setConfig({ ...config, title: 'Timeout test', hours_to_show: 2 });
    });
    await page.clock.runFor(31000);
    assert.equal(await page.locator('.history-note').isVisible(), true);
    assert.equal(await page.locator('.value').first().textContent(), '61.1');
    await page.evaluate(() => { hass.callWS = originalCall; });
    await page.clock.runFor(31000);
    assert.equal(await page.locator('.history-note').isVisible(), false);
  });

  await t.test('visual editor selects sensors, colours, order and options without stale resets', async () => {
    await page.evaluate(() => {
      // Native ha-form lives in HA. This fixture tests our selector schema and
      // value-changed/config-changed contract without replacing HA's selectors.
      customElements.define('ha-form', class extends HTMLElement {});
      window.editor = card.constructor.getConfigElement();
      editor.setConfig({ ...config, grid_options: { columns: 6 } }); editor.hass = hass;
      window.edits = [];
      editor.addEventListener('config-changed', event => { edits.push(event.detail.config); editor.setConfig(event.detail.config); });
      document.body.append(editor);
      window.forms = [...editor.shadowRoot.querySelectorAll('ha-form')];
      window.edit = (form, patch) => form.dispatchEvent(new CustomEvent('value-changed', { detail: { value: { ...form.data, ...patch } } }));
      edit(forms[1], { entity: 'sensor.ram', label: 'MEMORY' });
      edit(forms[2], { fill: false, decimals: 0 });
      edit(forms[0], { title: 'Workstation', hostname: 'Office PC' });
      edit(forms.at(-1), { hours_to_show: 2, history_interval: 120, scale: 'shared', show_legend: true });
    });
    await page.evaluate(() => { editor.shadowRoot.querySelector('details[data-slot]').open = true; });
    const hex = page.locator('.hex-value').first();
    await hex.fill('#ff0064'); await hex.press('Enter');
    const latest = await page.evaluate(() => edits.at(-1));
    assert.equal(latest.slots[0].entity, 'sensor.ram');
    assert.equal(latest.slots[0].label, 'MEMORY');
    assert.equal(latest.slots[0].colour, '#ff0064');
    assert.equal(latest.slots[0].fill, false);
    assert.equal(latest.title, 'Workstation');
    assert.equal(latest.hours_to_show, 2);
    assert.equal(latest.hostname, 'Office PC');
    assert.deepEqual(latest.grid_options, { columns: 6 });
    await hex.fill('wrong'); await hex.press('Enter');
    assert.equal(await hex.getAttribute('aria-invalid'), 'true');
    assert.equal(await page.evaluate(() => edits.at(-1).slots[0].colour), '#ff0064');
    await hex.press('Escape');
    await page.locator('.colour-field').first().getByRole('button', { name: 'Mint', exact: true }).click();
    assert.equal(await page.evaluate(() => edits.at(-1).slots[0].colour), '#83d5ad');
    await page.locator('.colour-field').first().getByRole('button', { name: 'Reset colour for slot 1' }).click();
    assert.equal(await page.evaluate(() => edits.at(-1).slots[0].colour), '#ff806b');
    await page.locator('.colour-swatch').first().evaluate(input => {
      input.value = '#aabbcc'; input.dispatchEvent(new Event('input')); input.dispatchEvent(new Event('change'));
    });
    assert.equal(await hex.inputValue(), '#AABBCC');
    assert.equal(await page.evaluate(() => edits.at(-1).slots[0].colour), '#aabbcc');
    await page.locator('.colour-field').first().screenshot({ path: path.join(process.env.WHAT_CARD_ARTIFACTS || os.tmpdir(), 'sensor-panel-colours.png') });
    await page.setViewportSize({ width: 320, height: 900 });
    assert.ok(await page.locator('.colour-field').first().evaluate(field => field.scrollWidth <= field.clientWidth));
    await page.locator('.colour-field').first().screenshot({ path: path.join(process.env.WHAT_CARD_ARTIFACTS || os.tmpdir(), 'sensor-panel-colours-mobile.png') });
    await page.setViewportSize({ width: 700, height: 900 });
    assert.equal(await page.evaluate(() => editor.shadowRoot.querySelector('ha-form') === forms[0]), true, 'Echoed config must preserve active form nodes');
    await page.evaluate(() => {
      const first = editor.shadowRoot.querySelector('details'); first.open = true;
    });
    await page.locator('what-sensor-graph-editor button', { hasText: 'Move down' }).first().click();
    assert.equal(await page.evaluate(() => edits.at(-1).slots[1].label), 'MEMORY');
    await page.locator('what-sensor-graph-editor button', { hasText: 'Add sensor slot' }).click();
    assert.equal(await page.evaluate(() => edits.at(-1).slots.length), 4);
    assert.equal(await page.evaluate(() => edits.at(-1).slots[3].label), '');
    await page.evaluate(() => {
      window.beforeRemove = edits.at(-1);
      const last = editor.shadowRoot.querySelector('details[data-slot="3"]'); last.open = true;
    });
    await page.locator('details[data-slot="3"]').getByRole('button', { name: 'Remove slot' }).click();
    assert.equal(await page.evaluate(() => beforeRemove.slots.length), 4, 'Removing must not mutate the previously emitted config');
    assert.equal(await page.evaluate(() => edits.at(-1).slots.length), 3);
    await page.evaluate(() => {
      card.setConfig({ slots: Array.from({ length: 6 }, () => ({ entity: 'sensor.temp' })) });
    });
    assert.equal(await page.locator('what-sensor-graph-card .tile').count(), 6);
    assert.ok(await page.evaluate(() => card.getGridOptions().min_rows >= 6));
    await page.evaluate(() => { editor.remove(); });
  });

  await t.test('generic slots, optional suggestions and automatic names', async () => {
    const result = await page.evaluate(async () => {
      const { normaliseConfig, sensorSuggestions } = await import('/card.js');
      hass.states['sensor.cpu_temperature'] = { state: '55', attributes: { friendly_name: 'Office CPU temperature', unit_of_measurement: '°C' } };
      hass.states['sensor.pump'] = { state: '1234', attributes: { friendly_name: 'Pump speed', unit_of_measurement: 'rpm' } };
      window.blank = normaliseConfig({});
      editor.setConfig(blank); editor.hass = hass; document.body.append(editor);
      card.setConfig({ slots: [{ entity: 'sensor.pump' }] }); card.hass = hass;
      return { slots: blank.slots, suggestions: sensorSuggestions(hass), name: window.customCards.find(c => c.type === 'what-sensor-graph-card').name };
    });
    assert.ok(result.slots.every(s => s.entity === '' && s.label === ''));
    assert.equal(result.name, 'WHAT Sensor Panel');
    assert.ok(result.suggestions.some(s => s.entity === 'sensor.cpu_temperature'));
    assert.equal(await page.locator('what-sensor-graph-card .label').textContent(), 'Pump speed');
    const suggestions = page.getByRole('combobox', { name: 'Suggested readings for slot 1' });
    await suggestions.focus(); await suggestions.selectOption('sensor.cpu_temperature');
    assert.equal(await page.evaluate(() => edits.at(-1).slots[0].entity), 'sensor.cpu_temperature');
    assert.equal(await page.evaluate(() => edits.at(-1).slots[0].label), '');
    assert.equal(await page.evaluate(() => edits.at(-1).slots[1].entity), '');
    await page.evaluate(() => {
      hass.states['sensor.pump'] = { ...hass.states['sensor.pump'], attributes: { ...hass.states['sensor.pump'].attributes, friendly_name: 'Water pump' } };
      card.hass = hass; editor.remove();
    });
    await page.clock.runFor(1100);
    assert.equal(await page.locator('what-sensor-graph-card .label').textContent(), 'Water pump');
  });

  await t.test('appearance edits keep history and do not refetch; tiles-only skips recorder', async () => {
    await page.evaluate(() => { card.setConfig(config); });
    await page.clock.runFor(1100);
    const before = await page.evaluate(() => calls.length);
    const count = await page.evaluate(() => card._series.get('sensor.temp').length);
    await page.evaluate(() => {
      for (let i = 0; i < 20; i++) card.setConfig({ ...config, title: 'Title ' + i, slots: config.slots.map(s => ({ ...s, colour: '#aabbcc' })) });
    });
    await page.clock.runFor(1100);
    assert.equal(await page.evaluate(() => calls.length), before);
    assert.ok(await page.evaluate(() => card._series.get('sensor.temp').length >= 20));
    assert.ok(count >= 20);
    await page.evaluate(() => { card.setConfig({ slots: [{ entity: 'sensor.temp', graph: false }] }); });
    await page.clock.runFor(61000);
    assert.equal(await page.evaluate(() => calls.length), before);
    assert.equal(await page.locator('.graph').isVisible(), false);
    assert.equal(await page.evaluate(() => card._series.size), 0);
  });

  await t.test('extreme finite numbers cannot generate invalid SVG coordinates', async () => {
    await page.evaluate(() => {
      card.setConfig({ slots: [{ entity: 'sensor.temp' }] });
      card._series.set('sensor.temp', [{ t: Date.now() - 5000, v: -1e308 }, { t: Date.now() - 3000, v: 1e308 }]);
      card._paint();
    });
    const d = await page.locator('.trace').first().getAttribute('d');
    assert.ok(d.includes('L') && !/NaN|Infinity/.test(d));
  });

  await t.test('last seen uses the matching PC timestamp, ages without requests and survives browser reload', async () => {
    await page.evaluate(() => {
      card.remove();
      window.offlineConfig = { height: 60, slots: [{ entity: 'sensor.temp', graph: false }] };
      window.offlineHass = {
        ...hass,
        states: {
          'sensor.temp': { state: 'unavailable', last_updated: new Date().toISOString(), attributes: {} },
          'sensor.renamed_reception': { state: new Date(Date.now() - 300000).toISOString(), attributes: { device_class: 'timestamp' } },
          'sensor.other_pc_seen': { state: new Date().toISOString(), attributes: { device_class: 'timestamp' } },
        },
        entities: { ...hass.entities,
          'sensor.renamed_reception': { platform: 'what', translation_key: 'app_last_seen', device_id: 'pc' },
          'sensor.other_pc_seen': { platform: 'what', translation_key: 'app_last_seen', device_id: 'other' },
        },
        callWS: async () => { throw new Error('Tiles-only last seen must not request history or registry data'); },
      };
      card = document.createElement('what-sensor-graph-card');
      card.setConfig(offlineConfig); card.hass = offlineHass; document.body.prepend(card);
    });
    await page.waitForFunction(() => card._inView);
    await page.clock.runFor(1100);
    assert.equal(await page.locator('.last-seen').textContent(), 'Last seen: 5 minutes ago');
    assert.match(await page.locator('.last-seen').getAttribute('title'), /^Last communication: /);
    const painted = await page.evaluate(() => card._paintedAt);
    await page.clock.runFor(60000);
    assert.equal(await page.locator('.last-seen').textContent(), 'Last seen: 6 minutes ago');
    assert.equal(await page.evaluate(() => card._paintedAt), painted);
    await page.setViewportSize({ width: 320, height: 800 });
    assert.ok(await page.locator('.message').evaluate(el => el.scrollHeight <= el.clientHeight));
    await page.locator('what-sensor-graph-card').screenshot({ path: path.join(process.env.WHAT_CARD_ARTIFACTS || os.tmpdir(), 'sensor-panel-last-seen.png') });
    await page.evaluate(() => { card.style.marginTop = '3000px'; });
    await page.waitForFunction(() => !card._inView);
    assert.equal(await page.evaluate(() => card._ageTimer), null);
    await page.clock.runFor(60000);
    assert.equal(await page.locator('.last-seen').textContent(), 'Last seen: 6 minutes ago');
    await page.evaluate(() => { card.style.marginTop = ''; });
    await page.waitForFunction(() => card._inView);
    await page.clock.runFor(1100);
    assert.equal(await page.locator('.last-seen').textContent(), 'Last seen: 7 minutes ago');
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, value: true });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    assert.equal(await page.evaluate(() => card._ageTimer), null);
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, value: false });
      document.dispatchEvent(new Event('visibilitychange'));
      // A new card while the PC is already offline must show the stored value,
      // not the time this browser/card started observing it.
      card.remove(); card = document.createElement('what-sensor-graph-card');
      card.setConfig(offlineConfig); card.hass = offlineHass; document.body.prepend(card);
    });
    await page.waitForFunction(() => card._inView);
    await page.clock.runFor(1100);
    assert.equal(await page.locator('.last-seen').textContent(), 'Last seen: 7 minutes ago');
    await page.evaluate(() => {
      offlineHass.states['sensor.renamed_reception'].state = 'unknown'; card.hass = offlineHass;
    });
    await page.clock.runFor(1100);
    assert.equal(await page.locator('.last-seen').textContent(), 'Last seen: unknown');
    await page.evaluate(() => {
      card.setConfig({ ...offlineConfig, last_seen_entity: 'sensor.other_pc_seen' });
    });
    assert.match(await page.locator('.last-seen').textContent(), /Last seen: \d+ minutes ago/);
    await page.evaluate(() => {
      card.setConfig({ slots: [{ entity: 'sensor.temp', graph: false }, { entity: 'sensor.other', graph: false }] });
      offlineHass.entities = { ...offlineHass.entities, 'sensor.other': { device_id: 'other' } };
      offlineHass.devices = { ...offlineHass.devices, other: { name: 'WHAT · Other PC' } };
      card.hass = offlineHass;
    });
    await page.clock.runFor(1100);
    assert.equal(await page.locator('.last-seen').textContent(), 'Last seen: unknown', 'Never pick one of several PCs');
    await page.evaluate(() => {
      card.setConfig(offlineConfig);
      offlineHass.states['sensor.temp'] = { state: '52', attributes: {} }; card.hass = offlineHass;
    });
    await page.clock.runFor(1100);
    assert.equal(await page.locator('.message').isVisible(), false);
    assert.equal(await page.evaluate(() => card._ageTimer), null);
    const onlinePainted = await page.evaluate(() => card._paintedAt);
    await page.evaluate(() => {
      offlineHass.states['sensor.renamed_reception'].state = new Date().toISOString(); card.hass = offlineHass;
    });
    await page.clock.runFor(1100);
    assert.equal(await page.evaluate(() => card._paintedAt), onlinePainted, 'Online reception timestamps do not repaint graphs');
    await page.setViewportSize({ width: 700, height: 900 });
    await page.evaluate(() => { card.setConfig(config); card.hass = hass; });
  });

  await t.test('last seen rejects invalid timestamps and formats longer absences', async () => {
    const result = await page.evaluate(async () => {
      const { lastSeenTimestamp, lastSeenAge, normaliseConfig } = await import('/card.js');
      const now = Date.now();
      const bad = ['unknown', 'unavailable', 'NaN', '', '1234', new Date(now + 3600000).toISOString(), null].map(s => lastSeenTimestamp(s, now));
      const formatted = [1000, 120000, 7200000, 259200000].map(age => lastSeenAge(now - age, now));
      let rejected = false;
      try { normaliseConfig({ last_seen_entity: 'light.wrong' }); } catch (_) { rejected = true; }
      return { bad, formatted, rejected };
    });
    assert.deepEqual(result.bad, Array(7).fill(null));
    assert.deepEqual(result.formatted, ['less than a minute ago', '2 minutes ago', '2 hours ago', '3 days ago']);
    assert.equal(result.rejected, true);
  });

  await t.test('bounded samples, unavailable gaps, validation and literal text', async () => {
    const result = await page.evaluate(async () => {
      const { compactPoints, numericState, normaliseConfig } = await import('/card.js');
      const points = Array.from({ length: 36001 }, (_, i) => ({ t: i * 100, v: i === 500 ? 99 : Math.sin(i) }));
      points[1000].v = null;
      const compact = compactPoints(points, 0, 3600000, 120000);
      const bad = [NaN, Infinity, '', 'unavailable', 'unknown', null, true, 'NaN'].map(numericState);
      const rejects = [{ slots: [] }, { hours_to_show: 100 }, { slots: [{ colour: 'url(https://bad.test)' }] }, { slots: [{ entity: 'light.test' }] }].map(config => {
        try { normaliseConfig(config); return false; } catch (_) { return true; }
      });
      card.setConfig({ title: '<img src=x onerror=alert(1)>', slots: [{ label: '<script>bad</script>', entity: 'sensor.temp' }] });
      return { count: compact.length, peak: compact.some(p => p.v === 99), gap: compact.some(p => p.v === null), bad, rejects };
    });
    assert.ok(result.count <= 155); assert.ok(result.peak); assert.ok(result.gap);
    assert.deepEqual(result.bad, Array(8).fill(null)); assert.ok(result.rejects.every(Boolean));
    assert.equal(await page.locator('h2').textContent(), '<img src=x onerror=alert(1)>');
    assert.equal(await page.locator('ha-card img').count(), 0);
    assert.deepEqual(errors, []);
  });
});
