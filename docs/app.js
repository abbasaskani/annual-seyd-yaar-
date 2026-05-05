const state = {
  meta: null,
  speciesMeta: new Map(),
  selectedSpecies: null,
  availableTimeIds: [],
  filteredTimeIds: [],
  selectedTimeId: null,
};

const qs = (id) => document.getElementById(id);

async function fetchJson(path) {
  const res = await fetch(path, { cache: 'no-store' });
  if (!res.ok) throw new Error(`Failed to load ${path}`);
  return res.json();
}

function fillInput(id, value) {
  const el = qs(id);
  if (el) el.value = value ?? '';
}

function metric(label, value) {
  const div = document.createElement('div');
  div.className = 'metric';
  div.innerHTML = `<div class="note">${label}</div><div style="font-size:22px;font-weight:700;margin-top:6px;">${value}</div>`;
  return div;
}

function applyTemporalSpec(spec) {
  fillInput('modeBox', spec?.mode || 'operational');
  fillInput('tzBox', spec?.timezone_label || spec?.timezone || '—');
  fillInput('hourBox', spec?.snapshot_local_hour != null ? `${spec.snapshot_local_hour}:00` : '—');
  fillInput('startYearBox', spec?.start_year ?? '—');
  fillInput('endYearBox', spec?.end_year ?? '—');
  fillInput('seasonStartBox', spec?.season_start_mmdd ?? '—');
  fillInput('seasonEndBox', spec?.season_end_mmdd ?? '—');
  fillInput('stepUnitBox', spec?.step_unit ?? '—');
  fillInput('stepValueBox', spec?.step_value ?? '—');
  fillInput('partialStepBox', spec?.partial_last_step_allowed ? 'allowed' : '—');
}

function renderTimezoneChips() {
  const holder = qs('timezoneChips');
  holder.innerHTML = '';
  const current = state.meta?.temporal_spec?.timezone;
  const options = state.meta?.timezone_options || [];
  options.forEach((opt) => {
    const div = document.createElement('div');
    div.className = 'chip' + (opt.value === current ? ' ok' : '');
    div.textContent = opt.label;
    holder.appendChild(div);
  });
}

function renderSummary() {
  const metrics = qs('summaryMetrics');
  metrics.innerHTML = '';
  metrics.appendChild(metric('Run id', state.meta?.run_id || '—'));
  metrics.appendChild(metric('Variant', state.meta?.variant || '—'));
  metrics.appendChild(metric('Species', String((state.meta?.species || []).length)));
  metrics.appendChild(metric('Snapshots', String((state.meta?.available_time_ids || []).length)));
}

function renderTimeSelectors() {
  const ids = state.availableTimeIds;
  for (const id of ['fromSelect', 'toSelect', 'focusSelect']) {
    const sel = qs(id);
    sel.innerHTML = ids.map((tid) => `<option value="${tid}">${tid}</option>`).join('');
  }
  if (ids.length) {
    qs('fromSelect').value = ids[0];
    qs('toSelect').value = ids[ids.length - 1];
    qs('focusSelect').value = ids[Math.max(ids.length - 1, 0)];
  }
  qs('fromSelect').addEventListener('change', applyTimeFilter);
  qs('toSelect').addEventListener('change', applyTimeFilter);
  qs('focusSelect').addEventListener('change', () => selectTime(qs('focusSelect').value));
}

function applyTimeFilter() {
  const from = qs('fromSelect').value;
  const to = qs('toSelect').value;
  state.filteredTimeIds = state.availableTimeIds.filter((tid) => tid >= from && tid <= to);
  renderTimeList();
  if (!state.filteredTimeIds.includes(state.selectedTimeId)) {
    selectTime(state.filteredTimeIds[0] || null);
  }
}

