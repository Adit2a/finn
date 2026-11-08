import { html, render, useState, useEffect, useMemo, useRef } from './vendor/preact-htm.js';

/* ====================================================================== utilities */
const store = {
  get: (k, d) => { try { const v = localStorage.getItem('finn.' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set: (k, v) => localStorage.setItem('finn.' + k, JSON.stringify(v)),
};
const inr = (x, d = 0) => x == null ? '—' : Number(x).toLocaleString('en-IN', { maximumFractionDigits: d, minimumFractionDigits: d });
const cr = x => x == null ? '—' : Math.abs(x) >= 1e5 ? `₹${(x / 1e5).toFixed(2)}L cr` : `₹${inr(x)} cr`;
const pct = (x, d = 1) => x == null ? '—' : `${x > 0 ? '+' : ''}${Number(x).toFixed(d)}%`;
const tone = x => x == null || Math.abs(x) < 0.05 ? 'flat' : x > 0 ? 'up' : 'down';
const DAY = 864e5;
const fmtDate = d => new Date(d).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
const fmtDay = d => new Date(d).toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'short' });
function ago(ts, now) {
  const s = (now - new Date(ts)) / 1000;
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  if (s < 7 * 86400) return `${Math.round(s / 86400)}d ago`;
  return fmtDate(ts);
}
const initials = n => (n || '?').replace(/^(mr|ms|mrs|dr|shri|smt)\.?\s+/i, '').split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase();
const go = h => { location.hash = h; };
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;

const WINDOWS = [
  { k: 'session', label: 'Last session', title: 'Since the close before the latest trading day — covers evenings and weekends' },
  { k: 1, label: '24 hours' }, { k: 7, label: '7 days' }, { k: 30, label: '30 days' },
  { k: 'last', label: 'New since last look' },
];
function sessionStart(holdings, asOf) {  // close (15:30 IST) of the trading day before the latest one
  const days = [...new Set(holdings.flatMap(h => (h.series || []).map(x => x.d)))].sort();
  const prev = days[days.length - 2];
  return prev ? new Date(prev + 'T15:30:00+05:30').getTime() : asOf - DAY;
}
const CAT_LABEL = {
  results: 'Results', 'M&A': 'M&A', fund_raise: 'Fund raise', management_change: 'Management', litigation: 'Legal',
  insider_trading: 'Insider trade', earnings_call: 'Earnings call', investor_meetings: 'Investor meet', credit_rating: 'Credit rating',
  dividend: 'Dividend', bonus_split: 'Bonus / split', capex: 'Capex', press_release: 'Press release',
  postal_ballot: 'Postal ballot', agm_egm: 'AGM / EGM', pledging: 'Pledge', unclassified: 'Paperwork', price: 'Unusual move',
};
const CAT_COLOR = {
  results: 'var(--accent)', management_change: '#b48cff', 'M&A': '#5fc8d8', fund_raise: '#5fc8d8', litigation: 'var(--down)',
  credit_rating: 'var(--warn)', pledging: 'var(--down)', price: 'var(--warn)', capex: 'var(--up)', insider_trading: 'var(--warn)',
};
const catColor = c => CAT_COLOR[c] || 'var(--faint)';

/* ====================================================================== icons */
const I = {
  brief: 'M4 6h16M4 11h16M4 16h10',
  alerts: 'M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10 21h4',
  research: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-5-5',
  portfolio: 'M4 8h16a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1zM9 8V6a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2M3 13h18',
  cal: 'M4 6h16v14H4zM4 10h16M8 3v4M16 3v4',
  x: 'M6 6l12 12M18 6L6 18',
  plus: 'M12 5v14M5 12h14',
  send: 'M21 3L10 14M21 3l-7 18-4-7-7-4 18-7z',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  less: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM8 12h8',
  ext: 'M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5',
  collapse: 'M15 6l-6 6 6 6M4 4v16',
  doc: 'M7 3h7l5 5v12a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM14 3v5h5M9 13h6M9 17h6',
  mic: 'M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3',
};
const Icon = ({ d, size = 20 }) => html`<svg viewBox="0 0 24 24" width=${size} height=${size} fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d=${d}/></svg>`;

/* ====================================================================== alerts engine (client-side so rules apply instantly) */
function priceAlerts(holdings, rules, sinceMs) {
  const out = [], L = rules.volume_lookback_days;
  for (const h of holdings) {
    const s = h.series || [];
    for (let i = Math.max(1, L); i < s.length; i++) {
      const d = s[i], t = new Date(d.d + 'T15:30:00+05:30').getTime();
      if (t <= sinceMs) continue;  // the boundary close belongs to the previous session
      const ret = (d.c / s[i - 1].c - 1) * 100;
      const win = s.slice(i - L, i), avg = win.reduce((a, x) => a + x.v, 0) / win.length;
      const mult = avg ? d.v / avg : 0;
      const pHit = Math.abs(ret) >= rules.price_move_pct, vHit = mult >= rules.volume_multiple;
      if (!pHit && !vHit) continue;
      const score = Math.min(96, Math.round(52 + (pHit ? Math.abs(ret) * 3 : 0) + (vHit ? (mult - 1) * 7 : 0) + (pHit && vHit ? 8 : 0)));
      out.push({ id: `px-${h.symbol}-${d.d}`, kind: 'price', category: 'price', symbol: h.symbol, company: h.name,
        ts: new Date(t).toISOString(), ret, mult, close: d.c, pHit, vHit, score, series: s.slice(Math.max(0, i - 29), i + 1) });
    }
  }
  return out;
}
function linkCauses(px, events) {  // what filing most likely explains each move
  for (const a of px) {
    const t = new Date(a.ts).getTime();
    a.cause = events.filter(e => e.symbol === a.symbol && e.score >= 33 && new Date(e.ts) <= t + 6 * 3600e3 && new Date(e.ts) >= t - 3 * DAY)
      .sort((x, y) => y.score - x.score)[0];
  }
  return px;
}

/* ====================================================================== tiny charts */
function Spark({ data, w = 120, h = 36, dir }) {
  if (!data || data.length < 2) return null;
  const min = Math.min(...data), max = Math.max(...data), r = max - min || 1;
  const y = v => h - 3 - ((v - min) / r) * (h - 6);
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${y(v)}`).join(' ');
  const c = (dir != null ? dir >= 0 : data[data.length - 1] >= data[0]) ? 'var(--up)' : 'var(--down)';
  return html`<svg width=${w} height=${h} viewBox="0 0 ${w} ${h}" aria-hidden="true">
    <polyline points=${pts} fill="none" stroke=${c} stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx=${w} cy=${y(data[data.length - 1])} r="3" fill=${c}/>
  </svg>`;
}
function Bars({ values, pair, color = 'var(--accent)', pairColor = 'var(--line-2)' }) {
  const vals = values.map(v => v ?? 0), all = pair ? vals.concat(pair.map(v => v ?? 0)) : vals;
  const max = Math.max(...all, 0), min = Math.min(...all, 0), r = max - min || 1, n = vals.length;
  const zero = 50 * (max / r), bw = 100 / n;
  const bar = (v, i, off, wf, c) => {
    const yy = v >= 0 ? zero - (v / r) * 50 : zero, hh = Math.max(1, Math.abs(v / r) * 50);
    return html`<rect x=${i * bw + off} y=${yy} width=${bw * wf} height=${hh} rx="1.2" fill=${c} opacity=${i === n - 1 ? 1 : 0.5}/>`;
  };
  return html`<svg viewBox="0 0 100 50" preserveAspectRatio="none" aria-hidden="true">
    ${vals.map((v, i) => pair ? [bar(v, i, bw * 0.12, 0.36, color), bar(pair[i] ?? 0, i, bw * 0.52, 0.36, pairColor)] : bar(v, i, bw * 0.18, 0.64, color))}
  </svg>`;
}
function Line({ values, color = 'var(--accent)' }) {
  const v = values.filter(x => x != null);
  if (v.length < 2) return html`<svg viewBox="0 0 100 50"></svg>`;
  const min = Math.min(...v), max = Math.max(...v), r = max - min || 1;
  const pts = values.map((x, i) => x == null ? null : `${(i / (values.length - 1)) * 100},${46 - ((x - min) / r) * 40}`).filter(Boolean).join(' ');
  return html`<svg viewBox="0 0 100 50" preserveAspectRatio="none" aria-hidden="true">
    <polyline points=${pts} fill="none" stroke=${color} stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"/>
  </svg>`;
}
function Ring({ score, size = 56 }) {
  const r = size / 2 - 4, c = 2 * Math.PI * r;
  const col = score == null ? 'var(--faint)' : score >= 7 ? 'var(--up)' : score >= 4 ? 'var(--warn)' : 'var(--down)';
  return html`<div class="ring" style="width:${size}px;height:${size}px" title=${score == null ? 'Not enough checkable promises to score' : `Credibility ${score}/10`}>
    <svg width=${size} height=${size}><circle cx=${size / 2} cy=${size / 2} r=${r} fill="none" stroke="var(--line-2)" stroke-width="4"/>
      ${score != null && html`<circle cx=${size / 2} cy=${size / 2} r=${r} fill="none" stroke=${col} stroke-width="4" stroke-linecap="round"
        stroke-dasharray=${`${(score / 10) * c} ${c}`}/>`}</svg>
    <span class="val num" style="font-size:${size * 0.36}px;color:${col}">${score ?? '–'}</span>
  </div>`;
}
function heatStyle(chg) {
  if (chg == null) return 'background:var(--raised)';
  const a = Math.min(0.9, 0.14 + Math.abs(chg) / 4 * 0.7);
  return `background:rgba(var(${chg >= 0 ? '--up-rgb' : '--down-rgb'}),${a.toFixed(2)});color:${a > 0.45 ? '#fff' : 'var(--text)'}`;
}

/* ====================================================================== app shell */
function App() {
  const [route, setRoute] = useState(location.hash.slice(1) || 'brief');
  const [feed, setFeed] = useState(null);
  const [status, setStatus] = useState(null);
  const [err, setErr] = useState(null);
  const [rules, setRulesRaw] = useState(null);
  const [toast, setToast] = useState(null);
  const [slim, setSlimRaw] = useState(store.get('slim', false));
  const setSlim = v => { setSlimRaw(v); store.set('slim', v); };
  useEffect(() => {
    const on = () => { setRoute(location.hash.slice(1) || 'brief'); window.scrollTo(0, 0); };
    const key = e => {  // Ctrl/⌘-K or "/" jumps to company search; Ctrl/⌘-B toggles the sidebar
      if ((e.key === 'k' && (e.metaKey || e.ctrlKey)) || (e.key === '/' && !/INPUT|TEXTAREA/.test(document.activeElement.tagName))) {
        e.preventDefault(); go('research'); setTimeout(() => document.querySelector('.search input')?.focus(), 50);
      }
      if (e.key === 'b' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); setSlimRaw(s => { store.set('slim', !s); return !s; }); }
    };
    addEventListener('hashchange', on); addEventListener('keydown', key);
    return () => { removeEventListener('hashchange', on); removeEventListener('keydown', key); };
  }, []);
  const loadStatus = () => fetch('/api/status').then(r => r.json()).then(setStatus).catch(() => {});
  const [pf, setPfRaw] = useState(null);  // this device's portfolio: [{symbol, qty?, avg?}]
  const loadFeed = () => fetch('/api/feed').then(r => r.ok ? r.json() : Promise.reject(r.statusText)).then(f => {
    setFeed(f); setRulesRaw(r => r || { ...f.rules, ...store.get('rules', {}) });
    setPfRaw(p => p || store.get('portfolio', null) || (f.default_portfolio || f.holdings.map(h => h.symbol)).map(symbol => ({ symbol })));
    return f;
  });
  useEffect(() => { loadFeed().catch(e => setErr(String(e))); loadStatus(); }, []);
  const setRules = r => { setRulesRaw(r); store.set('rules', r); syncTelegram(r, pf); };
  const setPf = p => { setPfRaw(p); store.set('portfolio', p); syncTelegram(rules, p); };
  const flash = (msg, undo) => { setToast({ msg, undo }); clearTimeout(flash.t); flash.t = setTimeout(() => setToast(null), 3500); };

  const [tab, arg] = route.split('/');
  const asOf = feed ? new Date(feed.generated_at).getTime() : Date.now();
  // everything the app shows is filtered to this device's portfolio
  const view = useMemo(() => {
    if (!feed || !pf) return null;
    const mine = new Set(pf.map(p => p.symbol));
    return { ...feed, holdings: feed.holdings.filter(h => mine.has(h.symbol)), events: feed.events.filter(e => mine.has(e.symbol)),
             upcoming: (feed.upcoming || []).filter(u => mine.has(u.symbol)) };
  }, [feed, pf]);
  const alertCount = useMemo(() => {
    if (!view || !rules) return 0;
    const since = sessionStart(view.holdings, asOf);
    return priceAlerts(view.holdings, rules, since).length +
      view.events.filter(e => rules.categories.includes(e.category) && e.score >= 60 && new Date(e.ts) >= since).length;
  }, [view, rules]);

  if (err) return html`<div class="empty" style="padding-top:30vh"><div class="glyph">⚠︎</div>Couldn't load the brief.<br/><span class="mono">${err}</span></div>`;
  if (!view || !rules) return html`<div class="empty" style="padding-top:40vh"><span class="spin"></span></div>`;

  const nav = [['brief', 'Brief', I.brief], ['portfolio', 'Portfolio', I.portfolio], ['alerts', 'Alerts', I.alerts], ['research', 'Research', I.research]];
  return html`<div class="shell ${slim ? 'slim' : ''}">
    <nav class="nav" aria-label="Main">
      <div class="brandmark"><img src="/icon.svg" alt="FINN"/><div>FINN</div></div>
      <button class="nav-search" title="Search companies (Ctrl K)" onClick=${() => { go('research'); setTimeout(() => document.querySelector('.search input')?.focus(), 50); }}>
        <${Icon} d=${I.research} size=${15}/><span class="lbl">Search</span><kbd>Ctrl K</kbd></button>
      ${nav.map(([k, l, d]) => html`
        <button class="nav-item ${tab === k ? 'on' : ''}" aria-current=${tab === k ? 'page' : null} title=${l}
          onClick=${() => go(k === 'research' && tab !== 'research' && lastSym() ? `research/${lastSym()}` : k)}>
          <${Icon} d=${d}/><span class="lbl">${l}</span>${k === 'alerts' && alertCount > 0 && html`<span class="badge">${alertCount}</span>`}
        </button>`)}
      <div class="nav-foot"><button class="collapse-btn" onClick=${() => setSlim(!slim)} title=${slim ? 'Expand sidebar (Ctrl B)' : 'Collapse sidebar (Ctrl B)'}
        aria-label=${slim ? 'Expand sidebar' : 'Collapse sidebar'}><${Icon} d=${I.collapse}/><span class="lbl">Collapse</span></button></div>
    </nav>
    <main class="main">
      ${tab === 'brief' && html`<${Brief} feed=${view} asOf=${asOf} rules=${rules} flash=${flash} focus=${arg} pf=${pf}/>`}
      ${tab === 'portfolio' && html`<${Portfolio} feed=${feed} pf=${pf} setPf=${setPf} flash=${flash} reload=${loadFeed}/>`}
      ${tab === 'alerts' && html`<${Alerts} feed=${view} asOf=${asOf} rules=${rules} setRules=${setRules} pf=${pf}/>`}
      ${tab === 'research' && html`<${Research} sym=${arg} status=${status} feed=${view} onDone=${loadStatus}/>`}
    </main>
    ${toast && html`<div class="toast" role="status">${toast.msg}${toast.undo && html`<button onClick=${() => { toast.undo(); setToast(null); }}>Undo</button>`}</div>`}
  </div>`;
}
const lastSym = () => store.get('lastSym', null);

/* ====================================================================== BRIEF */
function Brief({ feed, asOf, rules, flash, focus, pf }) {
  const [win, setWin] = useState(store.get('win', 'session'));
  const [done, setDone] = useState(() => new Set(store.get('done', [])));
  const [muted, setMuted] = useState(() => new Set(store.get('muted', [])));
  const [sym, setSym] = useState(focus || null);
  useEffect(() => { setSym(focus || null); }, [focus]);
  const [filedOpen, setFiledOpen] = useState(false);
  const [openRoutine, setOpenRoutine] = useState(null);
  const [showCleared, setShowCleared] = useState(false);
  const [leaving, setLeaving] = useState(null);
  const lastLook = useMemo(() => store.get('lastLook', asOf - DAY), []);
  useEffect(() => { const t = setTimeout(() => store.set('lastLook', Math.min(Date.now(), asOf)), 4000); return () => clearTimeout(t); }, []);

  const since = win === 'last' ? Math.min(lastLook, asOf - 3600e3) : win === 'session' ? sessionStart(feed.holdings, asOf) : asOf - win * DAY;
  const pickWin = k => { setWin(k); store.set('win', k); };

  const items = useMemo(() => {
    const ev = feed.events.map(e => ({ ...e, kind: 'filing' }));
    const px = linkCauses(priceAlerts(feed.holdings, rules, since), feed.events).map(a => ({ ...a, tier: a.score >= 68 ? 'act' : 'glance' }));
    return ev.concat(px)
      .filter(e => new Date(e.ts) >= since && (!sym || e.symbol === sym))
      .map(e => muted.has(`${e.symbol}|${e.category}`) ? { ...e, tier: 'routine', mutedBy: true } : e)
      .sort((a, b) => b.score - a.score || new Date(b.ts) - new Date(a.ts));
  }, [feed, rules, since, sym, muted]);

  const visible = items.filter(e => showCleared || !done.has(e.id));
  const act = visible.filter(e => e.tier === 'act'), glance = visible.filter(e => e.tier === 'glance');
  const routine = visible.filter(e => e.tier === 'routine');
  const cleared = items.filter(e => done.has(e.id)).length;
  const rGroups = routine.reduce((m, e) => ((m[e.category] = m[e.category] || []).push(e), m), {});

  const clear = id => {
    setLeaving(id);
    setTimeout(() => {
      const n = new Set(done); n.add(id); setDone(n); store.set('done', [...n]); setLeaving(null);
      flash('Cleared', () => { n.delete(id); setDone(new Set(n)); store.set('done', [...n]); });
    }, 220);
  };
  const mute = e => {
    const key = `${e.symbol}|${e.category}`, n = new Set(muted); n.add(key); setMuted(n); store.set('muted', [...n]);
    flash(`Fewer ${CAT_LABEL[e.category].toLowerCase()} items from ${e.symbol}`, () => { n.delete(key); setMuted(new Set(n)); store.set('muted', [...n]); });
  };
  const pulse = c => html`<${Pulse} feed=${feed} sym=${sym} setSym=${setSym} compact=${c}/>`;
  const hour = new Date().getHours();
  const greeting = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';
  const span = { session: 'since the last session', 1: 'in the last 24 hours', 7: 'this week', 30: 'this month', last: 'since you last looked' }[win];
  const summary = act.length === 0 ? `Nothing needs your attention ${span}.`
    : act.length === 1 ? `One item needs your attention ${span}.` : `${act.length} items need your attention ${span}.`;

  return html`
    <header class="head">
      <div class="eyebrow">${fmtDay(asOf)}</div>
      <h1>${greeting}</h1>
      <p class="sub">${sym ? html`Showing <b>${sym}</b> only · <a href="#brief" onClick=${() => setSym(null)}>Show everything</a>` : summary}</p>
    </header>
    <div class="tabs-wrap"><div class="tabs" role="tablist">${WINDOWS.map(w => html`
      <button role="tab" aria-selected=${win === w.k} class=${win === w.k ? 'on' : ''} title=${w.title || ''} onClick=${() => pickWin(w.k)}>${w.label}</button>`)}</div></div>

    <div class="cols">
      <div>
        <div class="rail-mobile sec">${pulse(true)}</div>

        ${act.length > 0 && html`<section class="sec">
          <div class="sec-h"><h2><span class="dot" style="background:var(--down)"></span>Needs attention
            <span class="info" title="Ranked by materiality: the exchange's own subject lines, exact figures from XBRL filings, deal size against market cap, and a small model that reads only the filings whose summary is unclear. Clear an item with ✓ or a left swipe; ⊖ shows you less of that kind.">i</span></h2></div>
          ${act.map(e => html`<${Card} key=${e.id} e=${e} asOf=${asOf} leaving=${leaving === e.id} onDone=${() => clear(e.id)} onMute=${() => mute(e)} isDone=${done.has(e.id)}/>`)}
        </section>`}

        ${glance.length > 0 && html`<section class="sec">
          <div class="sec-h"><h2><span class="dot" style="background:var(--warn)"></span>Worth a glance</h2></div>
          <div class="list">${glance.map(e => html`<${Row} key=${e.id} e=${e} asOf=${asOf} onDone=${() => clear(e.id)} onMute=${() => mute(e)}/>`)}</div>
        </section>`}

        ${feed.upcoming?.length > 0 && html`<div class="rail-mobile sec"><${Upcoming} items=${feed.upcoming} limit=${4}/></div>`}
        <div class="rail-mobile sec"><${TelegramPanel} rules=${rules} pf=${pf} compact/></div>
        ${act.length + glance.length === 0 && html`<div class="empty"><div class="glyph">☕</div>
          Nothing material ${win === 'last' ? 'since your last look' : win === 'session' ? 'since the last session' : `in the last ${WINDOWS.find(w => w.k === win).label}`}${sym ? ` for ${sym}` : ''}.
          ${win !== 30 && html`<div style="margin-top:16px"><button class="btn" onClick=${() => pickWin(win === 7 ? 30 : 7)}>Look back ${win === 7 ? '30 days' : '7 days'}</button></div>`}
        </div>`}

        ${routine.length > 0 && html`<section class="sec"><div class="list">
          <button class="filed" onClick=${() => setFiledOpen(!filedOpen)} aria-expanded=${filedOpen}>
            <span class="dot" style="background:var(--faint)"></span>
            <span style="flex:1">Routine filings</span>
            <span class="faint">${filedOpen ? 'Hide' : 'Show'}</span></button>
          ${filedOpen && html`<div class="chips">${Object.entries(rGroups).sort((a, b) => b[1].length - a[1].length).map(([c, l]) => html`
            <button class=${openRoutine === c ? 'on' : ''} onClick=${() => setOpenRoutine(openRoutine === c ? null : c)}>${CAT_LABEL[c] || c} · ${l.length}</button>`)}</div>`}
          ${filedOpen && openRoutine && rGroups[openRoutine]?.map(e => html`<${Row} key=${e.id} e=${e} asOf=${asOf} onDone=${() => clear(e.id)}/>`)}
        </div></section>`}

        ${cleared > 0 && html`<div style="text-align:center;padding:18px 0 0"><button class="btn" onClick=${() => setShowCleared(!showCleared)}>${showCleared ? 'Hide cleared items' : 'Show cleared items'}</button></div>`}
      </div>
      <aside class="rail">${pulse(false)}<${TelegramPanel} rules=${rules} pf=${pf} compact/><${AlertsMini} feed=${feed} rules=${rules} asOf=${asOf}/><${Upcoming} items=${feed.upcoming}/></aside>
    </div>`;
}

function Pulse({ feed, sym, setSym, compact }) {
  const [all, setAll] = useState(!compact);
  const moves = feed.holdings.map(h => { const s = h.series || [], n = s.length; return { ...h, chg: n > 1 ? (s[n - 1].c / s[n - 2].c - 1) * 100 : null }; });
  const valid = moves.filter(m => m.chg != null);
  const avg = valid.reduce((a, m) => a + m.chg, 0) / (valid.length || 1);
  const ups = valid.filter(m => m.chg > 0).length;
  const sorted = [...moves].sort((a, b) => (b.chg ?? 0) - (a.chg ?? 0));
  // compact (phones): the 8 biggest movers either way, in portfolio order of move
  const shown = all ? sorted : [...moves].sort((a, b) => Math.abs(b.chg ?? 0) - Math.abs(a.chg ?? 0)).slice(0, 8).sort((a, b) => (b.chg ?? 0) - (a.chg ?? 0));
  const last = feed.holdings[0]?.series?.at(-1)?.d;
  return html`<div class="panel">
    <div class="panel-h"><h3>Portfolio</h3>
      ${sym ? html`<button onClick=${() => setSym(null)}>Clear filter</button>` : compact && html`<button onClick=${() => setAll(!all)}>${all ? 'Top movers' : 'View all'}</button>`}</div>
    <div class="pulse-row"><span class="pulse-big num ${tone(avg)}">${pct(avg, 2)}</span>
      <span class="faint">${last ? `${fmtDate(last)} close` : ''} · ${ups} up, ${valid.length - ups} down</span></div>
    <div class="heat">${shown.map(h => html`
      <button class=${sym === h.symbol ? 'on' : ''} style=${heatStyle(h.chg)} title=${`${h.name}: ${pct(h.chg, 2)} — tap to focus the brief`}
        onClick=${() => setSym(sym === h.symbol ? null : h.symbol)}>
        <span class="s">${h.symbol}</span><span class="c num">${pct(h.chg, 1)}</span></button>`)}</div>
  </div>`;
}

function AlertsMini({ feed, rules, asOf }) {
  const since = sessionStart(feed.holdings, asOf);
  const px = priceAlerts(feed.holdings, rules, since);
  return html`<div class="panel">
    <div class="panel-h"><h3>Alerts</h3><a href="#alerts">Rules</a></div>
    ${px.length ? px.slice(0, 5).map(a => html`<a class="row" href="#brief/${a.symbol}" style="padding:8px 0">
        <span class="sym">${a.symbol}</span><span class="t num ${tone(a.ret)}" style="font-weight:600">${pct(a.ret)}</span>
        <span class="w">${a.vHit ? `${a.mult.toFixed(1)}× vol` : ''}</span></a>`)
      : html`<div class="faint" style="font-size:13.5px">No alerts last session.</div>`}
  </div>`;
}

/* swipe left to clear, like an inbox */
function useSwipe(onDone) {
  const [dx, setDx] = useState(0);
  const start = useRef(null);
  return {
    dx,
    handlers: {
      onTouchStart: ev => { start.current = [ev.touches[0].clientX, ev.touches[0].clientY]; },
      onTouchMove: ev => {
        if (!start.current) return;
        const x = ev.touches[0].clientX - start.current[0], y = ev.touches[0].clientY - start.current[1];
        if (Math.abs(y) > Math.abs(x)) { start.current = null; setDx(0); return; }
        setDx(Math.min(0, x));
      },
      onTouchEnd: () => { if (dx < -90) onDone(); setDx(0); start.current = null; },
    },
  };
}
const swipeStyle = (dx, extra = '') => `${extra}${dx ? `transform:translateX(${dx}px);opacity:${1 + dx / 300};transition:none` : ''}`;
const Controls = ({ onMute, onDone }) => html`
  ${onMute && html`<button class="icon-btn" title="Show me less like this" aria-label="Show less like this" onClick=${onMute}><${Icon} d=${I.less}/></button>`}
  <button class="icon-btn" title="Done — clear it (or swipe left)" aria-label="Done" onClick=${onDone}><${Icon} d=${I.check}/></button>`;

function Card({ e, asOf, leaving, onDone, onMute, isDone }) {
  const sw = useSwipe(onDone);
  const [rel, setRel] = useState(false);
  if (e.kind === 'price') return html`<${PriceCard} e=${e} asOf=${asOf} leaving=${leaving} onDone=${onDone} onMute=${onMute}/>`;
  const research = ['results', 'earnings_call'].includes(e.category);
  return html`<article class="card ${leaving ? 'leaving' : ''}" ...${sw.handlers} style=${swipeStyle(sw.dx, isDone ? 'opacity:.5;' : '')}>
    <div class="meta"><span class="sym">${e.symbol}</span>
      <span class="cat"><span class="dot" style="background:${catColor(e.category)}"></span>${CAT_LABEL[e.category]}</span>
      <span class="when">${ago(e.ts, asOf)}</span><${Controls} onMute=${onMute} onDone=${onDone}/></div>
    <div class="headline">${e.headline}</div>
    ${e.why && html`<div class="why">${e.why}</div>`}
    ${e.results && html`<${Results} r=${e.results}/>`}
    ${e.people?.length > 0 && html`<div class="people">${e.people.slice(0, 3).map(p => html`<${Person} p=${p} company=${e.company}/>`)}</div>`}
    ${e.reasons?.length > 0 && !e.results && html`<div class="because">${e.reasons.join(' · ')}</div>`}
    <div class="links">
      ${research && html`<a class="primary" href="#research/${e.symbol}">See promise ledger</a>`}
      ${e.pdf && html`<a href=${e.pdf} target="_blank" rel="noopener">Read filing ↗</a>`}
      ${e.related?.length > 0 && html`<button class="quiet" onClick=${() => setRel(!rel)}>${rel ? 'Hide' : `+${e.related.length}`} related</button>`}
    </div>
    ${rel && html`<div style="margin-top:8px">${e.related.map(r => html`<a href=${r.pdf} target="_blank" rel="noopener" class="row" style="padding:8px 0;font-size:13px">
      <span class="dot" style="background:${catColor(r.category)}"></span><span class="t">${r.headline}</span></a>`)}</div>`}
  </article>`;
}

function Results({ r }) {
  return html`<div>
    <div class="stats num">${r.metrics.map(m => html`<div class="stat">
      <div class="k">${m.label.replace(' (n.m. for lenders)', '')} · YoY</div>
      <div class="y ${tone(m.yoy)}">${m.value == null ? 'n.m.' : pct(m.yoy, 0)}</div>
      <div class="q">${m.value == null ? 'not meaningful' : html`₹${inr(m.value)} cr · QoQ <span class=${tone(m.qoq)}>${pct(m.qoq, 0)}</span>`}</div>
    </div>`)}</div>
    <div class="fine">${r.quarter} · ${r.basis}${r.ebitda_margin != null ? html` · EBITDA margin ${r.ebitda_margin}%${r.ebitda_margin_bps_yoy != null ? html` <span class=${tone(r.ebitda_margin_bps_yoy)}>(${r.ebitda_margin_bps_yoy > 0 ? '+' : ''}${r.ebitda_margin_bps_yoy} bps)</span>` : ''}` : ''} · <a href=${r.source} target="_blank" rel="noopener" title=${r.derived?.length ? 'Quarter derived from year-to-date figures in the filing' : 'Read from the company’s XBRL filing'}>Source</a></div>
  </div>`;
}

function Person({ p, company }) {
  const q = encodeURIComponent(`${(p.name || '').replace(/^(mr|ms|mrs|dr|shri|smt)\.?\s+/i, '')} ${company || ''}`);
  const verb = { appointed: 'Joins as', resigned: 'Leaves as', retired: 'Retires as', 're-appointed': 'Re-appointed as', redesignated: 'Now' }[p.change] || p.change;
  return html`<div class="person">
    <div class="avatar">${initials(p.name)}</div>
    <div style="flex:1;min-width:0">
      <div class="nm">${p.name}</div>
      <div class="rl">${verb} ${p.role}${p.effective ? ` · ${p.effective}` : ''}</div>
      ${p.background && html`<div class="bg">${p.background}</div>`}
      ${p.previous && html`<div class="pv">Previously ${p.previous}</div>`}
      ${p.reason && html`<div class="pv">Reason given: ${p.reason}</div>`}
      <div class="pv"><a href="https://www.linkedin.com/search/results/people/?keywords=${q}" target="_blank" rel="noopener">LinkedIn ↗</a>
        ${p.profile_source && html` · <a href=${p.profile_url} target="_blank" rel="noopener">${p.profile_source.replace(' (name match)', '')} ↗</a>`}</div>
    </div>
  </div>`;
}

function PriceCard({ e, asOf, leaving, onDone, onMute }) {
  const sw = useSwipe(onDone);
  return html`<article class="card ${leaving ? 'leaving' : ''}" ...${sw.handlers} style=${swipeStyle(sw.dx)}>
    <div class="meta"><span class="sym">${e.symbol}</span>
      <span class="cat"><span class="dot" style="background:var(--warn)"></span>Unusual move</span>
      <span class="when">${fmtDate(e.ts)}</span><${Controls} onMute=${onMute} onDone=${onDone}/></div>
    <div class="move">
      <div><div class="big num ${tone(e.ret)}">${pct(e.ret)}</div>
        <div class="sm num">${e.vHit ? `${e.mult.toFixed(1)}× normal volume` : 'normal volume'} · ₹${inr(e.close, 1)}</div></div>
      <${Spark} data=${e.series.map(x => x.c)} w=${130} h=${46} dir=${e.ret}/>
    </div>
    <div class="trigger">${e.cause ? html`Likely trigger — <b>${CAT_LABEL[e.cause.category]}:</b> ${e.cause.headline}` : html`<b>No filing explains this.</b> Market-driven, or news not yet disclosed.`}</div>
    <div class="links">
      <a href="#research/${e.symbol}">Research ${e.symbol}</a>
      ${e.cause?.pdf && html`<a href=${e.cause.pdf} target="_blank" rel="noopener">Read that filing ↗</a>`}
    </div>
  </article>`;
}

function Row({ e, asOf, onDone, onMute }) {
  const [open, setOpen] = useState(false);
  const title = e.kind === 'price' ? `${pct(e.ret)} move${e.vHit ? ` on ${e.mult.toFixed(1)}× volume` : ''}` : e.headline;
  return html`
    <button class="row" onClick=${() => setOpen(!open)} aria-expanded=${open}>
      <span class="dot" style="background:${catColor(e.category)}"></span>
      <span class="sym">${e.symbol}</span>
      <span class="t">${title}<small>${CAT_LABEL[e.category]}${e.why ? ` · ${e.why}` : ''}</small></span>
      <span class="w">${ago(e.ts, asOf)}</span>
    </button>
    ${open && html`<div class="expand">
      ${e.results && html`<${Results} r=${e.results}/>`}
      ${e.people?.length > 0 && html`<div class="people">${e.people.slice(0, 2).map(p => html`<${Person} p=${p} company=${e.company}/>`)}</div>`}
      ${e.kind === 'price' && html`<div class="trigger">${e.cause ? html`Likely trigger — ${e.cause.headline}` : 'No filing explains this move.'}</div>`}
      ${e.reasons?.length > 0 && html`<div class="because">${e.reasons.join(' · ')}</div>`}
      <div class="links">
        ${['results', 'earnings_call'].includes(e.category) && html`<a href="#research/${e.symbol}">Promise ledger</a>`}
        ${e.pdf && html`<a href=${e.pdf} target="_blank" rel="noopener">Read filing ↗</a>`}
        ${onMute && html`<button class="quiet" onClick=${onMute}>Show less like this</button>`}
        <button class="quiet" onClick=${onDone}>Mark done</button>
      </div>
    </div>`}`;
}

/* ====================================================================== PORTFOLIO */
function Portfolio({ feed, pf, setPf, flash, reload }) {
  const [pending, setPending] = useState({});  // symbol -> 'building' | 'failed'
  const [, setTick] = useState(0);
  useEffect(() => { loadSymbols().then(() => setTick(t => t + 1)); }, []);
  const bySym = Object.fromEntries(feed.holdings.map(h => [h.symbol, h]));
  const nameOf = s => bySym[s]?.name || SYMBOLS?.find(c => c.s === s)?.n || s;

  useEffect(() => {  // poll stocks whose data is being fetched for the first time
    const syms = Object.keys(pending).filter(s => pending[s] === 'building');
    if (!syms.length) return;
    const t = setInterval(async () => {
      for (const s of syms) {
        const r = await fetch(`/api/track/${encodeURIComponent(s)}`).then(r => r.json()).catch(() => null);
        if (r?.status === 'ready') { await reload(); setPending(p => { const n = { ...p }; delete n[s]; return n; }); flash(`${s} is ready`); }
        else if (r?.status?.startsWith('failed')) setPending(p => ({ ...p, [s]: 'failed' }));
      }
    }, 6000);
    return () => clearInterval(t);
  }, [pending]);

  const add = async sym => {
    if (pf.some(p => p.symbol === sym)) return flash(`${sym} is already in your portfolio`);
    const prev = pf;
    setPf([...pf, { symbol: sym }]);
    if (bySym[sym]) return flash(`Added ${sym}`);
    const r = await fetch('/api/track', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ symbol: sym }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) { setPf(prev); return flash(j.detail || `Couldn't add ${sym}`); }
    if (j.status === 'building') { setPending(p => ({ ...p, [sym]: 'building' })); flash(`Adding ${sym} — fetching its filings and prices`); }
    else { await reload(); flash(`Added ${sym}`); }
  };
  const remove = sym => { const prev = pf; setPf(pf.filter(p => p.symbol !== sym)); flash(`Removed ${sym}`, () => setPf(prev)); };
  const edit = (sym, k, v) => setPf(pf.map(p => p.symbol === sym ? { ...p, [k]: v === '' || isNaN(+v) ? undefined : +v } : p));

  const rows = pf.map(p => {
    const s = bySym[p.symbol]?.series || [], last = s.at(-1)?.c, prev = s.at(-2)?.c;
    return { ...p, name: nameOf(p.symbol), last, chg: last && prev ? (last / prev - 1) * 100 : null, series: s,
      value: p.qty && last ? p.qty * last : null, day: p.qty && last && prev ? (last - prev) * p.qty : null,
      pnl: p.qty && p.avg && last ? (last - p.avg) * p.qty : null, cost: p.qty && p.avg ? p.qty * p.avg : null };
  });
  const sum = k => rows.reduce((a, r) => a + (r[k] || 0), 0);
  const value = sum('value'), day = sum('day'), pnl = sum('pnl'), cost = sum('cost');
  const money = x => `${x < 0 ? '−' : ''}₹${inr(Math.abs(x))}`;

  return html`
    <header class="head">
      <div class="eyebrow">Portfolio</div>
      <h1>${value ? money(value) : plural(pf.length, 'stock')}</h1>
      <p class="sub">${value ? html`Today <b class=${tone(day)}>${day >= 0 ? '+' : ''}${money(day)}</b>${cost ? html` · Overall <b class=${tone(pnl)}>${pnl >= 0 ? '+' : ''}${money(pnl)} (${pct(pnl / cost * 100)})</b>` : ''}`
        : 'Add quantities to see value and returns. Your list stays on this device.'}</p>
      <${SearchBox} cached=${new Set()} onPick=${add} action="Add" placeholder="Add a stock — name or symbol"/>
    </header>
    <section class="sec">
      <div class="list pf">
        <div class="pf-row pf-head"><span>Stock</span><span class="r">Price</span><span class="r">Today</span><span class="r">Qty</span><span class="r">Avg cost</span><span class="r">Value</span><span class="r">Return</span><span></span></div>
        ${rows.map(r => html`<div class="pf-row" key=${r.symbol}>
          <a class="pf-name" href="#brief/${r.symbol}" title="See ${r.symbol}'s news"><b>${r.symbol}</b><small>${r.name}</small></a>
          <span class="r num pf-price">${pending[r.symbol] === 'building' ? html`<span class="spin"></span>` : pending[r.symbol] === 'failed' ? html`<span class="faint">unavailable</span>` : r.last ? `₹${inr(r.last, 1)}` : '—'}</span>
          <span class="r num pf-chg ${tone(r.chg)}">${pct(r.chg)}</span>
          <input class="pf-in pf-qty num" inputmode="decimal" placeholder="Qty" aria-label="Quantity of ${r.symbol}" value=${r.qty ?? ''} onChange=${e => edit(r.symbol, 'qty', e.target.value)}/>
          <input class="pf-in pf-avg num" inputmode="decimal" placeholder="Avg ₹" aria-label="Average buy price of ${r.symbol}" value=${r.avg ?? ''} onChange=${e => edit(r.symbol, 'avg', e.target.value)}/>
          <span class="r num pf-val">${r.value ? money(r.value) : ''}</span>
          <span class="r num pf-ret ${tone(r.pnl)}">${r.pnl != null ? pct(r.pnl / r.cost * 100) : ''}</span>
          <button class="icon-btn" title="Remove ${r.symbol}" aria-label="Remove ${r.symbol}" onClick=${() => remove(r.symbol)}><${Icon} d=${I.x} size=${16}/></button>
        </div>`)}
      </div>
      <div style="padding:14px 4px"><button class="quiet-link" onClick=${() => { const prev = pf; setPf((feed.default_portfolio || []).map(symbol => ({ symbol }))); flash('Reset to the sample portfolio', () => setPf(prev)); }}>Reset to sample portfolio</button></div>
    </section>`;
}

