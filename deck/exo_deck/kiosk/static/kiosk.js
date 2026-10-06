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
      <span class="${t.risk === 'low' ? 'ok' : t.risk === 'high' ? 'bad' : 'warn'}">${esc(t.risk || '?')}${t.auto_eligible === true ? ' · auto' : ''}</span></li>`;
    const sent = j.approved.slice(0, Math.max(0, Math.min(3, 7 - j.pending.length)));   // keep to 7 rows in all
    return `<h1>Approvals · <span class="${j.mode === 'auto' ? 'warn' : 'ok'}">${j.mode === 'auto' ? 'AUTO (low-risk only)' : 'MANUAL'}</span></h1>`
      + (j.auto_request ? `<p class="warn big">Press the approve key to turn on auto-approve</p>` : '')
      + `<p class="muted">${esc(j.pending_total)} pending · the key approves the top one · say "auto approve on/off"</p>`
      + `<ul class="rows tight">${j.pending.map(row).join('') || '<li class="muted">Nothing waiting</li>'}</ul>`
      + (sent.length ? `<ul class="rows tight">${sent.map((t) => `<li><span>${esc(t.summary)}</span><span class="ok">sent</span></li>`).join('')}</ul>` : '');
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
      : (RENDER[s.panel] || (() => `<h1>${NAMES[s.panel]}</h1>`))(j);
    current = s.panel;
  } catch (e) { setHtml('chips', `<span class="chip bad">KIOSK OFFLINE</span>`); }
  finally { busy = false; }
}

document.addEventListener('keydown', (e) => {   // keyboard fallback: 1-9 switch panels
  if (e.key >= '1' && e.key <= '9') fetch(`/api/nav/${e.key}`, { method: 'POST' });
});
tick(); setInterval(tick, 1500);
