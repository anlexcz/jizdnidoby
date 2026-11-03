const timetableHistory = {
  index: null,
  lines: new Map(),
};

async function ensureTimetableIndex() {
  if (timetableHistory.index) return timetableHistory.index;
  timetableHistory.index = await loadJson('data/web/timetable_index.json', null, false);
  return timetableHistory.index;
}

async function ensureTimetableLine(line) {
  if (timetableHistory.lines.has(line)) return timetableHistory.lines.get(line);
  const index = await ensureTimetableIndex();
  const entry = index?.lines?.[line];
  if (!entry?.file) return null;
  const payload = await loadJson(`data/web/${entry.file}`, null, false);
  if (payload) timetableHistory.lines.set(line, payload);
  return payload;
}

function timetableLines() {
  const lines = Object.keys(timetableHistory.index?.lines || {});
  return (lines.length ? lines : allLines()).sort((a, b) => a.localeCompare(b, 'cs', { numeric: true }));
}

function timetableDirections(payload) {
  return [...new Set(Object.values(payload?.dates || {}).flatMap((day) => Object.keys(day || {})))].sort();
}

function timetableDirectionLabel(payload, direction) {
  const dates = Object.keys(payload?.dates || {}).sort().reverse();
  for (const date of dates) {
    const snapshot = payload.dates?.[date]?.[direction];
    const terminal = snapshot?.stops?.at(-1)?.[1];
    if (terminal) return `${direction} · ${terminal}`;
  }
  return direction;
}

function routeSegmentKey(a, b) {
  return `${a}|${b}`;
}

function minuteValue(seconds) {
  if (seconds === null || seconds === undefined || seconds === '') return null;
  return Math.round(Number(seconds) / 60);
}