function renderTimeList() {
  const holder = qs('timeList');
  holder.innerHTML = '';
  state.filteredTimeIds.forEach((tid) => {
    const btn = document.createElement('button');
    btn.className = tid === state.selectedTimeId ? 'active' : '';
    btn.textContent = tid;
    btn.onclick = () => {
      qs('focusSelect').value = tid;
      selectTime(tid);
    };
    holder.appendChild(btn);
  });
}

function colorize(ctx, arr, ops = false) {
  const n = Math.sqrt(arr.length) | 0;
  const img = ctx.createImageData(n, n);
  for (let i = 0; i < arr.length; i++) {
    const v = arr[i];
    const idx = i * 4;
    if (ops) {
      const on = v > 0;
      img.data[idx] = on ? 99 : 14;
      img.data[idx + 1] = on ? 230 : 20;
      img.data[idx + 2] = on ? 143 : 38;
      img.data[idx + 3] = 255;
    } else {
      const c = Math.max(0, Math.min(255, Math.round(v * 255)));
      img.data[idx] = c;
      img.data[idx + 1] = Math.round(190 - c * 0.2);
      img.data[idx + 2] = Math.round(255 - c * 0.5);
      img.data[idx + 3] = 255;
    }
  }
  const off = document.createElement('canvas');
  off.width = n;
  off.height = n;
  const offCtx = off.getContext('2d');
  offCtx.putImageData(img, 0, 0);
  ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(off, 0, 0, ctx.canvas.width, ctx.canvas.height);
}

async function loadRaster(path, asOps = false) {
  const res = await fetch(path, { cache: 'no-store' });
  const buf = await res.arrayBuffer();
  return asOps ? new Uint8Array(buf) : new Float32Array(buf);
}

async function selectTime(timeId) {
  state.selectedTimeId = timeId;
  renderTimeList();
  if (!timeId) return;
  fillInput('selectedTimeId', timeId);
  const speciesMeta = state.speciesMeta.get(state.selectedSpecies);
  const timeEntries = new Map((speciesMeta.time_entries || []).map((x) => [x.time_id, x.time_utc]));
  fillInput('selectedTimeIso', timeEntries.get(timeId) || '');
  const tpl = speciesMeta.paths.per_time;
  const base = (rel) => `./latest/${rel.replace('{time_id}', timeId)}`;
  const [phab, pcatch, ops] = await Promise.all([
    loadRaster(base(tpl.phab)),
    loadRaster(base(tpl.pcatch)),
    loadRaster(base(tpl.ops), true),
  ]);
  colorize(qs('phabCanvas').getContext('2d'), phab, false);
  colorize(qs('pcatchCanvas').getContext('2d'), pcatch, false);
  colorize(qs('opsCanvas').getContext('2d'), ops, true);
}

async function onSpeciesChange(species) {
  state.selectedSpecies = species;
  const meta = await fetchJson(`./latest/${state.meta.run_path}/variants/${state.meta.variant}/species/${species}/meta.json`);
  state.speciesMeta.set(species, meta);
  state.availableTimeIds = meta.time_ids || [];
  renderTimeSelectors();
  applyTimeFilter();
  await selectTime(qs('focusSelect').value);
}

async function init() {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('./sw.js').catch(() => {});
  }
  state.meta = await fetchJson('./latest/meta.json');
  const timezoneOptions = await fetchJson('./seasonal_timezones.json').catch(() => []);
  state.meta.timezone_options = timezoneOptions;
  applyTemporalSpec(state.meta.temporal_spec || {});
  renderTimezoneChips();
  renderSummary();

  const speciesSelect = qs('speciesSelect');
  speciesSelect.innerHTML = (state.meta.species || []).map((s) => `<option value="${s}">${s}</option>`).join('');
  speciesSelect.addEventListener('change', (e) => onSpeciesChange(e.target.value));
  if ((state.meta.species || []).length) {
    await onSpeciesChange(state.meta.species[0]);
  }
}

init().catch((err) => {
  document.body.innerHTML = `<div class="wrap"><div class="card"><h2>Load error</h2><p class="note">${String(err)}</p></div></div>`;
  console.error(err);
});
