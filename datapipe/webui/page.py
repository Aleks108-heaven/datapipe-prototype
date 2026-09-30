"""The single-page review UI. Everything (HTML, CSS, JS) is inline so a strict CSP can forbid all other sources.

Rule for the JavaScript below: untrusted text (column names, provider rationale, notes, file names) is only ever
put into the page with textContent / createTextNode. There is no innerHTML, no eval, no javascript: URLs.
"""

_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="csrf" content="{{CSRF}}">
<meta name="fixed-reviewer" content="{{FIXED}}">
<title>Mapping Review</title>
<style nonce="{{NONCE}}">
:root{
  --bg:#f6f6f3; --surface:#ffffff; --text:#1b1b19; --muted:#63635d; --line:#dcdcd4;
  --accent:#2757d6; --accent-ink:#ffffff;
  --ok:#17692f; --ok-bg:#e4f3e8; --warn:#8a5200; --warn-bg:#fff1d0; --bad:#a8231b; --bad-bg:#fce6e3;
  --radius:12px; --r-sm:8px; --r-md:10px; --r-pill:999px;
  --tap:44px; --tap-sm:40px; --s2:8px; --s3:12px; --fs-xs:.8rem; --fs-sm:.85rem; --fs-md:.9rem; --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#131312; --surface:#1d1d1b; --text:#ecece7; --muted:#a4a49c; --line:#383832;
    --accent:#7ea3ff; --accent-ink:#0d1526;
    --ok:#77d296; --ok-bg:#15301e; --warn:#f2bd5f; --warn-bg:#33270e; --bad:#ff9087; --bad-bg:#3b1a17;
  }
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header.top{display:flex;align-items:center;gap:var(--s3);padding:14px 16px;border-bottom:1px solid var(--line);background:var(--surface)}
header.top h1{font-size:1.05rem;margin:0;font-weight:650}
header.top .sub{color:var(--muted);font-size:var(--fs-sm)}
main{max-width:920px;margin:0 auto;padding:16px 16px 48px}
a,button{font:inherit}
button.back{background:none;border:0;color:var(--accent);display:inline-block;padding:6px 0;cursor:pointer;text-align:left}
.sr-only{position:absolute;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
h2[tabindex="-1"]:focus{outline:none}
button:focus-visible,input:focus-visible,textarea:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
h2{font-size:1.15rem;margin:28px 0 10px}
h3{font-size:1rem;margin:0}
.muted{color:var(--muted)}
.small{font-size:var(--fs-sm)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:14px 16px;margin:0 0 12px}
.card.click{cursor:pointer}
.card.click:hover{border-color:var(--accent)}
.row{display:flex;flex-wrap:wrap;gap:var(--s2);align-items:center}
.spread{justify-content:space-between}
.badge{display:inline-block;padding:2px 10px;border-radius:var(--r-pill);font-size:var(--fs-xs);font-weight:600;border:1px solid transparent}
.b-ok{background:var(--ok-bg);color:var(--ok)}
.b-warn{background:var(--warn-bg);color:var(--warn)}
.b-bad{background:var(--bad-bg);color:var(--bad)}
.b-neutral{background:transparent;color:var(--muted);border-color:var(--line)}
.chip{display:inline-block;padding:1px 9px;border-radius:var(--r-sm);border:1px solid var(--line);font-size:var(--fs-xs);color:var(--muted)}
.name{font-family:var(--mono);font-size:var(--fs-md);background:var(--bg);border:1px solid var(--line);border-radius:var(--r-sm);padding:2px 8px;overflow-wrap:anywhere;white-space:pre-wrap}
.arrow{color:var(--muted)}
.banner{border-radius:var(--radius);padding:12px 14px;margin:0 0 12px;border:1px solid transparent}
.banner.bad{background:var(--bad-bg);color:var(--bad);border-color:var(--bad)}
.banner.ok{background:var(--ok-bg);color:var(--ok);border-color:var(--ok)}
.banner.warn{background:var(--warn-bg);color:var(--warn);border-color:var(--warn)}
dl.meta{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;margin:0}
dl.meta dt{color:var(--muted)}
dl.meta dd{margin:0;overflow-wrap:anywhere}
@media (max-width:520px){dl.meta{grid-template-columns:1fr}dl.meta dt{margin-top:8px}}
details{margin:10px 0 0}
summary{cursor:pointer;color:var(--accent)}
pre{background:var(--bg);border:1px solid var(--line);border-radius:var(--r-sm);padding:10px;overflow:auto;max-height:320px;font:var(--fs-xs)/1.4 var(--mono);white-space:pre-wrap;overflow-wrap:anywhere}
.bar{height:6px;border-radius:3px;background:var(--line);overflow:hidden;min-width:80px;flex:1}
.bar>span{display:block;height:100%;background:var(--accent)}
.evidence{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 0}
ul.reasons{margin:8px 0 0;padding-left:20px;color:var(--warn)}
ul.reasons.rej{color:var(--bad)}
.quote{margin:8px 0 0;padding:6px 10px;border-left:3px solid var(--line);color:var(--muted);font-size:var(--fs-md);overflow-wrap:anywhere}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:var(--r-md);overflow:hidden;margin-top:10px}
.seg button{border:0;background:var(--surface);color:var(--text);padding:8px 18px;min-height:var(--tap);cursor:pointer}
.seg button+button{border-left:1px solid var(--line)}
.seg button[aria-checked=true]{background:var(--accent);color:var(--accent-ink);font-weight:600}
.seg button:disabled{cursor:not-allowed;opacity:.55}
.locked{margin-top:10px;color:var(--muted);font-size:var(--fs-md)}
.actionbar{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:14px 16px;margin:16px 0 0}
.actionbar .inner{display:grid;gap:10px}
@media (min-width:900px){
  main{padding-bottom:190px}
  .actionbar{position:fixed;left:0;right:0;bottom:0;border-radius:0;border-width:1px 0 0;margin:0;padding:10px 16px;z-index:5}
  .actionbar .inner{max-width:920px;margin:0 auto;gap:var(--s2)}
  .actionbar textarea{min-height:40px;height:40px}
}
.fields{display:grid;grid-template-columns:minmax(120px,220px) 1fr;gap:var(--s2)}
@media (max-width:620px){.fields{grid-template-columns:1fr}}
label.f{display:block;font-size:var(--fs-xs);color:var(--muted);margin-bottom:2px}
input,textarea{width:100%;padding:9px 10px;border-radius:var(--r-sm);border:1px solid var(--line);background:var(--bg);color:var(--text);font:inherit}
textarea{min-height:var(--tap);resize:vertical}
.btns{display:flex;flex-wrap:wrap;gap:var(--s2);align-items:center}
button.primary{background:var(--accent);color:var(--accent-ink);border:0;border-radius:var(--r-md);padding:10px 18px;min-height:var(--tap);font-weight:650;cursor:pointer}
button.secondary{background:transparent;color:var(--text);border:1px solid var(--line);border-radius:var(--r-md);padding:10px 16px;min-height:var(--tap);cursor:pointer}
button.danger{color:var(--bad);border-color:var(--bad)}
button:disabled{opacity:.5;cursor:not-allowed}
.why{color:var(--muted);font-size:var(--fs-sm);flex:1;min-width:180px}
.err{color:var(--bad);font-size:var(--fs-md)}
.cmd{font:var(--fs-sm)/1.4 var(--mono);background:var(--bg);border:1px solid var(--line);border-radius:var(--r-sm);padding:8px 10px;overflow-wrap:anywhere;white-space:pre-wrap}
.empty{text-align:center;padding:36px 12px;color:var(--muted)}
select{width:100%;padding:9px 10px;border-radius:var(--r-sm);border:1px solid var(--line);background:var(--bg);color:var(--text);font:inherit;min-height:42px}
select:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.change{display:grid;grid-template-columns:1fr minmax(170px,290px);gap:6px 14px;align-items:end;margin-top:12px;padding-top:12px;border-top:1px solid var(--line)}
.change .cur{font-size:var(--fs-md);overflow-wrap:anywhere}
.change .full{grid-column:1 / -1}
@media (max-width:760px){.change{grid-template-columns:1fr}}.badge.b-manual{background:var(--accent);color:var(--accent-ink)}
.superseded{margin-top:10px;color:var(--muted);font-size:var(--fs-md)}
button.small{padding:6px 12px;min-height:var(--tap-sm);font-size:var(--fs-sm)}
</style>
</head>
<body>
<header class="top"><h1>Mapping review</h1><span class="sub" id="whoami"></span></header>
<main id="app"></main>
<div id="status" class="sr-only" role="status" aria-live="polite"></div>
<script nonce="{{NONCE}}">
(function () {
  'use strict';
  var csrf = document.querySelector('meta[name=csrf]').content;
  var fixedReviewer = document.querySelector('meta[name=fixed-reviewer]').content;
  var app = document.getElementById('app');
  var ID_RE = /^[0-9a-f]{64}$/;

  // ---- tiny DOM helper: text is ALWAYS set as text, never parsed as HTML
  function h(tag, props) {
    var e = document.createElement(tag);
    var p = props || {};
    Object.keys(p).forEach(function (k) {
      var v = p[k];
      if (v === null || v === undefined || v === false) return;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = String(v);
      else if (k.indexOf('on') === 0) e.addEventListener(k.slice(2), v);
      else if (k === 'disabled' || k === 'hidden') e[k] = !!v;
      else e.setAttribute(k, String(v));
    });
    for (var i = 2; i < arguments.length; i++) append(e, arguments[i]);
    return e;
  }
  function append(parent, c) {
    if (c === null || c === undefined || c === false) return;
    if (Array.isArray(c)) { c.forEach(function (x) { append(parent, x); }); return; }
    parent.appendChild(c && c.nodeType ? c : document.createTextNode(String(c)));
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  var statusBox = document.getElementById('status');
  // Screen-reader feedback for a route change: announce it, name the tab, and move focus to the page heading.
  function arrived(title, heading, announcement) {
    document.title = title + ' – Mapping review';
    statusBox.textContent = announcement;
    if (heading) { heading.setAttribute('tabindex', '-1'); heading.focus({ preventScroll: true }); }
  }
  // Redraws rebuild the DOM, so remember which control had focus (by data-fid) and give it back afterwards.
  function focusId() { var a = document.activeElement; return a && a.getAttribute ? a.getAttribute('data-fid') : null; }
  function refocus(fid) {
    if (!fid) return false;
    var all = app.querySelectorAll('[data-fid]');
    for (var i = 0; i < all.length; i++) {
      if (all[i].getAttribute('data-fid') === fid && !all[i].disabled) { all[i].focus({ preventScroll: true }); return true; }
    }
    return false;
  }
  function pct(x) { return (x === null || x === undefined) ? 'n/a' : Math.round(x * 100) + '%'; }
  function when(iso) { var d = new Date(iso); return isNaN(d) ? String(iso || '') : d.toISOString().slice(0, 16).replace('T', ' ') + ' UTC'; }

  function api(path, opts) {
    var o = opts || {};
    var headers = { 'Accept': 'application/json' };
    if (o.body !== undefined) { headers['Content-Type'] = 'application/json'; headers['X-DataPipe-CSRF'] = csrf; }
    return fetch(path, { method: o.method || 'GET', credentials: 'same-origin', headers: headers,
                         body: o.body === undefined ? undefined : JSON.stringify(o.body) })
      .then(function (r) {
        return r.json().catch(function () { return null; }).then(function (body) {
          if (!r.ok) throw new Error((body && body.error) || ('Request failed (' + r.status + ')'));
          return body;
        });
      });
  }

  var STATE_BADGE = { pending: ['b-warn', 'Pending review'], approved: ['b-ok', 'Approved'], rejected: ['b-bad', 'Rejected'] };
  var STATUS_BADGE = { accepted: ['b-ok', 'Verified'], needs_review: ['b-warn', 'Needs your review'], rejected: ['b-bad', 'Rejected by verification'] };
  function egressText(mode) {
    if (mode === 'none') return 'Nothing left this machine (offline provider)';
    if (mode === 'shapes') return 'Column names and value shapes were sent to the LLM';
    if (mode === 'shapes+samples') return 'Column names, value shapes and a few sample values were sent to the LLM';
    return String(mode);
  }
  function badge(map, key) { var b = map[key] || ['b-neutral', String(key)]; return h('span', { class: 'badge ' + b[0], text: b[1] }); }

  // ------------------------------------------------------------------ list view
  function showList() {
    clear(app);
    app.appendChild(h('p', { class: 'muted', text: 'Loading proposals…' }));
    api('/api/proposals').then(function (data) {
      clear(app);
      if (data.reviewer_fixed) document.getElementById('whoami').textContent = 'reviewing as ' + data.reviewer_fixed;
      var listHeading = h('h2', { text: 'Mapping proposals' });
      app.appendChild(listHeading);
      var pendingCount = data.proposals.filter(function (p) { return p.state === 'pending'; }).length;
      arrived('Proposals', listHeading, data.proposals.length + ' proposals, ' + pendingCount + ' pending review');
      if (!data.proposals.length) {
        app.appendChild(h('div', { class: 'card empty' },
          h('p', { text: 'No proposals found.' }),
          h('p', { class: 'small', text: 'Create one with: datapipe map <file> --schema <schema.json>. Looking in: ' + data.mappings_dir })));
      }
      data.proposals.forEach(function (p) {
        var s = p.summary || {};
        var card = h('div', { class: 'card click', role: 'link', tabindex: '0' },
          h('div', { class: 'row spread' },
            h('h3', { text: p.source_name || '(unnamed file)' }), badge(STATE_BADGE, p.state)),
          h('div', { class: 'row small muted' },
            h('span', { class: 'chip', text: (s.accepted || 0) + ' verified' }),
            h('span', { class: 'chip', text: (s.needs_review || 0) + ' need review' }),
            h('span', { class: 'chip', text: (s.rejected || 0) + ' rejected' }),
            p.required_unmapped ? h('span', { class: 'badge b-bad', text: p.required_unmapped + ' required unmapped' }) : null,
            p.integrity_ok ? null : h('span', { class: 'badge b-bad', text: 'INTEGRITY FAILED' })),
          h('div', { class: 'small muted', text: 'Proposed by ' + p.actor + ' · ' + (p.provider || '?') + (p.model ? ' (' + p.model + ')' : '') + ' · policy ' + p.policy + ' · ' + when(p.created) }));
        var open = function () { location.hash = '#/p/' + p.id; };
        card.addEventListener('click', open);
        card.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); } });
        app.appendChild(card);
      });
      if (data.skipped.length) {
        app.appendChild(h('details', null, h('summary', { text: data.skipped.length + ' file(s) skipped' }),
          h('ul', null, data.skipped.map(function (x) { return h('li', { class: 'small', text: x.file + ': ' + x.error }); }))));
      }
    }).catch(showError);
  }

  function showError(e) {
    clear(app);
    app.appendChild(h('div', { class: 'banner bad', role: 'alert', text: e.message || String(e) }));
    app.appendChild(h('button', { type: 'button', class: 'back', onclick: function () { location.hash = '#/'; route(); }, text: '← Back to proposals' }));
    arrived('Error', null, 'Error: ' + (e.message || String(e)));
  }

  // ------------------------------------------------------------------ detail view
  function showDetail(id) {
    clear(app);
    app.appendChild(h('p', { class: 'muted', text: 'Loading…' }));
    api('/api/proposals/' + id).then(function (data) { buildDetail(id, data); }).catch(showError);
  }

  function buildDetail(id, data) {
    var p = data.proposal;
    var decided = data.state.state !== 'pending';
    var remap = data.manual_remap || { available: false, reason: '', columns: [] };
    var cols = (remap.columns && remap.columns.length) ? remap.columns : (p.source.columns || []);
    var st = { decisions: {}, manual: {}, remapErr: {}, checking: {}, result: null, busy: false, error: '', refocus: null };
    // One card per schema column. The order is fixed from the server's verdicts (not from the reviewer's clicks), so cards never
    // jump while you work: needs a decision / required but unmapped, then refused, then optional unmapped, then verified.
    var itemFor = {};
    p.items.forEach(function (i) { itemFor[i.target] = i; });
    var schemaCols = p.target_schema.columns.slice();
    p.items.forEach(function (i) {                                     // a proposed target the schema does not list must still be visible
      if (!schemaCols.some(function (c) { return c.name === i.target; })) schemaCols.push({ name: i.target, required: false });
    });
    var order = schemaCols.map(function (c, n) {
      var it = itemFor[c.name];
      var rank = it ? (it.status === 'rejected' ? 1 : (it.status === 'accepted' ? 3 : 0)) : (c.required ? 0 : 2);
      return { c: c, n: n, rank: rank };
    }).sort(function (a, b) { return a.rank - b.rank || a.n - b.n; });
    var reviewer = h('input', { id: 'reviewer', type: 'text', autocomplete: 'off', maxlength: '80',
                                placeholder: 'Your name', value: data.reviewer_fixed || '' });
    if (data.reviewer_fixed) { reviewer.value = data.reviewer_fixed; reviewer.readOnly = true; }
    var note = h('textarea', { id: 'note', maxlength: '500', placeholder: 'Why? (required for overrides and rejections)' });
    var approveBtn = h('button', { class: 'primary', id: 'approve', type: 'button', text: 'Approve and create schema' });
    var rejectBtn = h('button', { class: 'secondary danger', id: 'reject', type: 'button', text: 'Reject proposal' });
    var why = h('div', { class: 'why', id: 'why' });
    var errBox = h('div', { class: 'err', id: 'errbox', role: 'alert' });
    var itemsBox = h('div', { id: 'items' });
    var summaryBox = h('p', { class: 'small', id: 'summary' });
    var remapBox = h('div', { id: 'remap' });
    var bar = h('div', { class: 'actionbar' });

    function included(item) {
      if (st.manual[item.target]) return false;              // superseded by the reviewer's own mapping
      if (item.status === 'accepted') return st.decisions[item.target] !== false;
      if (item.status === 'needs_review') return st.decisions[item.target] === true;
      return false;
    }
    function overrides() {
      var inc = [], exc = [];
      p.items.forEach(function (i) {
        if (i.status === 'needs_review' && st.decisions[i.target] === true) inc.push(i.target);
        if (i.status === 'accepted' && st.decisions[i.target] === false) exc.push(i.target);
      });
      return { include: inc, exclude: exc };
    }
    function manualList() {
      return Object.keys(st.manual).map(function (t) { return { target: t, source: st.manual[t].source }; });
    }
    function usedBy(source, exceptTarget) {
      var owner = null;
      p.items.forEach(function (i) { if (i.target !== exceptTarget && included(i) && i.source === source) owner = i.target; });
      Object.keys(st.manual).forEach(function (t) { if (t !== exceptTarget && st.manual[t].source === source) owner = t; });
      return owner;
    }
    function uncovered() {
      var covered = {};
      p.items.forEach(function (i) { if (included(i)) covered[i.target] = true; });
      Object.keys(st.manual).forEach(function (t) { covered[t] = true; });
      return p.target_schema.columns.filter(function (c) { return c.required && !covered[c.name]; }).map(function (c) { return c.name; });
    }
    function problems() {
      var out = [];
      if (!data.integrity_ok) out.push('the proposal failed its integrity check');
      if (!reviewer.value.trim()) out.push('enter your name');
      var un = uncovered();
      if (un.length) out.push('required column(s) not mapped: ' + un.join(', '));
      var o = overrides();
      if ((o.include.length || o.exclude.length || manualList().length) && !note.value.trim()) out.push('add a note explaining your overrides and manual mappings');
      return out;
    }
    function updateBar() {
      if (decided || st.result) return;
      var pr = problems();
      approveBtn.disabled = st.busy || pr.length > 0;
      rejectBtn.disabled = st.busy || !reviewer.value.trim() || !note.value.trim() || !data.integrity_ok;
      var same = reviewer.value.trim() && reviewer.value.trim() === p.actor;
      var msg = pr.length ? 'To approve: ' + pr.join('; ') + '.' : 'Ready to approve.';
      var rej = [];
      if (!reviewer.value.trim()) rej.push('your name');
      if (!note.value.trim()) rej.push('a note');
      if (rej.length) msg += ' To reject, add ' + rej.join(' and ') + '.';
      if (same) msg += ' Note: the reviewer must be a different person than the proposer (' + p.actor + ').';
      why.textContent = msg;
      errBox.textContent = st.error;
    }

    // Two-option radio group: one tab stop (the checked option), arrow keys switch and move focus.
    function segGroup(target, included, locked, setTo) {
      function opt(label, value) {
        var on = included === value;
        return h('button', { type: 'button', role: 'radio', 'aria-checked': on ? 'true' : 'false', tabindex: on ? '0' : '-1',
                             'data-fid': 'seg:' + target + ':' + label, disabled: locked, onclick: setTo(value),
                             onkeydown: function (ev) {
                               if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].indexOf(ev.key) < 0) return;
                               ev.preventDefault();
                               st.refocus = 'seg:' + target + ':' + (value ? 'Exclude' : 'Include');
                               setTo(!value)();
                             }, text: label });
      }
      return h('div', { class: 'seg', role: 'radiogroup', 'aria-label': 'Decision for ' + target }, opt('Include', true), opt('Exclude', false));
    }

    function evidenceChips(e, withAlternatives) {
      var chips = h('div', { class: 'evidence' });
      chips.appendChild(h('span', { class: 'chip', text: 'Values fit target type: ' + pct(e.parse_rate) + ' of ' + e.non_null }));
      chips.appendChild(h('span', { class: 'chip', text: 'Distinct: ' + pct(e.distinct_ratio) }));
      chips.appendChild(h('span', { class: 'chip', text: 'Name similarity: ' + (e.name_score === undefined ? 'n/a' : Number(e.name_score).toFixed(2)) }));
      if (withAlternatives && e.alternatives && e.alternatives.length) chips.appendChild(h('span', { class: 'chip', text: 'Other columns that also fit: ' + e.alternatives.join(', ') }));
      return chips;
    }
    function nameRow(source, target, required, rightBadge) {
      return h('div', { class: 'row spread' },
        h('div', { class: 'row' },
          source === null ? null : h('span', { class: 'name', 'data-role': 'source', text: source }),
          source === null ? null : h('span', { class: 'arrow', 'aria-label': 'maps to', text: '→' }),
          h('span', { class: 'name', 'data-role': 'target', text: target }),
          required ? h('span', { class: 'badge b-neutral', text: 'required' }) : null),
        rightBadge);
    }

    // One card per schema column: the proposal and its evidence, the reviewer's decision, and the way to choose another source.
    function targetCard(c, n) {
      var item = itemFor[c.name], man = st.manual[c.name];
      var status = man ? 'manual' : (item ? item.status : 'unmapped');
      var card = h('div', { class: 'card', 'data-target': c.name, 'data-status': status });
      if (man) {
        card.appendChild(nameRow(man.source, c.name, c.required, h('span', { class: 'badge b-manual', text: 'Manual (by you)' })));
        card.appendChild(evidenceChips(man.evidence, false));
        if (man.evidence.warnings && man.evidence.warnings.length) {
          card.appendChild(h('ul', { class: 'reasons' }, man.evidence.warnings.map(function (w) { return h('li', { text: w }); })));
        }
        card.appendChild(h('div', { class: 'quote', text: 'Chosen by you and checked against the source file just now. A note is required when you approve.' }));
        if (item) card.appendChild(h('div', { class: 'superseded', 'data-role': 'superseded', text: 'This replaces the proposed mapping from ' + item.source + '.' }));
        if (!decided) card.appendChild(h('button', { type: 'button', class: 'secondary small', text: 'Remove manual mapping', 'data-fid': 'rm:' + c.name,
          onclick: function () { delete st.manual[c.name]; st.refocus = 'sel:' + c.name; draw(); } }));
      } else if (item) {
        var locked = decided || item.status === 'rejected';
        var e = item.evidence || {};
        card.appendChild(nameRow(item.source, c.name, c.required, badge(STATUS_BADGE, item.status)));
        var conf = h('div', { class: 'row' });
        var bar_ = h('div', { class: 'bar', role: 'img', 'aria-label': 'confidence ' + pct(item.confidence) }, h('span'));
        bar_.firstChild.style.width = Math.max(0, Math.min(100, Math.round(item.confidence * 100))) + '%';
        conf.appendChild(h('span', { class: 'small muted', text: 'Provider confidence' }));
        conf.appendChild(bar_);
        conf.appendChild(h('span', { class: 'small', text: pct(item.confidence) }));
        card.appendChild(h('div', { class: 'row' }, conf));
        card.appendChild(item.evidence ? evidenceChips(e, true) : h('div', { class: 'evidence' }));
        if (item.reasons && item.reasons.length) {
          card.appendChild(h('ul', { class: 'reasons' + (item.status === 'rejected' ? ' rej' : '') },
            item.reasons.map(function (r) { return h('li', { text: r }); })));
        }
        if (item.rationale) {
          card.appendChild(h('div', { class: 'quote' }, h('span', { class: 'small', text: 'Provider says (unverified text): ' }), h('span', { text: item.rationale })));
        }
        if (item.status === 'rejected') {
          card.appendChild(h('div', { class: 'locked', text: 'Rejected by the deterministic checks. It cannot be included.' }));
        } else {
          var setTo = function (v) { return function () { if (locked) return; st.decisions[item.target] = v; draw(); }; };
          card.appendChild(segGroup(item.target, included(item), locked, setTo));
        }
      } else {
        card.appendChild(nameRow(null, c.name, c.required,
          h('span', { class: 'badge ' + (c.required ? 'b-bad' : 'b-neutral'), text: c.required ? 'Required, not mapped' : 'Not mapped' })));
        card.appendChild(h('div', { class: 'locked', text: c.required ? 'The provider found no source for this required column. Approval is blocked until you choose one.' : 'No source column chosen.' }));
      }
      if (!decided && remap.available) card.appendChild(changeSource(c, n));
      return card;
    }

    function currentText(c) {
      if (st.manual[c.name]) return 'Manual: ← ' + st.manual[c.name].source;
      var it = itemFor[c.name];
      if (it && it.status !== 'rejected') {
        if (included(it)) return 'Proposed: ← ' + it.source + (it.status === 'accepted' ? ' (verified)' : ' (reviewed by you)');
        return 'Proposed: ← ' + it.source + ' (currently excluded)';
      }
      return 'Not mapped';
    }
    function checkManual(t, idx) {
      st.remapErr[t] = ''; st.checking[t] = true; st.refocus = 'sel:' + t; draw();
      api('/api/proposals/' + id + '/check', { method: 'POST', body: { target: t, source: cols[idx] } })
        .then(function (res) { st.checking[t] = false; st.manual[t] = { source: cols[idx], evidence: res.evidence }; draw(); })
        .catch(function (e) { st.checking[t] = false; delete st.manual[t]; st.remapErr[t] = e.message; draw(); });
    }
    function changeSource(c, n) {
      var selId = 'sel' + n;
      var sel = h('select', { id: selId, 'data-role': 'remap-select', 'data-fid': 'sel:' + c.name, disabled: !!st.checking[c.name] });
      var hasProposal = !st.manual[c.name] && itemFor[c.name] && itemFor[c.name].status !== 'rejected' && included(itemFor[c.name]);
      sel.appendChild(h('option', { value: '', text: st.manual[c.name] ? 'Remove my manual mapping'
                                                     : (hasProposal ? 'Keep the proposed mapping (or choose another)…' : 'Choose a file column…') }));
      cols.forEach(function (name, idx) {
        var owner = usedBy(name, c.name);
        sel.appendChild(h('option', { value: String(idx), text: name + (owner ? '   (used for ' + owner + ')' : ''), disabled: !!owner }));
      });
      if (st.manual[c.name]) sel.value = String(cols.indexOf(st.manual[c.name].source));
      sel.addEventListener('change', function () {
        if (sel.value === '') { delete st.manual[c.name]; st.remapErr[c.name] = ''; draw(); }
        else checkManual(c.name, Number(sel.value));
      });
      var box = h('div', { class: 'change' },
        h('div', { class: 'cur', 'data-role': 'current', text: currentText(c) }),
        h('div', null, h('label', { class: 'f', for: selId, text: 'Use a different file column' }), sel));
      if (st.checking[c.name]) box.appendChild(h('div', { class: 'small muted full', text: 'Checking against the source file…' }));
      if (st.remapErr[c.name]) box.appendChild(h('div', { class: 'err full', role: 'alert', 'data-role': 'remap-error', text: st.remapErr[c.name] }));
      return box;
    }

    function drawItems() {
      clear(itemsBox);
      var nDecide = p.items.filter(function (i) { return i.status === 'needs_review' && !st.manual[i.target] && st.decisions[i.target] === undefined; }).length;
      var nOpen = uncovered().length, nManual = Object.keys(st.manual).length;
      var nVerified = p.items.filter(function (i) { return i.status === 'accepted'; }).length;
      var nRefused = p.items.filter(function (i) { return i.status === 'rejected'; }).length;
      summaryBox.textContent = nDecide + ' need your decision' + (nOpen ? ' · ' + nOpen + ' required column(s) unmapped' : '') + ' · ' + nVerified +
        ' verified · ' + nRefused + ' refused by the checks' + (nManual ? ' · ' + nManual + ' mapped by you' : '');
      order.forEach(function (x) { itemsBox.appendChild(targetCard(x.c, x.n)); });
      if (!order.length) itemsBox.appendChild(h('div', { class: 'card empty', text: 'The schema has no columns.' }));
    }
    function drawRemap() {
      clear(remapBox);
      if (decided || remap.available) return;
      remapBox.appendChild(h('div', { class: 'card' }, h('p', { class: 'small', id: 'remap-unavailable',
        text: 'Choosing a different file column needs the original data file to verify your choice. ' + (remap.reason || '') }),
        h('p', { class: 'small muted', text: 'Start the review with --data-dir <folder containing the file>.' })));
    }
    var barMode = null;
    function drawBar() {
      // The editing bar holds the reviewer's half-typed name and note, so it is built once and only refreshed;
      // rebuilding it on every redraw would detach the focused input mid-keystroke.
      var mode = decided ? 'decided' : (st.result ? 'result' : 'edit');
      if (mode === 'edit' && barMode === 'edit') { updateBar(); return; }
      barMode = mode;
      clear(bar);
      if (decided) {
        var d = data.state;
        var box = h('div', { class: 'inner' }, h('div', { class: 'banner ' + (d.state === 'approved' ? 'ok' : 'bad'),
          text: (d.state === 'approved' ? 'Approved' : 'Rejected') + ' by ' + d.by + ' on ' + when(d.ts) + (d.note ? ' — ' + d.note : '') +
                (d.schema_file ? '. Schema file: ' + d.schema_file : '') }));
        bar.appendChild(box);
        return;
      }
      if (st.result) {
        var r = st.result;
        var cmd = 'python -m datapipe run <your-file> --schema ' + r.schema_file + ' --policy ' + p.policy + ' --analysis <analysis.json>';
        var dl = h('button', { class: 'secondary', type: 'button', id: 'download', text: 'Download schema JSON', onclick: function () {
          var blob = new Blob([JSON.stringify(r.schema, null, 2) + '\n'], { type: 'application/json' });
          var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = r.schema_file.split('/').pop();
          document.body.appendChild(a); a.click(); a.remove();
        } });
        bar.appendChild(h('div', { class: 'inner', id: 'result' },
          h('div', { class: 'banner ok', role: 'status', text: 'Approved. Schema created: ' + r.schema_file + ' (fingerprint ' + r.fingerprint.slice(0, 12) + ')' }),
          h('div', { class: 'cmd', text: cmd }), h('div', { class: 'btns' }, dl,
            h('button', { class: 'secondary', type: 'button', text: 'Back to proposals', onclick: function () { location.hash = '#/'; route(); } }))));
        return;
      }
      bar.appendChild(h('div', { class: 'inner' },
        h('div', { class: 'fields' },
          h('div', null, h('label', { class: 'f', for: 'reviewer', text: 'Reviewer' }), reviewer),
          h('div', null, h('label', { class: 'f', for: 'note', text: 'Note' }), note)),
        h('div', { class: 'btns' }, approveBtn, rejectBtn, why), errBox));
      updateBar();
    }

    var reqBanner = null;
    function drawBanner() {
      if (!reqBanner) return;
      var open = (p.unmapped_targets || []).filter(function (t) { return t.required && !st.manual[t.target]; });
      reqBanner.hidden = !open.length;
      reqBanner.textContent = open.length ? 'Required columns without any mapping: ' + open.map(function (t) { return t.target; }).join(', ') + '. Approval is blocked until they are mapped (use “Use a different file column” on the card).' : '';
    }
    function draw() {
      var active = document.activeElement, fid = focusId();
      if (fid) st.refocus = fid;
      else if (active && active !== document.body) st.refocus = null;       // the reviewer is typing elsewhere: never take focus from them
      drawItems(); drawRemap(); drawBanner(); drawBar();
      if (st.refocus && refocus(st.refocus)) st.refocus = null;      // a disabled control (mid-check) keeps the request pending
    }

    function body(extra) { var o = { reviewer: reviewer.value.trim(), note: note.value.trim() }; Object.keys(extra || {}).forEach(function (k) { o[k] = extra[k]; }); return o; }
    function submit(path, payload, onOk) {
      st.busy = true; st.error = ''; updateBar();
      api('/api/proposals/' + id + '/' + path, { method: 'POST', body: payload })
        .then(function (res) { st.busy = false; onOk(res); })
        .catch(function (e) { st.busy = false; st.error = e.message; updateBar(); });
    }
    approveBtn.addEventListener('click', function () {
      var o = overrides();
      submit('approve', body({ include: o.include, exclude: o.exclude, manual: manualList() }), function (res) { st.result = res; draw(); });
    });
    rejectBtn.addEventListener('click', function () {
      submit('reject', body(), function () { showDetail(id); });
    });
    reviewer.addEventListener('input', updateBar);
    note.addEventListener('input', updateBar);

    // ---- static parts
    clear(app);
    app.appendChild(h('button', { type: 'button', class: 'back', onclick: function () { location.hash = '#/'; }, text: '← All proposals' }));
    var detailHeading = h('h2', { text: p.source.name || '(unnamed file)' });
    app.appendChild(h('div', { class: 'row spread' }, detailHeading, badge(STATE_BADGE, data.state.state)));
    arrived(p.source.name || 'Proposal', detailHeading, 'Proposal ' + (p.source.name || '') + ', ' + data.state.state);
    if (!data.integrity_ok) app.appendChild(h('div', { class: 'banner bad', role: 'alert', text: 'INTEGRITY CHECK FAILED: this proposal was modified after it was created. Do not approve it.' }));
    reqBanner = h('div', { class: 'banner warn', id: 'req-banner' });
    app.appendChild(reqBanner);

    app.appendChild(h('div', { class: 'card' }, h('dl', { class: 'meta' },
      h('dt', { text: 'Source file' }), h('dd', { text: (p.source.name || '') + ' · ' + (p.source.format || '?') + ' · sha256 ' + String(p.source.sha256 || '').slice(0, 12) }),
      h('dt', { text: 'Proposed by' }), h('dd', { text: p.actor + ' on ' + when(p.created) }),
      h('dt', { text: 'Policy' }), h('dd', { text: p.policy }),
      h('dt', { text: 'Provider' }), h('dd', { text: (p.provider.name || '?') + (p.provider.model ? ' (' + p.provider.model + ')' : '') + ' · ' + (p.provider.locality || '') }),
      h('dt', { text: 'Data sent out' }), h('dd', { text: egressText((p.egress || {}).mode) }),
      h('dt', { text: 'Thresholds' }), h('dd', { text: 'confidence ≥ ' + (p.thresholds || {}).min_confidence + ', value fit ≥ ' + pct((p.thresholds || {}).min_parse_rate) })),
      (p.egress && p.egress.payload) ? h('details', null, h('summary', { text: 'Exactly what was sent' }), h('pre', { id: 'payload', text: JSON.stringify(p.egress.payload, null, 2) })) : null));

    app.appendChild(h('h2', { text: 'Mappings' }));
    app.appendChild(summaryBox);
    app.appendChild(h('p', { class: 'small muted', text: 'One card per schema column; the ones that need you come first. Verified items are included by default, items that need review are excluded until you include them, and refused items cannot be included. To use a different file column, choose it on the card: it is checked against the real values in the source file, and a note is required.' }));
    app.appendChild(remapBox);
    app.appendChild(itemsBox);

    var others = h('div', { class: 'card' });
    others.appendChild(h('h3', { text: 'File columns not used' }));
    others.appendChild(h('p', { class: 'small', text: (p.unmapped_sources || []).length ? p.unmapped_sources.join(', ') : 'none' }));
    if ((p.unmapped_sources || []).length) others.appendChild(h('p', { class: 'small muted', text: 'Under a strict policy, unused file columns count as schema drift and block runs.' }));
    app.appendChild(others);    (p.warnings || []).forEach(function (w) { app.appendChild(h('div', { class: 'banner warn', text: w })); });
    app.appendChild(bar);
    draw();
  }

  // ------------------------------------------------------------------ routing
  function route() {
    var m = /^#\/p\/([0-9a-f]{64})$/.exec(location.hash);
    if (m && ID_RE.test(m[1])) showDetail(m[1]); else showList();
  }
  window.addEventListener('hashchange', route);
  if (fixedReviewer) document.getElementById('whoami').textContent = 'reviewing as ' + fixedReviewer;
  route();
})();
</script>
</body>
</html>
"""


def render_page(nonce: str, csrf: str, fixed_reviewer_escaped: str) -> str:
    return (_TEMPLATE.replace("{{NONCE}}", nonce).replace("{{CSRF}}", csrf)
            .replace("{{FIXED}}", fixed_reviewer_escaped))
