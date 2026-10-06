const PANELS = ['talk', 'agents', 'approvals', 'transactions', 'wallets', 'cre', 'body', 'sensors', 'media'];
const NAMES = { talk: 'Talk', agents: 'Agents', approvals: 'Approvals', transactions: 'Transactions',
  wallets: 'Wallets', cre: 'CRE', body: 'Body', sensors: 'Sensors', media: 'Media' };
const $ = (id) => document.getElementById(id);
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const ago = (ts) => { const s = Math.max(0, Date.now() / 1000 - ts); return s < 90 ? `${s | 0}s` : s < 5400 ? `${(s / 60) | 0}m` : s < 172800 ? `${(s / 3600) | 0}h` : `${(s / 86400) | 0}d`; };

export const RENDER = {
  talk: (j) => `<h1>Talk to Hermes</h1>` + (j.turns.length ? j.turns.map((t) =>
    `<div class="turn ${esc(t.role)}"><span class="who">${t.role === 'you' ? 'You' : t.role === 'hermes' ? 'Hermes' : '·'}</span>${esc(t.text)}</div>`).join('')
    : `<p class="muted">Hold Talk and speak.</p>`),
  approvals: (j) => {
    const row = (t) => `<li><span class="clamp">${esc(t.summary)}<br><span class="muted">${esc(t.explanation || '')}</span></span>
      <span class="tag ${t.risk === 'low' ? 'ok' : t.risk === 'high' ? 'bad' : 'warn'}">${esc(t.risk || '?')}${t.auto_eligible === true ? ' · auto' : ''}</span></li>`;
    const sent = j.approved.slice(0, Math.max(0, Math.min(3, 5 - j.pending.length - (j.pending_more ? 1 : 0))));   // pending rows are two lines: 5 row budget
    return `<h1>Approvals · <span class="${j.mode === 'auto' ? 'warn' : 'ok'}">${j.mode === 'auto' ? 'AUTO (low-risk only)' : 'MANUAL'}</span></h1>`
      + (j.auto_request ? `<p class="warn big">Press the approve key to turn on auto-approve</p>` : '')
      + `<p class="muted">${esc(j.pending_total)} pending · the key approves the top one · say "auto approve on/off"</p>`
      + `<ul class="rows tight">${j.pending.map(row).join('') || '<li class="muted">Nothing waiting</li>'}</ul>`
      + (j.pending_more ? `<p class="warn">+${esc(j.pending_more)} more — say "more"</p>` : '')
      + (sent.length ? `<ul class="rows tight">${sent.map((t) => `<li><span>${esc(t.summary)}</span><span class="ok">sent</span></li>`).join('')}</ul>` : '');
  },
  body: (j) => {
    if (!j.last) return `<h1>Body</h1><p class="bad">${esc(j.sync_error || 'No ring data')}</p>`;
    const l = j.last, v = (x, u = '') => x == null ? '–' : `${esc(x)}${u}`;
    const h = (m) => m == null ? '–' : `${(m / 60) | 0}h ${m % 60}m`;
    const bar = (x) => '▮'.repeat(Math.max(0, Math.min(10, Math.round((x || 0) / 10)))).padEnd(10, '▯');
    return `<h1>Body · night of ${esc(l.day)}</h1>
      <ul class="rows tight"><li><span>Sleep score</span><span class="big">${v(l.sleep_score)}</span></li>
      <li><span>Sleep · deep · REM</span><span class="big">${h(l.total_sleep_min)} · ${h(l.deep_min)} · ${h(l.rem_min)}</span></li>
      <li><span>HRV · resting HR</span><span class="big">${v(l.hrv_avg, ' ms')} · ${v(l.sleep_rhr, ' bpm')}</span></li>
      <li><span>Recovery · temp dev.</span><span class="big">${v(l.recovery_index)} · ${v(l.temp_deviation_c, '°C')}</span></li></ul>
      <p class="muted tight">Sleep score, last 7 nights</p>
      <p class="trend">${j.trend.map((t) => `${esc(String(t.day).slice(5))} <span class="accent">${bar(t.sleep_score)}</span> ${v(t.sleep_score)}`).join('<br>')}</p>
      <p class="muted tight">synced ${j.synced_at ? ago(j.synced_at) + ' ago' : 'never'}${j.sync_error ? ' · ' + esc(j.sync_error) : ''}</p>`;
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
      const f = `/api/media/file/${encodeURI(j.open.rel)}`;
      const body = j.open.where === 'cloud' ? '<p class="muted big">Stored in the cloud only</p>'
        : j.open.kind === 'photo' ? `<img src="${f}" alt="">` : j.open.kind === 'video' ? `<video src="${f}" poster="/api/media/thumb/${encodeURI(j.open.rel)}" autoplay playsinline></video>` : `<div class="glyph big">♪ Playing</div><audio src="${f}" autoplay></audio>`;
      return `<h1 class="clamp">${j.open.n} · ${esc(j.open.rel.split('/').pop())}</h1><p class="muted tight">${tag[j.open.where]} · say "close"</p><div class="full">${body}</div>`;
    }
    const tile = (i) => {
      const img = i.kind !== 'audio' && i.where !== 'cloud' ? `<img src="/api/media/thumb/${encodeURI(i.rel)}" alt="" onerror="this.remove()">` : '';
      return `<figure><div class="thumb"><span class="glyph">${i.where === 'cloud' ? '☁' : glyph[i.kind] || ''}</span>${img}<b class="num">${esc(i.n)}</b></div>
        <figcaption>${i.mtime ? ago(i.mtime) : '–'} · ${tag[i.where]}</figcaption></figure>`;
    };
    const more = (j.total > (j.page + 1) * 9) ? ' · "more"' : '';
    return `<h1>Media · ${esc(j.total)} files</h1><p class="muted tight clamp">${esc(j.sd_only)} SD only · ${esc(j.cloud_only)} cloud only · synced ${j.synced_at ? ago(j.synced_at) + ' ago' : 'never'} · "open 3"${more}</p>
      ${j.items.length ? `<div class="grid9">${j.items.map(tile).join('')}</div>` : '<p class="muted">No photos, memos or videos yet.</p>'}`;
  },
};

let current = null;
let busy = false;
const last = { tabs: '', chips: '' };
const setHtml = (id, html) => { if (last[id] !== html) { last[id] = html; $(id).innerHTML = html; } };
function chips(s) {
  return [
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
    $('panel').innerHTML = j.error ? `<h1>${NAMES[s.panel]}</h1><p class="bad">${esc(j.error)}</p>`
      : (RENDER[s.panel] || (() => `<h1>${NAMES[s.panel]}</h1>`))(j, s);
    current = s.panel;
  } catch (e) { setHtml('chips', `<span class="chip bad">KIOSK OFFLINE</span>`); }
  finally { busy = false; }
}

document.addEventListener('keydown', (e) => {   // keyboard fallback: 1-9 switch panels
  if (e.key >= '1' && e.key <= '9') fetch(`/api/nav/${e.key}`, { method: 'POST' });
});
tick(); setInterval(tick, 1500);