function Upcoming({ items, limit = 6 }) {
  if (!items?.length) return null;
  const today = new Date().toISOString().slice(0, 10);
  return html`<div class="panel">
    <div class="panel-h"><h3>Coming up</h3></div>
    <ul class="upc">${items.slice(0, limit).map(u => { const d = new Date(u.date + 'T00:00:00'); return html`<li title=${u.detail || ''}>
      <span class="dd"><b>${d.getDate()}</b>${d.toLocaleDateString('en-IN', { month: 'short' })}</span>
      <span style="min-width:0"><b class="s">${u.symbol}</b>${u.date === today ? html` <span class="chip-today">Today</span>` : ''}<div class="tt">${u.title}</div></span>
    </li>`; })}</ul>
  </div>`;
}

/* Telegram: one-tap connect. The device holds a random link code; tapping Start in Telegram links the
   chat to it, and this device's portfolio + rules sync to the server whenever they change. */
const tgCode = () => {
  let c = store.get('tgcode', null);
  if (!c) { c = Array.from(crypto.getRandomValues(new Uint8Array(18)), b => 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'[b % 57]).join(''); store.set('tgcode', c); }
  return c;
};

function useTelegram(rules, pf) {
  const [st, setSt] = useState(null);       // {available, bot, connected, name}
  const [waiting, setWaiting] = useState(false);
  const refresh = () => fetch(`/api/telegram/status?code=${tgCode()}`).then(r => r.json()).then(setSt).catch(() => setSt({ available: false }));
  useEffect(() => { refresh(); }, []);
  useEffect(() => {  // after "Connect", poll until the user taps Start in Telegram (max ~3 min)
    if (!waiting) return;
    let n = 0;
    const t = setInterval(async () => {
      const s = await fetch(`/api/telegram/status?code=${tgCode()}`).then(r => r.json()).catch(() => null);
      if (s?.connected || ++n > 60) { clearInterval(t); setWaiting(false); if (s) setSt(s); }
    }, 3000);
    return () => clearInterval(t);
  }, [waiting]);
  useEffect(() => {  // remember for the app-wide sync; push this device's portfolio + rules on (re)connect
    if (!st) return;
    store.set('tgconnected', !!st.connected);
    if (st.connected) syncTelegram(rules, pf);
  }, [st?.connected]);
  const connect = () => { window.open(`https://t.me/${st.bot}?start=${tgCode()}`, '_blank', 'noopener'); setWaiting(true); };
  return { st, waiting, connect, refresh, setSt };
}

function syncTelegram(rules, pf) {
  if (!store.get('tgconnected', false) || !pf || !rules) return;
  clearTimeout(syncTelegram.t);
  syncTelegram.t = setTimeout(() => fetch('/api/telegram/prefs', { method: 'PUT', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ code: tgCode(), symbols: pf.map(p => p.symbol), rules }) }).catch(() => {}), 800);
}

