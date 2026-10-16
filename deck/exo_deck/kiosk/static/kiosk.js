const PANELS = ['talk', 'agents', 'approvals', 'transactions', 'wallets', 'cre', 'body', 'sensors', 'media', 'projects'];
const NAMES = { talk: 'Talk', agents: 'Agents', approvals: 'Approvals', transactions: 'Transactions',
  wallets: 'Wallets', cre: 'CRE', body: 'Body', sensors: 'Sensors', media: 'Media', projects: 'Projects' };
const $ = (id) => document.getElementById(id);
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const encPath = (rel) => String(rel).split('/').map(encodeURIComponent).join('/');
export const ago = (ts) => { const s = Math.max(0, Date.now() / 1000 - ts); return s < 90 ? `${s | 0}s` : s < 5400 ? `${(s / 60) | 0}m` : s < 172800 ? `${(s / 3600) | 0}h` : `${(s / 86400) | 0}d`; };

const COLSTAT = { queued: ['triage', 'todo', 'blocked'], ready: ['ready'], running: ['running'], done: ['done'] };

export const RENDER = {
  talk: (j) => `<h1>Talk to Hermes</h1>` + (j.turns.length ? j.turns.map((t) =>
    `<div class="turn ${esc(t.role)}"><span class="who">${t.role === 'you' ? 'You' : t.role === 'hermes' ? 'Hermes' : '·'}</span>${esc(t.text)}</div>`).join('')
    : `<p class="muted">Hold Talk and speak.</p>`),
  agents: (j) => !j.online ? `<h1>Agents</h1><p class="bad big">Hermes offline</p>` :
    `<div class="hd"><h1>Agents</h1><p class="muted">${(() => { const k = Object.values(j.cols).flat().filter((t) => t.parent).length;
      return k ? `<span class="accent">▌</span>${k} delegated by hermes` : ''; })()}</p></div><div class="cols">${['queued', 'ready', 'running', 'done'].map((c) =>
      `<div><p class="${c === 'running' ? 'warn' : c === 'done' ? 'ok' : 'muted'}"><b>${c.toUpperCase()} ${COLSTAT[c].reduce((n, k) => n + (j.counts[k] || 0), 0)}</b></p><ul class="rows tight">${(j.cols[c] || []).map((t) =>
        `<li class="${t.parent ? 'child' : ''}"><span class="clamp"><b>${esc(t.assignee || '-')}</b> <span class="muted">${t.created_at ? ago(t.created_at) : ''}</span></span><span class="clamp2">${esc(t.title)}</span></li>`).join('') || '<li class="muted">none</li>'}</ul></div>`).join('')}</div>`
    + ((j.feed || []).length ? `<ul class="rows dense feed">${j.feed.map((m) =>
      `<li><span class="clamp"><b class="accent">${esc(m.from)} → ${esc(m.to)}</b> ${esc(m.text)}</span><span class="muted tag">${ago(m.ts)}</span></li>`).join('')}</ul>` : ''),
  wallets: (j) => j.loading ? `<h1>Wallets</h1><p class="muted big">Loading balances…</p>` : `<h1>Wallets</h1><ul class="rows tight">${j.wallets.map((w) =>
    `<li><span class="clamp"><b>${esc(w.label)}</b> <span class="muted">${esc(w.chain)} · ${esc(w.address_short)}${w.stale ? ' · stale' : ''}</span><br>${w.tokens.map((t) => `${esc(t.amount)} ${esc(t.symbol)}`).join(' · ') || '<span class="muted">no tokens</span>'}${w.tokens_more ? ` <span class="muted">+${esc(w.tokens_more)}</span>` : ''}</span>
     <span class="tag big">${esc(w.native)}</span></li>`).join('') || '<li class="muted">No wallets configured</li>'}</ul>`
    + (j.more ? `<p class="warn tight">+${esc(j.more)} more — say "more"</p>` : '')
    + (j.errors.length ? `<p class="bad tight clamp">${j.errors.map(esc).join(' · ')}</p>` : '') + `<p class="muted tight">updated ${esc(j.age_s)}s ago</p>`,
  transactions: (j) => {
    const cls = { executed: 'ok', refused: 'bad', failed: 'bad', waiting_key: 'warn', simulated: 'muted', proposed: 'muted' };
    const label = (k) => esc(String(k).replace('_', ' '));
    return `<div class="hd"><h1>Transactions</h1><p class="muted clamp">${Object.entries(j.counts).map(([k, v]) => `${label(k)} ${esc(v)}`).join(' · ') || 'none yet'}${j.more ? ` · +${esc(j.more)} more` : ''}</p></div>
      <ul class="rows dense">${j.rows.map((t) => `<li><span class="clamp">${esc(t.summary)}<br><span class="muted">${esc(t.chain)} · ${t.created_at ? ago(t.created_at) : '–'}${t.reason ? ' · ' + esc(t.reason) : ''}</span></span>
      <span class="tag ${cls[t.status] || 'muted'}">${label(t.status)}</span></li>`).join('') || '<li class="muted">No transactions yet</li>'}</ul>`;
  },
  cre: (j) => {
    const hc = (h) => (h.status === 'deployed' ? 'ok' : h.status === 'simulated' ? 'warn' : 'muted');
    const short = { simulated: 'sim', planned: 'plan', deployed: 'live' };
    return `<div class="hd"><h1>CRE</h1><p class="chips">${j.handlers.map((h) =>
      `<span class="chip ${hc(h)}">${esc(h.handler)} · ${esc(short[h.status] || h.status)}</span>`).join(' ')}</p></div>`
      + (j.calls_error ? `<p class="bad">${esc(j.calls_error)}</p>` : `<ul class="rows dense">${j.calls.map((c) =>
        `<li><span class="clamp"><b>${esc(c.handler)}</b> ${esc(c.reason)}<br><span class="muted">${c.created_at ? ago(c.created_at) : '–'} · ${c.latency_ms == null ? '?' : esc(Math.round(c.latency_ms))} ms</span></span>
        <span class="tag ${c.verdict === 'approve' ? 'ok' : c.verdict === 'refuse' ? 'bad' : 'warn'}">${esc(c.verdict)}</span></li>`).join('') || '<li class="muted">No calls yet</li>'}</ul>`
        + (j.more ? `<p class="warn tight">more calls, say "more"</p>` : ''));
  },
  approvals: (j) => {
    // The first row is ALWAYS the item the key approves (pinned on every page); "more" pages the rest below it.
    // Headline = the Guardian's explanation (what the key signs); the agent's own summary is the muted second line.
    const L = j.labels || {};
    const row = (t) => `<li class="${t.key ? 'key' : ''}">${t.key ? `<span class="keymark">${esc(L.key)}</span>` : ''}<span class="txt">
      <span class="${t.key ? 'clamp2' : 'clamp'}">${esc(t.explanation || '(no Guardian explanation)')}</span>
      <span class="clamp muted">${esc(L.agent_says)}: ${esc(t.summary)}</span></span>
      <span class="tag ${t.risk === 'low' ? 'ok' : t.risk === 'high' ? 'bad' : 'warn'}">${esc(t.risk || '?')}${t.auto_eligible === true ? ' · auto' : ''}</span></li>`;
    const sent = j.approved.slice(0, Math.max(0, Math.min(3, 5 - j.pending.length - (j.pending_more ? 1 : 0))));   // pending rows are two lines: 5 row budget
    return `<h1>Approvals · <span class="${j.mode === 'auto' ? 'warn' : 'ok'}">${j.mode === 'auto' ? 'AUTO (low-risk only)' : 'MANUAL'}</span></h1>`
      + (j.auto_request ? `<p class="warn big">Press the approve key to turn on auto-approve</p>` : '')
      + `<p class="muted tight">${esc(j.pending_total)} pending · the key approves the top one · say "auto approve on/off"</p>`
      + `<ul class="rows tight appr">${j.pending.map(row).join('') || '<li class="muted">Nothing waiting</li>'}</ul>`
      + (j.pending_more ? `<p class="warn tight">+${esc(j.pending_more)} more — say "more"</p>` : '')
      + (sent.length ? `<ul class="rows tight">${sent.map((t) => `<li><span class="clamp">${esc(t.summary)}</span><span class="ok">sent</span></li>`).join('')}</ul>` : '');
  },
  projects: (j) => {
    // Read-only. Bar: ▓ shipped (live/done) · ▒ testing+building+designing · ░ backlog; the counts are in the words too.
    const STAGE = { idea: 'BACKLOG', designing: 'DESIGN', building: 'BUILD', testing: 'TEST', live: 'LIVE', parked: 'PARKED', done: 'DONE' };
    const bar = (c) => {
      const total = Object.entries(c || {}).filter(([k]) => k !== 'parked').reduce((a, [, n]) => a + n, 0);
      if (!total) return '';
      const n = (x) => Math.round((x / total) * 10);
      const done = n((c.live || 0) + (c.done || 0)), work = n((c.testing || 0) + (c.building || 0) + (c.designing || 0));
      return '▓'.repeat(done) + '▒'.repeat(Math.min(work, 10 - done)) + '░'.repeat(Math.max(0, 10 - done - work));
    };
    const age = j.fetched_at ? Math.round((Date.now() / 1000 - j.fetched_at) / 60) : null;
    const t = j.totals.features || {};
    const sub = `${t.building || 0} building · ${t.testing || 0} testing · ${t.idea || 0} backlog · ${esc(j.needs.length)} need you`
      + (j.stale ? ` · <span class="warn">cached ${age ?? '?'} min ago</span>` : '');
    const head = (title) => `<div class="hd"><h1>${title}</h1><p class="muted clamp">${sub}</p></div>`;
    const item = (i) => `<li><span class="clamp"><b>${esc(i.feature ? `${i.project} / ${i.title}` : i.title)}</b> ${esc(i.detail)}</span></li>`;
    if (j.view === 'needs') return head('Needs you') + `<ul class="rows tight">${j.needs.map(item).join('') || '<li class="muted">Nothing waiting on you</li>'}</ul>`
      + `<p class="muted tight">say "more" for gaps</p>`;
    if (j.view === 'attention') return head('Attention') + `<ul class="rows tight">${j.attention.map(item).join('') || '<li class="muted">No failing, overdue or stale work</li>'}</ul>`
      + `<p class="muted tight">goals with no work: ${esc((j.gaps.goals_uncovered || []).join(', ') || 'none')} · say "more" for projects</p>`;
    return head('Projects') + `<ul class="rows tight">${j.projects.map((p) => `<li><span class="clamp"><b>${esc(p.title)}</b> <span class="muted">${esc(p.next || '')}</span></span>`
      + `<span class="tag">${STAGE[p.stage] || esc(p.stage)} ${bar(p.counts)}${p.needs_owner ? ' <span class="warn">⚑</span>' : ''}</span></li>`).join('') || '<li class="muted">No projects</li>'}</ul>`
      + `<p class="muted tight">⚑ needs you · say "more" for the needs-you list</p>`;
  },
  body: (j) => {
    if (!j.last) return `<h1>Body</h1><p class="bad">${esc(j.sync_error || 'No ring data')}</p>`;
    const l = j.last, v = (x, u = '') => x == null ? '–' : `${esc(Math.round(x * 10) / 10)}${u}`;
    const stages = j.stages || [], hr = j.sleep_hr || [], trend = j.trend || [], srv = j.server;   // demo data has only last + trend
    const h = (m) => m == null ? '–' : `${(m / 60) | 0}:${String(m % 60).padStart(2, '0')}`;
    const clock = (ts) => new Date(ts * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
    const night = l.bedtime_start ? `${esc(l.day)} · ${clock(l.bedtime_start)} → ${clock(l.bedtime_end)}` : `${esc(l.day)}${l.sleep_score == null ? ' · no sleep' : ''}`;
    return `<div class="hd"><h1>Body</h1><p class="muted clamp">night of ${night}</p></div>
      <div class="body2"><div>
        <ul class="rows tight"><li><span>Sleep score</span><span class="big">${v(l.sleep_score)}</span></li>
        <li><span>Sleep·deep·REM</span><span>${h(l.total_sleep_min)} · ${h(l.deep_min)} · ${h(l.rem_min)}</span></li>
        <li><span>HRV · resting HR</span><span>${v(l.sleep_hrv ?? l.hrv_avg, ' ms')} · ${v(l.sleep_rhr, ' bpm')}</span></li>
        <li><span>Recovery · temp</span><span>${v(l.recovery_index)} · ${l.temp_deviation_c == null ? '–' : (l.temp_deviation_c > 0 ? '+' : '') + l.temp_deviation_c.toFixed(1) + '°C'}</span></li>
        <li><span>Efficiency · SpO₂</span><span>${v(l.sleep_efficiency_pct, '%')} · ${v(l.spo2_pct, '%')}</span></li></ul>
        <p class="muted tight">Sleep score, last ${trend.length} days</p>${scoreBars(trend, l.day)}
      </div><div>${hypnogram(stages, l.bedtime_start, l.bedtime_end)}${hr.length > 1 ? '<p class="muted tight">Heart rate in bed, bpm</p>' : ''}${sleepHr(hr, l.bedtime_start, l.bedtime_end)}</div></div>
      <p class="muted tight clamp">${j.nights_total ? `night ${j.night_index + 1} of ${j.nights_total} · say "more" for the one before · ` : ''}synced ${j.synced_at ? ago(j.synced_at) + ' ago' : 'never'}${srv
        ? ` · <span class="${srv.uploaded === srv.total ? 'ok' : 'warn'}">${srv.uploaded}/${srv.total} days on server</span>` : ''}${j.sync_error ? ' · <span class="bad">' + esc(j.sync_error) + '</span>' : ''}</p>`;
  },
  sensors: (j, s) => {
    const d = j.deck, c = j.clip, x = (n, u = '') => n == null ? '–' : `${esc(n)}${u}`;
    const clip = c.online ? (c.recording ? 'RECORDING' : 'online') : 'offline';
    return `<h1>Sensors</h1><ul class="rows tight">
      <li><span>Deck</span><span class="big">${x(d.cpu_c, '°C')} · load ${x(d.load1)}</span></li>
      <li><span>Disk free · uptime</span><span class="big">${x(d.disk_free_gb, ' GB')} · ${x(d.uptime_h, ' h')}</span></li>
      <li><span>Camera clip</span><span class="${c.online ? (c.recording ? 'warn' : 'ok') : 'bad'} big">${clip}${c.online ? ` · SD ${c.sd ? 'yes' : 'no'} · ${x(c.rssi, ' dBm')}` : ''}</span></li>
      <li><span>Hermes</span><span class="${s && s.hermes ? 'ok' : 'bad'} big">${s && s.hermes ? 'reachable' : 'offline'}</span></li></ul>
      ${j.sensors.length ? `<ul class="rows tight tiles">${j.sensors.slice(0, 6).map((t) => `<li><span>${esc(t.name)}</span><span class="big">${esc(t.value)}${t.unit ? ' ' + esc(t.unit) : ''}</span></li>`).join('')}</ul>`
        : `<p class="muted">No wearable sensors reporting.</p>`}`;
  },
  media: (j) => {
    const tag = { both: '<span class="ok">synced</span>', sd: '<span class="warn">SD only</span>', cloud: '<span class="muted">cloud only</span>' };
    const glyph = { photo: '▣', audio: '♪', video: '▶' };
    if (j.open) {
      const f = `/api/media/file/${encPath(j.open.rel)}`;
      const body = j.open.where === 'cloud' ? '<p class="muted big">Stored in the cloud only</p>'
        : j.open.kind === 'photo' ? `<img src="${f}" alt="">` : j.open.kind === 'video' ? `<video src="${f}" poster="/api/media/thumb/${encPath(j.open.rel)}" autoplay playsinline></video>` : `<div class="glyph big">♪ Playing</div><audio src="${f}" autoplay></audio>`;
      return `<h1 class="clamp">${j.open.n} · ${esc(j.open.rel.split('/').pop())}</h1><p class="muted tight">${tag[j.open.where]} · say "close"</p><div class="full">${body}</div>`;
    }
    const tile = (i) => {
      const img = i.kind !== 'audio' && i.where !== 'cloud' ? `<img src="/api/media/thumb/${encPath(i.rel)}" alt="" onerror="this.remove()">` : '';
      return `<figure><div class="thumb"><span class="glyph">${i.where === 'cloud' ? '☁' : glyph[i.kind] || ''}</span>${img}<b class="num">${esc(i.n)}</b></div>
        <figcaption>${i.mtime ? ago(i.mtime) : '–'} · ${tag[i.where]}</figcaption></figure>`;
    };
    const more = (j.total > (j.page + 1) * 9) ? ' · "more"' : '';
    return `<h1>Media · ${esc(j.total)} files</h1><p class="muted tight clamp">${esc(j.sd_only)} SD only · ${esc(j.cloud_only)} cloud only · synced ${j.synced_at ? ago(j.synced_at) + ' ago' : 'never'} · "open 3"${more}</p>
      ${j.items.length ? `<div class="grid9">${j.items.map(tile).join('')}</div>` : '<p class="muted">No photos, memos or videos yet.</p>'}`;
  },
};

// ── body panel charts: inline SVG, viewBox ≈ on-screen px so 30 px text stays 30 px ──
const STAGES = [['awake', 'Awake'], ['rem_sleep', 'REM'], ['light_sleep', 'Light'], ['deep_sleep', 'Deep']];
const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
function hourTicks(x, a, b, y) {
  const out = [], step = (b - a) > 6 * 3600 ? 7200 : 3600;
  for (let t = Math.ceil(a / step) * step; t <= b; t += step) out.push(`<text x="${x(t)}" y="${y}" text-anchor="middle">${hhmm(t)}</text>`);
  return out.join('');
}
export function hypnogram(stages, a, b) {
  if (!stages || !stages.length || !a) return '<p class="muted">No sleep stages for this night</p>';
  const W = 1000, L = 120, R = 40, rowH = 62, x = (t) => (L + (t - a) / (b - a) * (W - L - R)).toFixed(1);
  const rows = STAGES.map(([k, n], i) => `<text x="0" y="${i * rowH + rowH / 2 + 10}">${n}</text>`
    + `<line x1="${L}" x2="${W - R}" y1="${(i + 1) * rowH}" y2="${(i + 1) * rowH}" class="grid"/>`).join('');
  const bars = stages.map((s) => {
    const i = STAGES.findIndex(([k]) => k === s.stage);
    if (i < 0) return '';
    const w = Math.max(2, x(s.end) - x(s.start) - 2);
    return `<rect x="${x(s.start)}" y="${i * rowH + 6}" width="${w.toFixed(1)}" height="${rowH - 12}" rx="4" class="st-${s.stage}"/>`;
  }).join('');
  return `<svg class="chart" viewBox="0 0 ${W} ${4 * rowH + 44}">${rows}${bars}${hourTicks(x, a, b, 4 * rowH + 38)}</svg>`;
}
export function sleepHr(hr, a, b) {
  if (!hr || hr.length < 2 || !a) return '';
  const W = 1000, H = 190, L = 120, R = 40, T = 14, B = 46;
  const vals = hr.map((p) => p[1]), lo = Math.floor(Math.min(...vals) / 5) * 5, hi = Math.ceil(Math.max(...vals) / 5) * 5;
  const x = (t) => (L + (t - a) / (b - a) * (W - L - R)).toFixed(1), y = (v) => (T + (hi - v) / ((hi - lo) || 1) * (H - T - B)).toFixed(1);
  const path = hr.map((p, i) => `${i ? 'L' : 'M'}${x(p[0])},${y(p[1])}`).join('');
  return `<svg class="chart" viewBox="0 0 ${W} ${H}">`
    + [lo, hi].map((v) => `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="grid"/><text x="0" y="${+y(v) + 10}">${v}</text>`).join('')
    + `<path d="${path}" class="line"/>${hourTicks(x, a, b, H - 6)}</svg>`;
}
export function scoreBars(trend, sel) {
  if (!trend || !trend.length) return '';
  const W = 600, H = 170, T = 40, B = 40, slot = W / trend.length, bw = Math.min(28, slot - 4), y = (v) => T + (100 - v) / 100 * (H - T - B);
  const last = trend.length - 1;
  return `<svg class="chart" viewBox="0 0 ${W} ${H}"><line x1="0" x2="${W}" y1="${y(0)}" y2="${y(0)}" class="grid"/>` + trend.map((d, i) => {
    const cx = slot * i + slot / 2, on = d.day === sel;
    const bar = d.sleep_score == null ? '' : `<path d="M${cx - bw / 2},${y(0)} V${y(d.sleep_score) + 4} q0,-4 4,-4 H${cx + bw / 2 - 4} q4,0 4,4 V${y(0)} Z" class="${on ? 'bar on' : 'bar'}"/>`
      + (on ? `<text x="${cx}" y="${y(d.sleep_score) - 8}" text-anchor="middle" class="val">${d.sleep_score}</text>` : '');
    const lbl = (i === 0 || i === last || on) ? `<text x="${Math.min(W - 20, Math.max(20, cx))}" y="${H - 6}" text-anchor="middle">${String(d.day).slice(8)}</text>` : '';
    return bar + lbl;
  }).join('') + '</svg>';
}

let current = null;
let busy = false;
const last = { tabs: '', chips: '' };
const setHtml = (id, html) => { if (last[id] !== html) { last[id] = html; $(id).innerHTML = html; } };
function chips(s) {
  return [
    s.demo ? `<span class="chip warn">DEMO</span>` : '',
    s.listening ? `<span class="chip warn">LISTENING</span>` : '',
    `<span class="chip ${s.mode === 'auto' ? 'warn' : 'ok'}">${s.mode === 'auto' ? 'AUTO' : 'MANUAL'}</span>`,
    s.pending ? `<span class="chip warn blink">${s.pending} PENDING</span>` : '',
    s.frozen ? `<span class="chip bad">FROZEN</span>` : '',
    `<span class="chip ${s.hermes ? 'ok' : 'bad'}">${s.hermes ? 'HERMES' : 'HERMES OFF'}</span>`,
  ].join('');
}

async function tick() {
  if (busy) return;
  busy = true;
  try {
    const s = await (await fetch('/api/state')).json();
    setHtml('tabs', PANELS.map((p, i) => `<span class="${p === s.panel ? 'on' : ''}">${i + 1}${p === s.panel ? `<span class="name"> ${NAMES[p]}</span>` : ''}</span>`).join(''));
    setHtml('chips', chips(s));
    $('heard').textContent = s.heard; $('said').textContent = s.reply;
    const j = await (await fetch(`/api/panel/${s.panel}`)).json();
    setHtml('panel', j.error ? `<h1>${NAMES[s.panel]}</h1><p class="bad">${esc(j.error)}</p>`
      : (RENDER[s.panel] || (() => `<h1>${NAMES[s.panel]}</h1>`))(j, s));
    current = s.panel;
  } catch (e) { setHtml('chips', `<span class="chip bad">KIOSK OFFLINE</span>`); }
  finally { busy = false; }
}

document.addEventListener('keydown', (e) => {   // keyboard fallback: 1-9 switch panels
  if (e.key >= '1' && e.key <= '9') fetch(`/api/nav/${e.key}`, { method: 'POST' });
});
tick(); setInterval(tick, 1500);
