const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const esc = (value) => String(value ?? '').replace(/[&<>"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;',
}[char]));

const D = {
  segs: [],
  events: [],
  cur: { dates: {}, v: {} },
  status: null,
  segmentIndex: null,
};

let mode = 'changes';
let filter = 'all';
let selected = null;
let hour = 8;
let dtype = 'w';
let historyPath = [];
const loadedLines = new Set();
let legacyGeometryLoaded = false;

const map = L.map('map', {
  zoomControl: false,
  renderer: L.canvas({ tolerance: 8 }),
}).setView([50.08, 14.44], 12);
L.control.zoom({ position: 'bottomright' }).addTo(map);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '© OpenStreetMap',
}).addTo(map);

const layer = L.layerGroup().addTo(map);
const segMap = new Map();
const key = (line, direction, a, b) => `${line}|${direction}|${a}|${b}`;
const ll = (geometry) => geometry.map((point) => [point[1], point[0]]);

function secs(value) {
  const seconds = Math.round(+value || 0);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

function delta(value) {
  const amount = Math.abs(+value || 0);
  const rendered = amount >= 60 ? `${Math.abs(Math.round(amount / 60))} min` : `${Math.round(amount)} s`;
  return `${value > 0 ? '+' : '−'}${rendered}`;
}

function validity(value) {
  return ({
    weekdays: 'pracovní dny', weekend: 'víkend', saturday: 'sobota',
    sunday: 'neděle', daily: 'denně', dates: 'vybrané dny',
  })[value] || value || '';
}

const period = (event) => `${validity(event.v)} · ${event.s}:00–${event.e}:00`;
const fmtDate = (value) => value ? new Date(`${value}T12:00:00`).toLocaleDateString('cs-CZ') : '';

function norm(event) {
  const oldValue = event.o ?? event.old_s ?? 0;
  const newValue = event.w ?? event.new_s ?? 0;
  const diff = event.x ?? event.total_diff_s ?? event.diff_s ?? (+newValue - +oldValue);
  return {
    on: event.on ?? event.detected_on,
    l: String(event.l ?? event.line ?? ''),
    d: String(event.d ?? event.direction_id ?? ''),
    a: event.a ?? event.stop_a_id,
    b: event.b ?? event.stop_b_id,
    n: event.n || [event.stop_a_name || '', event.stop_b_name || ''],
    s: +(event.s ?? event.start_hour ?? 0),
    e: +(event.e ?? event.end_hour ?? 29),
    o: +oldValue,
    w: +newValue,
    x: +diff,
    v: event.v ?? event.validity,
    f: event.f ?? event.valid_from,
    u: event.u ?? event.valid_to,
    dates: event.dates || [],
    m: +(event.m ?? event.matched_trips ?? 0),
    p: +(event.p ?? event.supporting_trips ?? 0),
    q: event.q ?? event.confidence,
    segments: (event.segments || [{
      a: event.a ?? event.stop_a_id,
      an: event.stop_a_name,
      b: event.b ?? event.stop_b_id,
      bn: event.stop_b_name,
      old_s: oldValue,
      new_s: newValue,
      diff_s: diff,
    }]).map((segment) => ({
      ...segment,
      diff_s: +(segment.diff_s ?? ((+segment.new_s || 0) - (+segment.old_s || 0))) || 0,
    })),
  };
}

async function loadJson(path, fallback, required = false) {
  try {
    const response = await fetch(path, { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return await response.json();
  } catch (error) {
    if (required) throw new Error(`${path}: ${error.message}`);
    console.warn(`Nelze načíst ${path}`, error);
    return fallback;
  }
}

function addSegments(segments) {
  for (const raw of segments || []) {
    const segment = { ...raw };
    segment.l = String(segment.l);
    segment.d = String(segment.d);
    segment.k = segment.k || key(segment.l, segment.d, segment.a, segment.b);
    if (segMap.has(segment.k)) continue;
    segMap.set(segment.k, segment);
    D.segs.push(segment);
  }
}

function allLines() {
  const indexed = Object.keys(D.segmentIndex?.lines || {});
  const source = indexed.length ? indexed : [...new Set(D.segs.map((segment) => segment.l))];
  return source.sort((a, b) => a.localeCompare(b, 'cs', { numeric: true }));
}

async function ensureLines(lines) {
  const wanted = [...new Set((lines || []).filter(Boolean).map(String))];
  if (!wanted.length) return;

  if (!D.segmentIndex?.lines) {
    if (!legacyGeometryLoaded) {
      addSegments(await loadJson('data/web/segments.json', [], true));
      legacyGeometryLoaded = true;
    }
    wanted.forEach((line) => loadedLines.add(line));
    return;
  }

  const jobs = [];
  for (const line of wanted) {
    if (loadedLines.has(line)) continue;
    const entry = D.segmentIndex.lines[line];
    if (!entry?.file) {
      console.warn(`V segment_index.json chybí linka ${line}`);
      continue;
    }
    jobs.push(loadJson(`data/web/${entry.file}`, [], true).then((segments) => {
      addSegments(segments);
      loadedLines.add(line);
    }));
  }
  await Promise.all(jobs);
}

function filtered() {
  return D.events
    .filter((event) => filter === 'all' || (filter === 'up' ? event.x > 0 : event.x < 0))
    .sort((a, b) => (b.on || '').localeCompare(a.on || '') || Math.abs(b.x) - Math.abs(a.x));
}

function eventHtml(event, index) {
  return `<button class="event ${selected === index ? 'sel' : ''}" data-event="${index}">
    <span class="route">${esc(event.l)}</span>
    <span>
      <div class="names">${esc(event.n[0])} → ${esc(event.n[1])}</div>
      <div class="meta">${esc(period(event))}</div>
      <div class="time">${secs(event.o)} → ${secs(event.w)}${event.m ? ` · ${event.p}/${event.m} spojů` : ''}</div>
    </span>
    <span class="delta ${event.x > 0 ? 'up' : 'down'}">${delta(event.x)}</span>
  </button>`;
}

function renderChanges() {
  const events = filtered();
  $('#panel').innerHTML = `
    <div class="filters">
      <button data-filter="all" class="${filter === 'all' ? 'on' : ''}">Vše</button>
      <button data-filter="up" class="${filter === 'up' ? 'on' : ''}">Prodloužení</button>
      <button data-filter="down" class="${filter === 'down' ? 'on' : ''}">Zkrácení</button>
    </div>
    <p class="count">${events.length} změn v evidenci</p>
    ${events.length
      ? events.map((event) => eventHtml(event, D.events.indexOf(event))).join('')
      : `<div class="empty">Zatím tu nejsou žádné potvrzené změny.<br><br>
          <button class="back" data-go-history>Prohlédnout historii konkrétního úseku →</button></div>`}
  `;
  bind();
}

function detail(index) {
  const event = D.events[index];
  selected = index;
  $('#panel').innerHTML = `
    <button class="back" data-back>← Zpět na změny</button>
    <div class="detail">
      <div class="meta">Linka ${esc(event.l)} · ${esc(period(event))}</div>
      <h2>${esc(event.n[0])} → ${esc(event.n[1])}</h2>
      <div class="big ${event.x > 0 ? 'up' : 'down'}">${secs(event.o)} → ${secs(event.w)} &nbsp; ${delta(event.x)}</div>
      <div class="meta">Platnost ${esc(event.f || '')} – ${esc(event.u || '')}</div>
      ${event.m ? `<div class="evidence"><b>${event.p}/${event.m} spojů potvrzuje změnu</b><br>Shoda ${Math.round(+(event.q ?? event.p / event.m) * 100)} %.</div>` : ''}
      <div class="segments">${event.segments.map((segment) => `<div>${esc(segment.an || segment.a)} → ${esc(segment.bn || segment.b)}</div>`).join('')}</div>
    </div>`;
  bind();
  focus(event);
}

function renderCurrent() {
  const labels = { w: 'Po–Pá', s: 'So', n: 'Ne' };
  $('#panel').innerHTML = `<div class="current">
    <div class="filters">${Object.keys(labels).filter((item) => D.cur.dates?.[item]).map((item) => `<button data-day="${item}" class="${dtype === item ? 'on' : ''}">${labels[item]}</button>`).join('')}</div>
    <label>Hodina · <b>${hour}:00</b></label>
    <input id="hr" type="range" min="4" max="25" value="${hour}">
    <p class="count">Mapa ukazuje mezizastávkové úseky s jízdní dobou pro zvolený typ dne a hodinu.</p>
  </div>`;
  bind();
  draw();
}

function dirs(line) {
  return [...new Set(D.segs.filter((segment) => segment.l === line).map((segment) => segment.d))].sort();
}

function stops(line, direction) {
  const result = new Map();
  D.segs.filter((segment) => segment.l === line && segment.d === direction).forEach((segment) => {
    result.set(segment.a, segment.n?.[0] || segment.a);
    result.set(segment.b, segment.n?.[1] || segment.b);
  });
  return [...result].sort((a, b) => a[1].localeCompare(b[1], 'cs'));
}

function findPath(line, direction, a, b) {
  const segments = D.segs.filter((segment) => segment.l === line && segment.d === direction);
  const adjacency = new Map();
  segments.forEach((segment) => {
    if (!adjacency.has(segment.a)) adjacency.set(segment.a, []);
    adjacency.get(segment.a).push(segment);
  });
  const queue = [[a, []]];
  const seen = new Set([a]);
  while (queue.length) {
    const [node, path] = queue.shift();
    if (node === b) return path;
    for (const segment of adjacency.get(node) || []) {
      if (!seen.has(segment.b)) {
        seen.add(segment.b);
        queue.push([segment.b, [...path, segment]]);
      }
    }
  }
  return [];
}

function eventApplies(event) {
  if (hour < event.s || hour >= event.e) return false;
  if (event.v === 'weekdays' && dtype !== 'w') return false;
  if (event.v === 'weekend' && dtype === 'w') return false;
  if (event.v === 'saturday' && dtype !== 's') return false;
  if (event.v === 'sunday' && dtype !== 'n') return false;
  return true;
}

function pathCurrent(path) {
  let total = 0;
  for (const segment of path) {
    const value = (((D.cur.v || {})[segment.k] || {})[dtype] || [])[hour];
    if (value === undefined || value === null || +value <= 0) return null;
    total += +value;
  }
  return total;
}

function historyRows(path, line, direction) {
  const keys = new Set(path.map((segment) => segment.k));
  const byDate = new Map();
  D.events.filter((event) => event.l === line && event.d === direction && eventApplies(event)).forEach((event) => {
    let change = 0;
    event.segments.forEach((segment) => {
      if (keys.has(key(event.l, event.d, segment.a, segment.b))) change += +segment.diff_s || 0;
    });
    if (change) byDate.set(event.on, (byDate.get(event.on) || 0) + change);
  });
  return [...byDate].sort((a, b) => b[0].localeCompare(a[0]));
}

async function showHistory(values = {}) {
  const line = values.line || allLines()[0] || '';
  await ensureLines([line]);
  renderHistory({ ...values, line });
}

function renderHistory(values = {}) {
  const line = values.line || allLines()[0] || '';
  const direction = values.dir || dirs(line)[0] || '';
  const stopList = stops(line, direction);
  const a = values.a || stopList[0]?.[0] || '';
  const b = values.b || stopList[1]?.[0] || '';
  const path = findPath(line, direction, a, b);
  historyPath = path;
  const current = pathCurrent(path);
  const changes = historyRows(path, line, direction);
  let value = current;
  let rows = '';

  if (current !== null) {
    rows += `<div class="historyRow"><span>Aktuálně</span><strong>${secs(current)}</strong><span></span></div>`;
    for (const [detectedOn, change] of changes) {
      const before = value - change;
      rows += `<div class="historyRow"><span>${fmtDate(detectedOn)}</span><strong>${secs(before)} → ${secs(value)}</strong><span class="delta ${change > 0 ? 'up' : 'down'}">${delta(change)}</span></div>`;
      value = before;
    }
  }

  const names = path.length ? `${path[0].n?.[0] || a} → ${path[path.length - 1].n?.[1] || b}` : '';
  $('#panel').innerHTML = `<div class="history">
    <div class="historyGrid">
      <div><label>Linka</label><select id="hLine">${allLines().map((item) => `<option ${item === line ? 'selected' : ''}>${esc(item)}</option>`).join('')}</select></div>
      <div><label>Směr</label><select id="hDir">${dirs(line).map((item) => `<option ${item === direction ? 'selected' : ''}>${esc(item)}</option>`).join('')}</select></div>
      <div><label>Zastávka A</label><select id="hA">${stopList.map(([id, name]) => `<option value="${esc(id)}" ${id === a ? 'selected' : ''}>${esc(name)}</option>`).join('')}</select></div>
      <div><label>Zastávka B</label><select id="hB">${stopList.map(([id, name]) => `<option value="${esc(id)}" ${id === b ? 'selected' : ''}>${esc(name)}</option>`).join('')}</select></div>
      <div class="wide">
        <div class="filters"><button data-hday="w" class="${dtype === 'w' ? 'on' : ''}">Po–Pá</button><button data-hday="s" class="${dtype === 's' ? 'on' : ''}">So</button><button data-hday="n" class="${dtype === 'n' ? 'on' : ''}">Ne</button></div>
        <label>Hodina · <b>${hour}:00</b></label><input id="hHr" type="range" min="4" max="25" value="${hour}">
      </div>
    </div>
    ${!path.length
      ? `<div class="empty">Pro zvolenou dvojici se v tomto směru nepodařilo najít souvislý úsek.</div>`
      : `<div class="historyResult"><div class="meta">${esc(names)} · ${path.length} mezizastávkových úseků</div>${current === null
        ? `<div class="empty">Pro zvolený typ dne a hodinu nemám úplnou aktuální jízdní dobu.</div>`
        : rows + (changes.length ? '' : `<div class="empty">V evidované historii zatím na tomto úseku není potvrzená změna.</div>`)}<div class="historyPath">Historie zachycuje změny od začátku evidence projektu, nikoli období před jejím spuštěním.</div></div>`}
  </div>`;
  bindHistory(line, direction, a, b);
  draw();
}

function bindHistory(line, direction, a, b) {
  if ($('#hLine')) $('#hLine').onchange = (event) => showHistory({ line: event.target.value });
  if ($('#hDir')) $('#hDir').onchange = (event) => showHistory({ line, dir: event.target.value });
  if ($('#hA')) $('#hA').onchange = (event) => showHistory({ line, dir: direction, a: event.target.value, b });
  if ($('#hB')) $('#hB').onchange = (event) => showHistory({ line, dir: direction, a, b: event.target.value });
  $$('[data-hday]').forEach((button) => {
    button.onclick = () => {
      dtype = button.dataset.hday;
      showHistory({ line, dir: direction, a, b });
    };
  });
  if ($('#hHr')) $('#hHr').oninput = (event) => {
    hour = +event.target.value;
    showHistory({ line, dir: direction, a, b });
  };
}

function bind() {
  $$('[data-filter]').forEach((button) => button.onclick = () => {
    filter = button.dataset.filter;
    renderChanges();
  });
  $$('[data-event]').forEach((button) => button.onclick = () => detail(+button.dataset.event));
  if ($('[data-back]')) $('[data-back]').onclick = () => {
    selected = null;
    renderChanges();
    draw();
  };
  if ($('[data-go-history]')) $('[data-go-history]').onclick = () => setView('history');
  $$('[data-day]').forEach((button) => button.onclick = () => {
    dtype = button.dataset.day;
    renderCurrent();
  });
  if ($('#hr')) $('#hr').oninput = (event) => {
    hour = +event.target.value;
    renderCurrent();
  };
}

function focus(event) {
  const segments = event.segments.map((segment) => segMap.get(key(event.l, event.d, segment.a, segment.b))).filter(Boolean);
  if (segments.length) map.fitBounds(L.latLngBounds(segments.flatMap((segment) => ll(segment.g))).pad(0.7), { maxZoom: 16 });
  draw();
}

function draw() {
  layer.clearLayers();
  if (mode === 'current') {
    for (const segment of D.segs) {
      const value = (((D.cur.v || {})[segment.k] || {})[dtype] || [])[hour] || 0;
      L.polyline(ll(segment.g), { color: value ? '#35c9c3' : '#4d555c', weight: value ? 4 : 2, opacity: value ? 0.8 : 0.18 }).addTo(layer);
    }
    $('#legend').innerHTML = '<b style="background:var(--cyan)"></b>provoz <b style="background:#4d555c"></b>bez hodnoty';
    return;
  }
  if (mode === 'history') {
    const keys = new Set(historyPath.map((segment) => segment.k));
    for (const segment of D.segs) {
      L.polyline(ll(segment.g), { color: keys.has(segment.k) ? '#ff3636' : '#51585e', weight: keys.has(segment.k) ? 6 : 1.5, opacity: keys.has(segment.k) ? 1 : 0.14 }).addTo(layer);
    }
    if (historyPath.length) map.fitBounds(L.latLngBounds(historyPath.flatMap((segment) => ll(segment.g))).pad(0.5), { maxZoom: 15 });
    $('#legend').innerHTML = '<b style="background:var(--red)"></b>vybraný úsek';
    return;
  }

  const active = new Map();
  D.events.forEach((event, index) => event.segments.forEach((segment) => active.set(key(event.l, event.d, segment.a, segment.b), { event, index })));
  for (const segment of D.segs) {
    const hit = active.get(segment.k);
    const isSelected = hit && selected === hit.index;
    L.polyline(ll(segment.g), {
      color: hit ? (hit.event.x > 0 ? '#ff3636' : '#35c9c3') : '#51585e',
      weight: isSelected ? 7 : hit ? 4 : 1.5,
      opacity: isSelected ? 1 : hit ? 0.78 : 0.18,
    }).on('click', () => hit && detail(hit.index)).addTo(layer);
  }
  $('#legend').innerHTML = '<b style="background:var(--red)"></b>delší <b style="background:var(--cyan)"></b>kratší';
}

async function setView(view) {
  mode = view;
  selected = null;
  try {
    if (view === 'changes') {
      await ensureLines(D.events.map((event) => event.l));
      renderChanges();
    } else if (view === 'current') {
      await ensureLines(allLines());
      renderCurrent();
    } else {
      await showHistory();
    }
    $$('[data-view]').forEach((button) => button.classList.toggle('on', button.dataset.view === view));
    $$('[data-mode]').forEach((button) => button.classList.toggle('on', button.dataset.mode === view));
    draw();
  } catch (error) {
    renderFatal(error);
  }
}

$$('[data-view]').forEach((button) => button.onclick = () => setView(button.dataset.view));
$$('[data-mode]').forEach((button) => button.onclick = () => setView(button.dataset.mode));
$$('[data-mobile]').forEach((button) => button.onclick = async () => {
  $$('[data-mobile]').forEach((item) => item.classList.toggle('on', item === button));
  const view = button.dataset.mobile;
  $('#app').classList.toggle('showmap', view === 'map');
  if (view !== 'map') await setView(view);
  draw();
  setTimeout(() => map.invalidateSize(), 80);
});

function renderStatus() {
  const status = D.status;
  if (!status) {
    $('#statusText').textContent = 'Stav načítání není k dispozici';
    return;
  }
  const week = status.week || {};
  const ok = week.ok === week.expected && status.qa === 'passed';
  $('#statusDot').className = `dot ${ok ? 'ok' : week.ok >= 5 ? 'warn' : 'bad'}`;
  $('#statusText').textContent = `Data ${fmtDate(status.feed_updated || status.last_success)} · načtení ${week.ok || 0}/${week.expected || 7} za 7 dní`;
  $('#statusDetail').innerHTML = `Poslední úspěšné načtení: <b>${fmtDate(status.last_success) || '—'}</b><br>
    QA: <b>${esc(status.qa || '—')}</b>
    <div class="days">${(week.days || []).map((day) => `<span class="day ${day.ok ? 'ok' : ''}" title="${esc(day.date)}"></span>`).join('')}</div>
    ${week.missing?.length ? `Chybí: ${week.missing.map(fmtDate).join(', ')}` : 'Všechna očekávaná načtení za posledních 7 dní proběhla.'}`;
  $('#statusLine').onclick = () => {
    $('#statusDetail').hidden = !$('#statusDetail').hidden;
  };
}

function renderFatal(error) {
  console.error(error);
  $('#panel').innerHTML = `<div class="fatal"><strong>Webová data se nepodařilo načíst.</strong><p>${esc(error?.message || error)}</p><button class="back" id="retryLoad">Zkusit znovu</button></div>`;
  $('#statusDot').className = 'dot bad';
  $('#statusText').textContent = 'Chyba načtení webových dat';
  if ($('#retryLoad')) $('#retryLoad').onclick = () => location.reload();
}

async function load() {
  $('#panel').innerHTML = '<div class="loading">Načítám data…</div>';
  try {
    const [events, regimes, current, status, segmentIndex] = await Promise.all([
      loadJson('data/web/events.json', null),
      loadJson('data/web/regimes.json', []),
      loadJson('data/web/current.json', { dates: {}, v: {} }),
      loadJson('data/web/status.json', null),
      loadJson('data/web/segment_index.json', null),
    ]);
    D.cur = current;
    D.status = status;
    D.segmentIndex = segmentIndex;
    D.events = (events || regimes || []).map(norm);
    renderStatus();
    await ensureLines(D.events.map((event) => event.l));
    renderChanges();
    draw();
  } catch (error) {
    renderFatal(error);
  }
}

load();