function commonValue(values) {
  const counts = new Map();
  for (const value of values) {
    if (value === null) continue;
    counts.set(value, (counts.get(value) || 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0] - b[0])[0]?.[0] ?? null;
}

function timetableBaselines(payload, direction) {
  const samples = new Map();
  for (const day of Object.values(payload?.dates || {})) {
    const snapshot = day?.[direction];
    if (!snapshot?.stops?.length) continue;
    const values = snapshot.hours?.[String(hour)] || [];
    for (let i = 0; i < snapshot.stops.length - 1; i += 1) {
      const a = snapshot.stops[i]?.[0];
      const b = snapshot.stops[i + 1]?.[0];
      const value = minuteValue(values[i]);
      if (!a || !b || value === null) continue;
      const key = routeSegmentKey(a, b);
      if (!samples.has(key)) samples.set(key, []);
      samples.get(key).push(value);
    }
  }
  return new Map([...samples].map(([key, values]) => [key, commonValue(values)]));
}

function routeStrip(snapshot, baselines) {
  if (!snapshot?.stops?.length) return '<div class="routeEmpty">Trasa není v tomto dni dostupná.</div>';
  const values = snapshot.hours?.[String(hour)] || [];
  let html = '<div class="routeStrip">';
  for (let i = 0; i < snapshot.stops.length; i += 1) {
    const [stopId, stopName] = snapshot.stops[i];
    html += `<div class="routeStop" title="${esc(stopId)}">${esc(stopName)}</div>`;
    if (i >= snapshot.stops.length - 1) continue;

    const nextId = snapshot.stops[i + 1]?.[0];
    const value = minuteValue(values[i]);
    const baseline = baselines.get(routeSegmentKey(stopId, nextId));
    const diff = value !== null && baseline !== null && baseline !== undefined ? value - baseline : 0;
    const diffText = diff > 0 ? `(+${diff})` : diff < 0 ? `(${diff})` : '';
    const cls = diff > 0 ? 'up' : diff < 0 ? 'down' : '';
    html += `<div class="routeTime ${cls}"><b>${value === null ? '–' : value}</b>${diffText ? `<span>${diffText}</span>` : ''}</div>`;
  }
  return `${html}</div>`;
}

function updateHistoryMap(line, direction, payload) {
  const dates = Object.keys(payload?.dates || {}).sort().reverse();
  const snapshot = dates.map((date) => payload.dates?.[date]?.[direction]).find((item) => item?.stops?.length);
  if (!snapshot) {
    historyPath = [];
    draw();
    return;
  }
  const path = [];
  for (let i = 0; i < snapshot.stops.length - 1; i += 1) {
    const a = snapshot.stops[i][0];
    const b = snapshot.stops[i + 1][0];
    const segment = segMap.get(key(line, direction, a, b));
    if (segment) path.push(segment);
  }
  historyPath = path;
  draw();
}

showHistory = async function showTimetableHistory(values = {}) {
  await ensureTimetableIndex();
  const lines = timetableLines();
  const line = values.line && lines.includes(values.line) ? values.line : lines[0] || '';
  if (line) await ensureLines([line]);
  const payload = line ? await ensureTimetableLine(line) : null;
  renderHistory({ line, dir: values.dir, payload });
};

renderHistory = function renderDailyTimetableHistory(values = {}) {
  const lines = timetableLines();
  const line = values.line || lines[0] || '';
  const payload = values.payload || timetableHistory.lines.get(line);

  if (!payload) {
    $('#panel').innerHTML = `<div class="history"><div class="historyGrid"><div><label>Linka</label><select id="hLine">${lines.map((item) => `<option ${item === line ? 'selected' : ''}>${esc(item)}</option>`).join('')}</select></div></div><div class="empty">Historický přehled jízdních řádů se právě vytváří. Po nejbližším úspěšném datovém běhu se tu objeví jednotlivé dny a celé trasy.</div></div>`;
    if ($('#hLine')) $('#hLine').onchange = (event) => showHistory({ line: event.target.value });
    return;
  }

  const directions = timetableDirections(payload);
  const direction = values.dir && directions.includes(values.dir) ? values.dir : directions[0] || '';
  const dates = Object.keys(payload.dates || {}).filter((date) => payload.dates?.[date]?.[direction]).sort().reverse();
  const baselines = timetableBaselines(payload, direction);
  const firstDate = dates.at(-1);
  const lastDate = dates[0];
  const coverage = dates.length
    ? `${dates.length} ${dates.length === 1 ? 'den' : dates.length < 5 ? 'dny' : 'dnů'} · ${fmtDate(firstDate)}–${fmtDate(lastDate)}`
    : 'Pro tento směr zatím nejsou archivované dny.';

  $('#panel').innerHTML = `<div class="history timetableHistory">
    <div class="historyGrid timetableControls">
      <div><label>Linka</label><select id="hLine">${lines.map((item) => `<option ${item === line ? 'selected' : ''}>${esc(item)}</option>`).join('')}</select></div>
      <div><label>Směr</label><select id="hDir">${directions.map((item) => `<option value="${esc(item)}" ${item === direction ? 'selected' : ''}>${esc(timetableDirectionLabel(payload, item))}</option>`).join('')}</select></div>
      <div class="wide">
        <label>Hodina · <b>${hour}:00</b></label>
        <input id="hHr" type="range" min="4" max="25" value="${hour}">
      </div>
    </div>
    <div class="historyCoverage">
      <strong>Dostupná historie</strong><span>${esc(coverage)}</span>
      <small>Každý řádek je konkrétní den. Číslo mezi zastávkami je jízdní doba v minutách; závorka ukazuje rozdíl proti nejčastější hodnotě daného úseku v evidované historii.</small>
    </div>
    <div class="timetableDays">
      ${dates.length ? dates.map((date) => `<section class="timetableDay">
        <div class="timetableDate"><strong>${fmtDate(date)}</strong><span>${new Date(`${date}T12:00:00`).toLocaleDateString('cs-CZ', { weekday: 'short' })}</span></div>
        <div class="timetableRoute">${routeStrip(payload.dates[date][direction], baselines)}</div>
      </section>`).join('') : '<div class="empty">Pro tento směr zatím nejsou archivované dny.</div>'}
    </div>
  </div>`;

  if ($('#hLine')) $('#hLine').onchange = (event) => showHistory({ line: event.target.value });
  if ($('#hDir')) $('#hDir').onchange = (event) => showHistory({ line, dir: event.target.value });
  if ($('#hHr')) $('#hHr').oninput = (event) => {
    hour = +event.target.value;
    renderHistory({ line, dir: direction, payload });
  };
  updateHistoryMap(line, direction, payload);
};

renderStatus = function renderDataHealth() {
  const status = D.status;
  if (!status) {
    $('#statusDot').className = 'dot bad';
    $('#statusText').textContent = 'Stav dat nelze ověřit';
    $('#statusDetail').innerHTML = 'Soubor se stavem dat není dostupný. Jízdní doby mohou být načtené, ale jejich čerstvost teď nelze potvrdit.';
    $('#statusLine').onclick = () => {
      $('#statusDetail').hidden = !$('#statusDetail').hidden;
    };
    return;
  }

  const week = status.week || {};
  const expected = week.expected || 0;
  const completed = week.ok || 0;
  const qaOk = status.qa === 'passed';
  const allRunsOk = expected > 0 && completed === expected;
  const feedDate = status.feed_updated || status.last_success;
  const feedTime = feedDate ? new Date(`${feedDate}T12:00:00`).getTime() : NaN;
  const ageDays = Number.isFinite(feedTime) ? Math.max(0, Math.floor((Date.now() - feedTime) / 86400000)) : null;
  const freshnessOk = ageDays !== null && ageDays <= 1;
  const level = qaOk && allRunsOk && freshnessOk ? 'ok' : qaOk && freshnessOk ? 'warn' : 'bad';
  const headline = level === 'ok' ? 'Data aktuální' : level === 'warn' ? 'Data s upozorněním' : 'Data vyžadují kontrolu';

  $('#statusDot').className = `dot ${level}`;
  $('#statusText').textContent = `${headline} · ${fmtDate(feedDate) || 'datum neznámé'}`;
  $('#statusDetail').innerHTML = `
    <b>Poslední úspěšné načtení:</b> ${fmtDate(status.last_success) || '—'}<br>
    <b>Aktualizace GTFS:</b> ${fmtDate(status.feed_updated) || '—'}<br>
    <b>QA:</b> ${qaOk ? 'OK' : esc(status.qa || 'neznámé')}<br>
    <b>Očekávané běhy:</b> ${completed}/${expected || '—'}${week.full_window === false ? ' od začátku sledování' : ' za posledních 7 dní'}
    <div class="days">${(week.days || []).map((day) => `<span class="day ${day.ok ? 'ok' : ''}" title="${esc(fmtDate(day.date))}: ${day.ok ? 'načteno' : 'chybí'}"></span>`).join('')}</div>
    ${week.missing?.length ? `<b>Chybí:</b> ${week.missing.map(fmtDate).join(', ')}` : 'Všechna očekávaná načtení proběhla.'}`;
  $('#statusLine').onclick = () => {
    $('#statusDetail').hidden = !$('#statusDetail').hidden;
  };
};

renderStatus();