const TG_ICON = 'M21 4L3 11l5.5 2M21 4l-3 15-7.5-6M21 4L8.5 13m0 0V19l3-3.5';

function TelegramPanel({ rules, pf, compact }) {
  const { st, waiting, connect, setSt } = useTelegram(rules, pf);
  const [msg, setMsg] = useState(null), [busy, setBusy] = useState(false);
  if (!st?.available) return null;  // the server has no bot configured: show nothing rather than a dead end
  const post = async (url, ok) => {
    setBusy(true); setMsg(null);
    const r = await fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ code: tgCode() }) }).catch(() => null);
    setBusy(false);
    if (r?.ok) setMsg({ ok: true, t: ok }); else setMsg({ ok: false, t: (await r?.json().catch(() => ({})))?.detail || "Couldn't reach Telegram" });
    return r?.ok;
  };
  if (st.connected && compact) return null;  // on the Brief it's only an invitation
  if (st.connected) return html`<div class="panel tg on">
    <div class="tg-head"><span class="tg-ic"><${Icon} d=${TG_ICON}/></span><div style="flex:1;min-width:0">
      <div class="tg-title">Telegram alerts</div><div class="tg-sub"><span class="ok">● Connected</span>${st.name ? ` as ${st.name}` : ''}</div></div></div>
    ${!compact && html`<p class="tg-text">After each market close you get one message with anything in your portfolio that crosses the rules below. Changes here sync automatically.</p>`}
    <div class="links" style="margin-top:10px">
      <button disabled=${busy} onClick=${() => post('/api/telegram/test', 'Sent — check Telegram')}>Send a test</button>
      ${!compact && html`<button class="quiet" disabled=${busy} onClick=${async () => { if (await post('/api/telegram/disconnect', 'Disconnected')) setSt({ ...st, connected: false }); }}>Disconnect</button>`}
    </div>
    ${msg && html`<div class=${msg.ok ? 'ok' : 'no'} style="font-size:13px;margin-top:8px">${msg.t}</div>`}
  </div>`;
  return html`<div class="panel tg">
    <div class="tg-head"><span class="tg-ic"><${Icon} d=${TG_ICON}/></span><div style="flex:1;min-width:0">
      <div class="tg-title">Get alerts on Telegram</div><div class="tg-sub">One message after each market close. Free.</div></div></div>
    ${!compact && html`<p class="tg-text">Filings that need attention, unusual moves and tomorrow's events for your portfolio, using your rules.</p>`}
    <button class="tg-btn" onClick=${connect} disabled=${!st.bot}>${waiting ? html`<span class="spin"></span> Tap Start in Telegram…` : 'Connect Telegram'}</button>
    ${waiting && html`<div class="tg-sub" style="margin-top:8px">Telegram opened in a new tab. Tap <b>Start</b> there and this updates by itself.</div>`}
  </div>`;
}

/* ====================================================================== ALERTS */
const ALERT_CATS = ['results', 'M&A', 'management_change', 'fund_raise', 'litigation', 'credit_rating', 'pledging', 'capex', 'insider_trading', 'dividend', 'bonus_split', 'press_release'];

function RulesPanel({ rules, setRules, nPx, nAnn, days }) {
  const set = (k, v) => setRules({ ...rules, [k]: v });
  const toggle = c => set('categories', rules.categories.includes(c) ? rules.categories.filter(x => x !== c) : [...rules.categories, c]);
  const total = nPx + nAnn;
  return html`<div class="panel">
    <div class="panel-h"><h3>Rules</h3></div>
    <div class="rule">
      <div class="rule-top"><span class="lbl">Volume spike</span><span class="val num">${rules.volume_multiple.toFixed(1)}×</span></div>
      <div class="hint">A day's volume vs its ${rules.volume_lookback_days}-day average</div>
      <input type="range" min="1.5" max="5" step="0.1" value=${rules.volume_multiple} aria-label="Volume multiple" onInput=${e => set('volume_multiple', +e.target.value)}/>
    </div>
    <div class="rule">
      <div class="rule-top"><span class="lbl">Price move</span><span class="val num">±${rules.price_move_pct.toFixed(1)}%</span></div>
      <div class="hint">Close-to-close in one session</div>
      <input type="range" min="1" max="12" step="0.5" value=${rules.price_move_pct} aria-label="Price move percent" onInput=${e => set('price_move_pct', +e.target.value)}/>
    </div>
    <div class="rule">
      <div class="rule-top"><span class="lbl">Filings I care about</span><span class="val num">${rules.categories.length}</span></div>
      <div class="hint">Alert when a material filing of these types lands</div>
      <div class="toggles">${ALERT_CATS.map(c => html`<button aria-pressed=${rules.categories.includes(c)} class=${rules.categories.includes(c) ? 'on' : ''} onClick=${() => toggle(c)}>${CAT_LABEL[c]}</button>`)}</div>
    </div>
    <p class="verdict-line"><b>${total}</b> alerts in 30 days (${nPx} price/volume on ${plural(days, 'day')}, ${nAnn} filings) — ${total > 40 ? 'noisy; tighten a threshold.' : total < 3 ? 'very quiet; you may miss things.' : 'a sensible signal-to-noise ratio.'}</p>
  </div>`;
}

function Alerts({ feed, asOf, rules, setRules, pf }) {
  const since = asOf - 30 * DAY;
  const px = linkCauses(priceAlerts(feed.holdings, rules, since), feed.events);
  const ann = feed.events.filter(e => rules.categories.includes(e.category) && e.score >= 60 && new Date(e.ts) >= since);
  const all = [...px, ...ann.map(e => ({ ...e, kind: 'filing' }))].sort((a, b) => new Date(b.ts) - new Date(a.ts));
  const byDay = all.slice(0, 80).reduce((m, e) => { const k = fmtDay(e.ts); (m[k] = m[k] || []).push(e); return m; }, {});
  const panel = html`<${RulesPanel} rules=${rules} setRules=${setRules} nPx=${px.length} nAnn=${ann.length} days=${new Set(px.map(a => a.ts.slice(0, 10))).size}/>`;
  return html`
    <header class="head">
      <div class="eyebrow">Last 30 days</div>
      <h1>You decide what's unusual.</h1>
      <p class="sub">FINN checks all ${feed.holdings.length} holdings after every close and flags anything that crosses a line you set. Move a slider and the list below updates instantly.</p>
    </header>
    <div class="cols">
      <div>
        <div class="rail-mobile sec"><${TelegramPanel} rules=${rules} pf=${pf}/>${panel}</div>
        <section class="sec">
          ${all.length ? Object.entries(byDay).map(([d, list]) => html`<div class="day-h">${d}</div>
            <div class="list">${list.map(e => html`<${Row} key=${e.id} e=${e} asOf=${asOf} onDone=${() => {}}/>`)}</div>`)
            : html`<div class="empty">Nothing crossed your lines in 30 days.</div>`}
        </section>
      </div>
      <aside class="rail"><${TelegramPanel} rules=${rules} pf=${pf}/>${panel}</aside>
    </div>`;
}

/* ====================================================================== company search (type-ahead) */
let SYMBOLS = null;
const loadSymbols = () => SYMBOLS ? Promise.resolve(SYMBOLS)
  : fetch('/symbols.json').then(r => r.json()).then(rows => (SYMBOLS = rows.map(([s, n, rank, alias]) => ({
      s, n, rank, alias, sl: s.toLowerCase(), nl: n.toLowerCase(), words: (n + ' ' + alias).toLowerCase().split(/[\s&.\-()]+/) }))));

function matchCompanies(q, limit = 8) {
  const t = q.trim().toLowerCase();
  if (!SYMBOLS || !t) return [];
  const out = [];
  for (const c of SYMBOLS) {
    let sc = 0;
    if (c.sl === t) sc = 120;
    else if (c.sl.startsWith(t)) sc = 100 - (c.sl.length - t.length);
    else if (c.nl.startsWith(t)) sc = 90;
    else if (c.words.some(w => w.startsWith(t))) sc = 75;
    else if (t.length >= 3 && (c.nl.includes(t) || c.alias.includes(t))) sc = 50;
    if (sc) out.push([sc + c.rank * 6, c]);
  }
  return out.sort((a, b) => b[0] - a[0] || a[1].s.length - b[1].s.length).slice(0, limit).map(x => x[1]);
}

function SearchBox({ initial, cached, running, onPick, autofocus, action = 'Analyse', placeholder = 'Search any NSE company — Zomato, INFY, Tata…' }) {
  const [q, setQ] = useState(initial || '');
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const [, setReady] = useState(!!SYMBOLS);
  const ref = useRef(null);
  useEffect(() => { loadSymbols().then(() => setReady(true)); if (autofocus && matchMedia('(min-width: 900px)').matches) ref.current?.focus(); }, []);
  useEffect(() => { setQ(initial || ''); }, [initial]);
  const hits = open ? matchCompanies(q) : [];
  const choose = c => { setOpen(false); setQ(action === 'Add' ? '' : c.s); onPick(c.s); };
  const submit = e => {
    e.preventDefault();
    if (hits[hi]) return choose(hits[hi]);
    const s = q.trim().toUpperCase(); if (s) { setOpen(false); if (action === 'Add') setQ(''); onPick(s); }
  };
  const key = e => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setHi(h => Math.min(h + 1, hits.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setHi(h => Math.max(h - 1, 0)); }
    else if (e.key === 'Escape') setOpen(false);
  };
  const mark = (text, t) => {
    const s = t.trim(), i = text.toLowerCase().indexOf(s.toLowerCase());
    return i < 0 || !s ? text : html`${text.slice(0, i)}<b>${text.slice(i, i + s.length)}</b>${text.slice(i + s.length)}`;
  };
  return html`<form class="search" onSubmit=${submit} role="search">
    <div class="field"><${Icon} d=${I.research}/>
      <input ref=${ref} value=${q} placeholder=${placeholder} autocomplete="off" spellcheck="false"
        role="combobox" aria-expanded=${hits.length > 0} aria-autocomplete="list" aria-label="Search companies"
        onInput=${e => { setQ(e.target.value); setOpen(true); setHi(0); }} onFocus=${() => setOpen(true)}
        onBlur=${() => setTimeout(() => setOpen(false), 150)} onKeyDown=${key}/></div>
    <button class="go" type="submit">${running ? html`<span class="spin"></span>` : action}</button>
    ${hits.length > 0 && html`<ul class="suggest" role="listbox">${hits.map((c, i) => html`
      <li role="option" aria-selected=${i === hi} class=${i === hi ? 'hi' : ''} onMouseDown=${e => { e.preventDefault(); choose(c); }}>
        <span class="s">${mark(c.s, q)}</span><span class="n">${mark(c.n, q)}</span>
        ${cached.has(c.s) ? html`<span class="tag">● instant</span>` : ''}
      </li>`)}</ul>`}
  </form>`;
}

/* ====================================================================== RESEARCH */
function Research({ sym, status, feed, onDone }) {
  const [steps, setSteps] = useState([]);
  const [dossier, setDossier] = useState(null);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    if (!sym) { setDossier(null); setSteps([]); return; }
    store.set('lastSym', sym);
    setDossier(null); setSteps([]); setRunning(true);
    const es = new EventSource(`/api/research/${encodeURIComponent(sym)}/stream`);
    es.onmessage = m => {
      const ev = JSON.parse(m.data);
      if (ev.type === 'done') { setDossier(ev.dossier); setRunning(false); es.close(); onDone?.(); }
      else if (ev.type === 'partial') setDossier({ ...ev.dossier, pending: true });
      else if (ev.type === 'error') { setSteps(s => [...s, ev]); setRunning(false); es.close(); }
      else setSteps(s => [...s, ev]);
    };
    es.onerror = () => {  // offline or server hiccup: fall back to the last saved dossier
      es.close();
      fetch(`/api/research/${encodeURIComponent(sym)}`).then(r => r.ok ? r.json() : null).then(d => {
        if (d) setDossier({ ...d, offline: true });
        else setSteps(s => [...s, { type: 'error', text: "Couldn't reach FINN's server. Check your connection." }]);
        setRunning(false);
      }).catch(() => { setSteps(s => [...s, { type: 'error', text: "You're offline and this company isn't saved yet." }]); setRunning(false); });
    };
    return () => es.close();
  }, [sym]);

  const cached = new Set(status?.cached || []);
  const ready = status?.ready || [];
  const holdingsReady = new Set(feed.holdings.map(h => h.symbol));
  const agent = html`<div class="agent">
    ${steps.map((s, i) => html`<div class="step ${s.type === 'warn' || s.type === 'error' ? 'warn' : i === steps.length - 1 && running ? 'live' : ''}">
      <span class="ic">${s.type === 'warn' || s.type === 'error' ? '!' : i === steps.length - 1 && running ? html`<span class="spin"></span>` : '✓'}</span>${s.text}</div>`)}
    ${running && steps.length === 0 && html`<div class="step live"><span class="ic"><span class="spin"></span></span>Starting…</div>`}
  </div>`;

  return html`
    <header class="head">
      <div class="eyebrow">Research</div>
      ${!sym && html`<h1>Did they do what they said?</h1>
        <p class="sub">FINN reads a company's last four earnings calls and its annual report, lists every promise management made, and checks each one against the reported numbers.</p>`}
      <${SearchBox} initial=${sym} cached=${cached} running=${running} onPick=${s => go(`research/${s}`)} autofocus=${!sym}/>
    </header>
    ${!sym && html`
      <section class="sec">
        <div class="sec-h"><h2>Recently analysed</h2></div>
        <div class="ready-grid">${[...ready].sort((a, b) => holdingsReady.has(b.symbol) - holdingsReady.has(a.symbol) || (b.score ?? -1) - (a.score ?? -1)).map(r => html`
          <button class="rcard" onClick=${() => go(`research/${r.symbol}`)}>
            <${Ring} score=${r.score} size=${44}/>
            <div style="min-width:0;flex:1"><div class="nm">${r.name || r.symbol}</div>
              <div class="sy">${r.symbol}${holdingsReady.has(r.symbol) ? ' · Holding' : ''}</div>
              <div class="tl">${r.promises ? html`<span class="up">${r.delivered} kept</span> · <span class=${r.missed ? 'down' : ''}>${r.missed} broken</span>` : 'Financials only'}</div></div>
          </button>`)}</div>
      </section>
      `}
    ${sym && !dossier && html`<div class="sec"><div class="panel">${agent}</div></div>`}
    ${dossier && html`<${Dossier} d=${dossier} agent=${dossier.pending ? agent : null}/>`}
  `;
}

const VERDICT = { delivered: 'Delivered', partly: 'Partly', missed: 'Missed', walked_back: 'Walked back', too_early: 'Too early' };
const VCOLOR = { delivered: 'var(--up)', partly: 'var(--warn)', missed: 'var(--down)', walked_back: 'var(--down)', too_early: 'var(--faint)' };

function Dossier({ d, agent }) {
  const n = d.numbers, s = n.snapshot, L = d.ledger;
  const qs = n.quarters, ys = n.years, lastQ = qs[qs.length - 1] || {};
  const order = Object.keys(VERDICT);
  const rows = L ? [...L.promise_ledger].sort((a, b) => order.indexOf(a.verdict) - order.indexOf(b.verdict)) : [];
  const tally = rows.reduce((m, r) => (m[r.verdict] = (m[r.verdict] || 0) + 1, m), {});
  const ccc = n.working_capital['Cash Conversion Cycle'] || n.working_capital['Working Capital Days'];
  const segCols = ['var(--accent)', 'var(--up)', 'var(--warn)', '#b48cff', '#5fc8d8', 'var(--down)', 'var(--faint)'];

  return html`
    <div class="dhead">
      <div class="eyebrow">${d.symbol} · NSE</div>
      <h1>${d.name}</h1>
      ${d.business?.what_it_does && html`<p class="lede">${d.business.what_it_does}</p>`}
      <div class="kpis num">
        <div class="kpi"><div class="k">Market cap</div><div class="v">${s.market_cap_cr ? cr(s.market_cap_cr) : '—'}</div></div>
        <div class="kpi"><div class="k">P/E</div><div class="v">${s.pe ?? '—'}</div></div>
        <div class="kpi"><div class="k">${s.is_financial ? 'ROE' : 'ROCE'}</div><div class="v">${(s.is_financial ? s.roe : s.roce) ?? '—'}%</div></div>
        ${!s.is_financial && html`<div class="kpi"><div class="k">Debt / equity</div><div class="v">${s.de ?? '—'}</div></div>`}
        <div class="kpi"><div class="k">Last quarter revenue</div><div class="v">${inr(lastQ.revenue)} <span class=${tone(lastQ.rev_yoy)} style="font-size:14px">${pct(lastQ.rev_yoy, 0)}</span></div></div>
      </div>
    </div>
    ${d.offline && html`<div class="note">Offline — showing the copy saved on ${fmtDate(d.generated_at)}.</div>`}
    ${d.note && html`<div class="note">${d.note}</div>`}

    <div class="dgrid">
      <div class="primary-col">
        ${agent && html`<section class="sec"><div class="sec-h"><h2>Promise ledger</h2><span class="count">reading the filings…</span></div>
          <div class="panel">${agent}<p class="faint" style="font-size:13px;margin-top:8px">First analysis of this company takes about a minute. Financials are ready below.</p></div></section>`}

        ${L && html`<section class="sec">
          <div class="sec-h"><h2>Promise ledger</h2><span class="count">${d.promise_source === 'annual_report' ? `from ${d.calls[0].quarter} outlook — no calls held` : `last ${plural(d.calls.length, 'call')}`}</span></div>
          <div class="panel">
            <div class="cred"><${Ring} score=${L.credibility?.score}/><div class="line">${L.credibility?.line}</div></div>
            <div class="tally">${order.filter(k => tally[k]).map(k => html`<span style="flex:${tally[k]};background:${VCOLOR[k]}"></span>`)}</div>
            <div class="legend">${order.filter(k => tally[k]).map(k => html`<span><span class="dot" style="background:${VCOLOR[k]}"></span>${tally[k]} ${VERDICT[k].toLowerCase()}</span>`)}</div>
          </div>
          <div class="panel ledger">${rows.map(r => html`<${LedgerRow} r=${r}/>`)}</div>
        </section>`}

        ${L?.narrative_vs_numbers?.length > 0 && html`<section class="sec">
          <div class="sec-h"><h2>Story vs numbers</h2></div>
          <div class="panel"><ul class="nvn">${L.narrative_vs_numbers.map(x => html`<li>
            <span class="ic" style="background:${x.agree ? 'var(--up-soft)' : 'var(--down-soft)'};color:${x.agree ? 'var(--up)' : 'var(--down)'}">${x.agree ? '✓' : '✗'}</span><span>${x.point}</span></li>`)}</ul></div>
        </section>`}

        ${L && html`<section class="sec">
          <div class="sec-h"><h2>Bull vs bear</h2></div>
          <div class="bb">
            <div class="panel"><h3 class="up"><span class="dot" style="background:var(--up)"></span>Bull case</h3><ul>${(L.bull || []).map(x => html`<li>${x}</li>`)}</ul></div>
            <div class="panel"><h3 class="down"><span class="dot" style="background:var(--down)"></span>Bear case</h3><ul>${(L.bear || []).map(x => html`<li>${x}</li>`)}</ul></div>
          </div>
          ${L.watch?.length > 0 && html`<div class="panel" style="margin-top:10px"><h3 style="font-size:13px;font-weight:650;color:var(--muted);margin-bottom:6px">Check next quarter</h3>
            <ul class="flags">${L.watch.map(x => html`<li>${x}</li>`)}</ul></div>`}
        </section>`}

        ${!agent && d.calls.length > 0 && html`<${AskBox} sym=${d.symbol}/>`}
      </div>

      <div class="side">
        <section class="sec">
          <div class="sec-h"><h2>The numbers</h2><span class="count">reported · ₹ crore</span></div>
          <div class="charts num">
            <div class="chart"><div class="t">Quarterly revenue</div><div class="v">${inr(lastQ.revenue)} <small class=${tone(lastQ.rev_yoy)}>${pct(lastQ.rev_yoy, 0)} YoY</small></div>
              <${Bars} values=${qs.map(x => x.revenue)}/><div class="ax"><span>${qs[0]?.q}</span><span>${lastQ.q}</span></div></div>
            ${!s.is_financial ? html`<div class="chart"><div class="t">Operating margin</div><div class="v">${lastQ.margin ?? '—'}%${lastQ.ebit_margin != null ? html` <small class="faint">EBIT ${lastQ.ebit_margin}%</small>` : ''}</div>
              <${Line} values=${qs.map(x => x.margin)}/><div class="ax"><span>${qs[0]?.q}</span><span>${lastQ.q}</span></div></div>`
              : html`<div class="chart"><div class="t">Quarterly profit</div><div class="v">${inr(lastQ.pat)} <small class=${tone(lastQ.pat_yoy)}>${pct(lastQ.pat_yoy, 0)}</small></div>
              <${Bars} values=${qs.map(x => x.pat)} color="var(--up)"/><div class="ax"><span>${qs[0]?.q}</span><span>${lastQ.q}</span></div></div>`}
            <div class="chart"><div class="t">Annual profit${n.trajectory.pat_cagr_3y != null ? ` · ${n.trajectory.pat_cagr_3y}% 3y CAGR` : ''}</div><div class="v">${inr(ys[ys.length - 1]?.pat)}</div>
              <${Bars} values=${ys.map(x => x.pat)} color="var(--up)"/><div class="ax"><span>${ys[0]?.y}</span><span>${ys[ys.length - 1]?.y}</span></div></div>
            ${!s.is_financial && n.cash.length > 0 ? html`<div class="chart"><div class="t">Cash vs profit</div><div class="v">${n.cash_conv_5y != null ? `${Math.round(n.cash_conv_5y * 100)}%` : '—'} <small class="faint">5y conversion</small></div>
              <${Bars} values=${n.cash.map(x => x.cfo)} pair=${n.cash.map(x => x.pat)}/><div class="ax"><span>■ cash</span><span class="faint">■ profit</span></div></div>`
              : html`<div class="chart"><div class="t">Revenue · 5y CAGR</div><div class="v">${n.trajectory.rev_cagr_5y ?? '—'}%</div>
              <${Bars} values=${ys.map(x => x.revenue)}/><div class="ax"><span>${ys[0]?.y}</span><span>${ys[ys.length - 1]?.y}</span></div></div>`}
            ${!s.is_financial && n.balance_sheet.length > 1 && html`<div class="chart"><div class="t">Debt / equity</div><div class="v">${s.de ?? '—'}</div>
              <${Line} values=${n.balance_sheet.map(x => x.de)} color="var(--warn)"/><div class="ax"><span>${n.balance_sheet[0].y}</span><span>${n.balance_sheet.at(-1).y}</span></div></div>`}
            ${ccc && !s.is_financial && html`<div class="chart"><div class="t">Cash cycle, days</div><div class="v">${ccc.at(-1)[1] ?? '—'}</div>
              <${Line} values=${ccc.map(x => x[1])} color="var(--warn)"/><div class="ax"><span>${ccc[0][0]}</span><span>${ccc.at(-1)[0]}</span></div></div>`}
          </div>
          ${n.flags.length > 0 && html`<div class="panel" style="margin-top:10px"><ul class="flags">${n.flags.map(f => html`<li class=${f.tone}>${f.text}</li>`)}</ul></div>`}
        </section>

        ${d.business?.segments?.length > 0 && html`<section class="sec">
          <div class="sec-h"><h2>Business mix</h2><span class="count">annual report ${d.business.year}</span></div>
          <div class="panel">
            ${d.business.segments.filter(x => x.share_pct).length > 1 && html`<div class="segbar">${d.business.segments.filter(x => x.share_pct).map((x, i) => html`<span style="flex:${x.share_pct};background:${segCols[i % segCols.length]}"></span>`)}</div>`}
            <ul class="seglist">${d.business.segments.map((x, i) => html`<li><span class="sw" style="background:${segCols[i % segCols.length]}"></span>
              <span><b>${x.name}</b>${x.what ? html` <span class="faint">${x.what}</span>` : ''}</span><span class="p num">${x.share_pct ? x.share_pct + '%' : ''}</span></li>`)}</ul>
            ${d.business.mix_note && html`<div class="fine">${d.business.mix_note}</div>`}
            ${d.business.risks_mgmt_flags?.length > 0 && html`<div class="fine">Risks management names: ${d.business.risks_mgmt_flags.join(' · ')}</div>`}
          </div>
        </section>`}

        ${!agent && html`<${Sources} d=${d}/>`}
      </div>
    </div>
  `;
}

function Sources({ d }) {
  const docs = [...d.sources].reverse();  // newest first
  const kind = x => x.kind === 'transcript' ? ['Earnings call transcript', I.mic] : ['Annual report', I.doc];
  const c = d.cost;
  const built = new Date(d.generated_at).toLocaleString('en-IN', { day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit' });
  return html`<section class="sec">
    <div class="sec-h"><h2>Sources</h2></div>
    <div class="panel">
      <ul class="docs">
        ${docs.map(x => { const [k, ic] = kind(x); return html`<li><a href=${x.url} target="_blank" rel="noopener">
          <span class="di"><${Icon} d=${ic}/></span>
          <span><div class="dt">${x.label.replace(' call', '').replace(' (outlook)', '')}</div><div class="ds">${k}</div></span>
          <span class="arrow"><${Icon} d=${I.ext} size=${15}/></span></a></li>`; })}
        <li><a href="https://www.screener.in/company/${encodeURIComponent(d.symbol)}/${d.basis === 'consolidated' ? 'consolidated/' : ''}" target="_blank" rel="noopener">
          <span class="di"><${Icon} d=${I.brief}/></span>
          <span><div class="dt">Reported financials</div><div class="ds">${d.basis === 'consolidated' ? 'Consolidated' : 'Standalone'} statements</div></span>
          <span class="arrow"><${Icon} d=${I.ext} size=${15}/></span></a></li>
      </ul>
      ${c && html`<details class="about"><summary>About this analysis</summary><dl>
        <dt>Updated</dt><dd>${built}</dd>
        <dt>Method</dt><dd>Promises extracted from primary documents; every quote string-matched to its page; verdicts checked against reported figures.</dd>
        <dt>Models</dt><dd>${c.models.join(', ') || 'None — figures only'}${d.ledger_fallback ? ' (fallback; upgrades on the next run)' : ''}</dd>
        <dt>Usage</dt><dd>${plural(c.calls, 'call')} · ${inr(c.tokens_in)} input tokens · free tier</dd>
      </dl></details>`}
    </div>
  </section>`;
}

function LedgerRow({ r }) {
  const [open, setOpen] = useState(false);
  return html`<button class="lrow ${open ? 'open' : ''}" onClick=${() => setOpen(!open)} aria-expanded=${open}>
    <span class="bar" style="background:${VCOLOR[r.verdict] || 'var(--faint)'}"></span>
    <span>
      <span class="vd" style="color:${VCOLOR[r.verdict] || 'var(--faint)'}">${VERDICT[r.verdict] || r.verdict}<span class="when">promised ${r.made_in}</span></span>
      <div class="p">${r.promise}</div>
      <div class="d">${r.delivered}</div>
      ${open && html`<div>
        ${r.quote && html`<blockquote>“${r.quote}”</blockquote>`}
        <div class="src">
          ${r.verified === true ? html`<span class="ok">✓ quote found in the transcript</span>` : r.verified === false ? html`<span class="no">⚠ quote not matched word-for-word</span>` : html`<span>scanned transcript · read visually</span>`}
          <a href="${r.url}#page=${r.page || 1}" target="_blank" rel="noopener" onClick=${e => e.stopPropagation()}>Open page ${r.page || ''} ↗</a>
          ${r.checked_against && html`<span>checked against ${r.checked_against}</span>`}
        </div>
      </div>`}
    </span>
    <span class="chev">›</span>
  </button>`;
}

function AskBox({ sym }) {
  const [q, setQ] = useState(''), [a, setA] = useState(null), [busy, setBusy] = useState(false);
  const ask = async text => {
    const question = (text ?? q).trim(); if (!question) return;
    setQ(question); setBusy(true); setA(null);
    try {
      const r = await fetch('/api/ask', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ symbol: sym, question }) });
      setA(r.ok ? await r.json() : { answer: (await r.json()).detail || 'Something went wrong.' });
    } catch { setA({ answer: 'Network error.' }); }
    setBusy(false);
  };
  return html`<section class="sec">
    <div class="sec-h"><h2>Ask the filings</h2><span class="count">answers cite the page</span></div>
    <div class="panel">
      <form class="ask" onSubmit=${e => { e.preventDefault(); ask(); }}>
        <input value=${q} placeholder="What did management say about…" aria-label="Ask a question about this company" onInput=${e => setQ(e.target.value)}/>
        <button class="btn primary">${busy ? html`<span class="spin"></span>` : 'Ask'}</button>
      </form>
      ${!a && !busy && html`<div class="qs">${['margin outlook', 'capex plans', 'debt', 'biggest risk', 'pricing'].map(x => html`<button onClick=${() => ask(`What did management say about ${x}?`)}>${x}</button>`)}</div>`}
      ${a && html`<div class="answer">${a.answer}</div>
        ${a.cites?.length > 0 && html`<div class="cites">${a.cites.map(c => html`<a href="${c.url}#page=${c.page}" target="_blank" rel="noopener">${c.label} ↗</a>`)}</div>`}`}
    </div>
  </section>`;
}

render(html`<${App}/>`, document.getElementById('app'));
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
