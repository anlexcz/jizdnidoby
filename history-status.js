function routeGraph(line, direction) {
  const segments = D.segs.filter((segment) => segment.l === line && segment.d === direction);
  const names = new Map();
  const outgoing = new Map();
  const incoming = new Map();

  for (const segment of segments) {
    names.set(segment.a, segment.n?.[0] || segment.a);
    names.set(segment.b, segment.n?.[1] || segment.b);
    if (!outgoing.has(segment.a)) outgoing.set(segment.a, []);
    outgoing.get(segment.a).push(segment);
    incoming.set(segment.b, (incoming.get(segment.b) || 0) + 1);
    if (!incoming.has(segment.a)) incoming.set(segment.a, 0);
  }

  return { names, outgoing, incoming };
}

function orderedRouteStops(line, direction) {
  const graph = routeGraph(line, direction);
  const result = [];
  const seen = new Set();
  const roots = [...graph.names.keys()].filter((id) => (graph.incoming.get(id) || 0) === 0);
  const queue = roots.length ? [...roots] : [...graph.names.keys()].slice(0, 1);

  while (queue.length) {
    const id = queue.shift();
    if (seen.has(id)) continue;
    seen.add(id);
    result.push([id, graph.names.get(id) || id]);
    for (const segment of graph.outgoing.get(id) || []) {
      if (!seen.has(segment.b)) queue.push(segment.b);
    }
  }

  for (const [id, name] of graph.names) {
    if (!seen.has(id)) result.push([id, name]);
  }
  return result;
}

function reachableRouteStops(line, direction, from) {
  const graph = routeGraph(line, direction);
  const result = [];
  const seen = new Set([from]);
  const queue = [from];

  while (queue.length) {
    const id = queue.shift();
    for (const segment of graph.outgoing.get(id) || []) {
      if (seen.has(segment.b)) continue;
      seen.add(segment.b);
      result.push([segment.b, graph.names.get(segment.b) || segment.b]);
      queue.push(segment.b);
    }
  }
  return result;
}

function routeDirectionLabel(line, direction) {
  const ordered = orderedRouteStops(line, direction);
  const terminal = ordered.at(-1)?.[1];
  return terminal ? `${direction} · ${terminal}` : direction;
}

renderHistory = function renderHistoryWithRouteOrder(values = {}) {
  const line = values.line || allLines()[0] || '';
  const direction = values.dir || dirs(line)[0] || '';
  const stopList = orderedRouteStops(line, direction);
  const a = values.a && stopList.some(([id]) => id === values.a)
    ? values.a
    : stopList[0]?.[0] || '';
  const reachable = reachableRouteStops(line, direction, a);
  const b = values.b && reachable.some(([id]) => id === values.b)
    ? values.b
    : reachable[0]?.[0] || '';
  const path = a && b ? findPath(line, direction, a, b) : [];
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
      <div><label>Směr</label><select id="hDir">${dirs(line).map((item) => `<option value="${esc(item)}" ${item === direction ? 'selected' : ''}>${esc(routeDirectionLabel(line, item))}</option>`).join('')}</select></div>
      <div><label>Zastávka A</label><select id="hA">${stopList.map(([id, name]) => `<option value="${esc(id)}" ${id === a ? 'selected' : ''}>${esc(name)}</option>`).join('')}</select></div>
      <div><label>Zastávka B</label><select id="hB">${reachable.map(([id, name]) => `<option value="${esc(id)}" ${id === b ? 'selected' : ''}>${esc(name)}</option>`).join('')}</select></div>
      <div class="wide">
        <div class="filters"><button data-hday="w" class="${dtype === 'w' ? 'on' : ''}">Po–Pá</button><button data-hday="s" class="${dtype === 's' ? 'on' : ''}">So</button><button data-hday="n" class="${dtype === 'n' ? 'on' : ''}">Ne</button></div>
        <label>Hodina · <b>${hour}:00</b></label><input id="hHr" type="range" min="4" max="25" value="${hour}">
      </div>
    </div>
    ${!path.length
      ? `<div class="empty">Pro tuto zastávku už ve zvoleném směru není další dostupný úsek.</div>`
      : `<div class="historyResult"><div class="meta">${esc(names)} · ${path.length} mezizastávkových úseků</div>${current === null
        ? `<div class="empty">Pro zvolený typ dne a hodinu nemám úplnou aktuální jízdní dobu.</div>`
        : rows + (changes.length ? '' : `<div class="empty">V evidované historii zatím na tomto úseku není potvrzená změna.</div>`)}<div class="historyPath">Historie zachycuje změny od začátku evidence projektu, nikoli období před jejím spuštěním.</div></div>`}
  </div>`;
  bindHistory(line, direction, a, b);
  draw();
};

bindHistory = function bindHistoryWithReachability(line, direction, a, b) {
  if ($('#hLine')) $('#hLine').onchange = (event) => showHistory({ line: event.target.value });
  if ($('#hDir')) $('#hDir').onchange = (event) => showHistory({ line, dir: event.target.value });
  if ($('#hA')) $('#hA').onchange = (event) => showHistory({ line, dir: direction, a: event.target.value });
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
