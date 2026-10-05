"""The single-page review UI. Everything (HTML, CSS, JS) is inline so a strict CSP can forbid all other sources.

Rule for the JavaScript below: untrusted text (column names, provider rationale, notes, file names) is only ever
put into the page with textContent / createTextNode. There is no innerHTML, no eval, no javascript: URLs.
"""

_TEMPLATE = r"""<!doctype html>
<html lang="en"{{THEME_ATTR}}>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="{{SCHEME}}">
<meta name="csrf" content="{{CSRF}}">
<meta name="fixed-reviewer" content="{{FIXED}}">
<title>datapipe</title>
<link rel="icon" href="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxNiAxNiI+PHJlY3Qgd2lkdGg9IjE2IiBoZWlnaHQ9IjE2IiByeD0iMyIgZmlsbD0iIzI3NTdkNiIvPjxwYXRoIGQ9Ik00IDguNWwzIDMgNS02IiBzdHJva2U9IndoaXRlIiBzdHJva2Utd2lkdGg9IjIiIGZpbGw9Im5vbmUiLz48L3N2Zz4=">
<style nonce="{{NONCE}}">
:root{
  --bg:#f6f6f3; --surface:#ffffff; --text:#1b1b19; --muted:#63635d; --line:#dcdcd4;
  --line-strong:#76766e;   /* control boundaries: >= 3:1 on surface and page (WCAG 1.4.11); --line stays for decorative card edges */
  --accent:#2757d6; --accent-ink:#ffffff;
  --ok:#17692f; --ok-bg:#e4f3e8; --warn:#8a5200; --warn-bg:#fff1d0; --bad:#a8231b; --bad-bg:#fce6e3;
  --radius:12px; --r-sm:8px; --r-md:10px; --r-pill:999px;
  --tap:44px; --tap-sm:40px; --s1:4px; --s2:8px; --s3:12px; --s4:16px; --s5:24px; --s6:48px; --fs-xs:.8rem; --fs-sm:.9rem; --fs-md:.9rem; --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme=light]){
    --bg:#131312; --surface:#1d1d1b; --text:#ecece7; --muted:#a4a49c; --line:#383832;
    --line-strong:#8c8c84;
    --accent:#7ea3ff; --accent-ink:#0d1526;
    --ok:#77d296; --ok-bg:#15301e; --warn:#f2bd5f; --warn-bg:#33270e; --bad:#ff9087; --bad-bg:#3b1a17;
  }
}
:root[data-theme=dark]{
  --bg:#131312; --surface:#1d1d1b; --text:#ecece7; --muted:#a4a49c; --line:#383832;
  --line-strong:#8c8c84;
  --accent:#7ea3ff; --accent-ink:#0d1526;
  --ok:#77d296; --ok-bg:#15301e; --warn:#f2bd5f; --warn-bg:#33270e; --bad:#ff9087; --bad-bg:#3b1a17;
}
:root[data-theme=light]{
  --bg:#f6f6f3; --surface:#ffffff; --text:#1b1b19; --muted:#63635d; --line:#dcdcd4;
  --line-strong:#76766e;   /* control boundaries: >= 3:1 on surface and page (WCAG 1.4.11); --line stays for decorative card edges */
  --accent:#2757d6; --accent-ink:#ffffff;
  --ok:#17692f; --ok-bg:#e4f3e8; --warn:#8a5200; --warn-bg:#fff1d0; --bad:#a8231b; --bad-bg:#fce6e3;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font:1rem/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}  /* rem, so the reader's text-size setting scales everything together */
header.top{display:flex;align-items:center;gap:var(--s3);padding:var(--s4) var(--s4);border-bottom:1px solid var(--line);background:var(--surface)}
header.top h1{font-size:1.05rem;margin:0;font-weight:650}
header.top .sub{color:var(--muted);font-size:var(--fs-sm)}
main{max-width:920px;margin:0 auto;padding:var(--s4) var(--s4) var(--s6);overflow-wrap:anywhere}      /* file names and column names are one long token: they must wrap, never widen the page (WCAG 1.4.10) */
a,button{font:inherit}
button{overflow-wrap:normal}
.titlerow{align-items:center;flex-wrap:nowrap}
.titlerow>.row{min-width:0;flex:1}
.titlerow h2{margin:0}
/* The three folded sections share one line; an open one takes the full width. */
.folds{display:flex;flex-wrap:wrap;gap:0 var(--s5);margin:0 0 var(--s2)}
.folds details{margin:0;flex:0 0 auto}
.folds details[open]{flex-basis:100%}
.folds details>summary{padding:var(--s2) 0}
button.back{background:none;border:0;color:var(--accent);display:inline-block;padding:var(--s2) 0;min-height:var(--tap);cursor:pointer;text-align:left}
.sr-only{position:absolute;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
h2[tabindex="-1"]:focus{outline:none}
button:focus-visible,input:focus-visible,textarea:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
h2{font-size:1.15rem;margin:var(--s5) 0 var(--s2);overflow-wrap:anywhere}
h3{font-size:1rem;margin:0;overflow-wrap:anywhere;min-width:0}
h2.tight{margin-top:var(--s3)}
::placeholder{color:var(--muted);opacity:1}                                   /* 4.5:1 on the field background in both themes */
#result:focus{outline:none}
.muted{color:var(--muted)}
.small{font-size:var(--fs-sm)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:var(--s4) var(--s4);margin:0 0 var(--s3)}
.card.click{cursor:pointer}
.card.click:hover{border-color:var(--accent)}
.row{display:flex;flex-wrap:wrap;gap:var(--s2);align-items:center}
.spread{justify-content:space-between}
.badge{display:inline-block;padding:2px var(--s2);border-radius:var(--r-pill);font-size:var(--fs-xs);font-weight:600;border:1px solid transparent}
.b-ok{background:var(--ok-bg);color:var(--ok)}
.b-warn{background:var(--warn-bg);color:var(--warn)}
.b-bad{background:var(--bad-bg);color:var(--bad)}
.b-neutral{background:transparent;color:var(--muted);border-color:var(--line)}
.chip{display:inline-block;padding:1px var(--s2);border-radius:var(--r-sm);border:1px solid var(--line);font-size:var(--fs-xs);color:var(--muted)}
.name{font-family:var(--mono);font-size:var(--fs-md);background:var(--bg);border:1px solid var(--line);border-radius:var(--r-sm);padding:2px var(--s2);overflow-wrap:anywhere;white-space:pre-wrap}
.arrow{color:var(--muted)}
.banner{border-radius:var(--radius);padding:var(--s3) var(--s4);margin:0 0 var(--s3);border:1px solid transparent}
.banner.bad{background:var(--bad-bg);color:var(--bad);border-color:var(--bad)}
.banner.ok{background:var(--ok-bg);color:var(--ok);border-color:var(--ok)}
.banner.warn{background:var(--warn-bg);color:var(--warn);border-color:var(--warn)}
dl.meta{display:grid;grid-template-columns:max-content 1fr;gap:var(--s1) var(--s4);margin:0}
dl.meta dt{color:var(--muted)}
dl.meta dd{margin:0;overflow-wrap:anywhere}
@media (max-width:520px){dl.meta{grid-template-columns:1fr}dl.meta dt{margin-top:var(--s2)}.titlerow{flex-wrap:wrap}}
details{margin:var(--s2) 0 0}
summary{cursor:pointer;color:var(--accent);padding:var(--s2) 0}
pre{background:var(--bg);border:1px solid var(--line);border-radius:var(--r-sm);padding:var(--s2);overflow:auto;max-height:320px;font:var(--fs-xs)/1.4 var(--mono);white-space:pre-wrap;overflow-wrap:anywhere}
.bar{height:6px;border-radius:3px;background:var(--line);overflow:hidden;min-width:80px;flex:1}
.bar>span{display:block;height:100%;background:var(--accent)}
.evidence{display:flex;flex-wrap:wrap;gap:var(--s2);margin:var(--s2) 0 0}
ul.reasons{margin:var(--s2) 0 0;padding-left:var(--s5);color:var(--warn)}
ul.reasons.rej{color:var(--bad)}
.quote{margin:var(--s2) 0 0;padding:var(--s2) var(--s2);border-left:3px solid var(--line);color:var(--muted);font-size:var(--fs-md);overflow-wrap:anywhere}
.seg{display:inline-flex;border:1px solid var(--line-strong);border-radius:var(--r-md);overflow:hidden;margin-top:var(--s2)}
.seg button{border:0;background:var(--surface);color:var(--text);padding:var(--s2) var(--s4);min-height:var(--tap);cursor:pointer}
.seg button+button{border-left:1px solid var(--line-strong)}
.seg button[aria-checked=true]{background:var(--accent);color:var(--accent-ink);font-weight:600}
.seg button:disabled{cursor:not-allowed;opacity:.55}
/* .seg clips its corners (overflow:hidden), so the focus ring is drawn inside the button; on the filled option it flips to the ink colour so it is not blue-on-blue */
.seg button:focus-visible{outline:3px solid var(--accent);outline-offset:-5px}
.seg button[aria-checked=true]:focus-visible{outline-color:var(--accent-ink)}
.seg button[aria-checked=true]::before{content:"\2713\00a0"}
.seg button[aria-checked=true]::before{content:"\2713\00a0" / ""}              /* a tick as well as the colour (WCAG 1.4.1); the screen reader already has aria-checked */
.undecided{margin:var(--s2) 0 0;color:var(--warn);font-size:var(--fs-sm)}
.card[data-open="1"]{border-color:var(--warn)}
.locked{margin-top:var(--s2);color:var(--muted);font-size:var(--fs-md)}
.actionbar{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:var(--s4) var(--s4);margin:var(--s4) 0 0}
.actionbar .inner{display:grid;gap:var(--s2)}
/* Wide screens: one fixed row (name | note | buttons) with the one-line "why" under it, so the bar stays about 100px and never hides the first card. */
@media (min-width:900px){
  main{padding-bottom:140px}
  .actionbar{position:fixed;left:0;right:0;bottom:0;border-radius:0;border-width:1px 0 0;margin:0;padding:var(--s2) var(--s4);z-index:5}
  .actionbar .inner{max-width:920px;margin:0 auto;gap:var(--s1) var(--s3);grid-template-columns:1fr auto;align-items:end}
  .actionbar .inner>.btns{flex-wrap:nowrap}
  .actionbar .inner>.msgs{grid-column:1 / -1}
  .actionbar textarea{min-height:40px;height:40px}
}
.fields{display:grid;grid-template-columns:minmax(120px,220px) 1fr;gap:var(--s2)}
@media (max-width:620px){.fields{grid-template-columns:1fr}}
label.f{display:block;font-size:var(--fs-xs);color:var(--muted);margin-bottom:2px}
input,textarea{width:100%;padding:var(--s2) var(--s2);border-radius:var(--r-sm);border:1px solid var(--line-strong);background:var(--bg);color:var(--text);font:inherit}
textarea{min-height:var(--tap);resize:vertical}
.btns{display:flex;flex-wrap:wrap;gap:var(--s2);align-items:center}
button.primary{background:var(--accent);color:var(--accent-ink);border:0;border-radius:var(--r-md);padding:var(--s2) var(--s4);min-height:var(--tap);font-weight:650;cursor:pointer}
button.secondary{background:transparent;color:var(--text);border:1px solid var(--line-strong);border-radius:var(--r-md);padding:var(--s2) var(--s4);min-height:var(--tap);cursor:pointer}
button.danger{color:var(--bad);border-color:var(--bad)}
button:disabled{opacity:.5;cursor:not-allowed}
.why{color:var(--muted);font-size:var(--fs-sm);flex:1;min-width:180px}
.msgs{display:grid;gap:2px}
.msgs .why{min-width:0}
.err{color:var(--bad);font-size:var(--fs-md)}
.cmd{font:var(--fs-sm)/1.4 var(--mono);background:var(--bg);border:1px solid var(--line);border-radius:var(--r-sm);padding:var(--s2) var(--s2);overflow-wrap:anywhere;white-space:pre-wrap}
.empty{text-align:center;padding:var(--s5) var(--s3);color:var(--muted)}
select{width:100%;padding:var(--s2) var(--s2);border-radius:var(--r-sm);border:1px solid var(--line-strong);background:var(--bg);color:var(--text);font:inherit;min-height:42px}
select:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.change{display:grid;grid-template-columns:1fr minmax(170px,290px);gap:var(--s2) var(--s4);align-items:end;margin-top:var(--s3);padding-top:var(--s3);border-top:1px solid var(--line)}
.change .cur{font-size:var(--fs-md);overflow-wrap:anywhere}
.change .full{grid-column:1 / -1}
@media (max-width:760px){.change{grid-template-columns:1fr}}.badge.b-manual{background:var(--accent);color:var(--accent-ink)}
.superseded{margin-top:var(--s2);color:var(--muted);font-size:var(--fs-md)}
button.small{padding:var(--s2) var(--s3);min-height:var(--tap-sm);font-size:var(--fs-sm)}
/* The reviewer's live to-do count stays on screen while scrolling; on phones the action bar is at the end of the page, so it also carries a jump. */
.statusline{position:sticky;top:0;z-index:4;display:flex;flex-wrap:wrap;gap:var(--s2);align-items:center;justify-content:space-between;background:var(--bg);border-bottom:1px solid var(--line);padding:var(--s2) 0;margin:0 0 var(--s3)}
.statusline p{margin:0;font-weight:600;flex:1;min-width:200px}
.statusline label{display:flex;gap:var(--s2);align-items:center;font-size:var(--fs-sm);min-height:var(--tap-sm)}
.statusline input{width:auto}
.jump{display:none}
@media (max-width:899px){.jump{display:inline-block}}
header.top nav{display:flex;gap:var(--s2);flex:1}
header.top nav a.gear{margin-left:auto;gap:var(--s1)}
header.top nav a{color:var(--text);text-decoration:none;padding:var(--s2) var(--s3);border-radius:var(--r-md);min-height:var(--tap-sm);display:inline-flex;align-items:center}
header.top nav a[aria-current=page]{background:var(--accent);color:var(--accent-ink);font-weight:650}
header.top nav a:focus-visible,a.dl:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.runfields{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:var(--s3);margin:var(--s3) 0}
.runfield select,.runfield input{width:100%}
a.dl{display:inline-flex;align-items:center;min-height:var(--tap);padding:var(--s2) var(--s4);border:1px solid var(--line-strong);border-radius:var(--r-md);color:var(--text);text-decoration:none}
a.dl:hover{border-color:var(--accent)}
a.dl.small{min-height:var(--tap-sm);font-size:var(--fs-sm);margin-top:var(--s2)}
.tablecard{overflow-x:auto}
table.metric{border-collapse:collapse;width:100%;font-size:var(--fs-sm)}
table.metric caption{text-align:left;font-weight:650;padding-bottom:var(--s2);text-transform:capitalize}
table.metric th,table.metric td{text-align:left;padding:var(--s1) var(--s3);border-bottom:1px solid var(--line);white-space:nowrap}
table.metric th{color:var(--muted);font-weight:600}
.ok-note{color:var(--ok)}
.mono{font-family:var(--mono);overflow-wrap:anywhere}
.folds{margin:var(--s3) 0}
.warn-note{color:var(--warn)}
.warn-note button{margin-left:var(--s2)}
table.metric .num{text-align:right;font-variant-numeric:tabular-nums}
/* Phones: the reviewer's first decision must be on the first screen even with taller fonts (Linux) or larger text, so the chrome above it is tight. */
.titlerow h2{margin:0}
@media (max-width:520px){
  header.top{padding:var(--s2) var(--s3);flex-wrap:wrap;gap:var(--s1) var(--s2)}
  header.top h1{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap}     /* the tab title already says datapipe; the nav needs the width */
  header.top nav a.gear .navtext{display:none}
  header.top nav a.gear{min-width:var(--tap-sm);justify-content:center}
  header.top nav{gap:var(--s1)}
  header.top nav a{min-height:36px;padding:var(--s1) var(--s3)}
  main{padding-top:var(--s3)}
  .titlerow{gap:var(--s1);margin-bottom:var(--s1)}
  button.back{padding:0}
  .statusline p{min-width:140px}
  .statusline{padding:var(--s1) 0;margin-bottom:var(--s2)}
  .metaline{margin-bottom:var(--s1)}
  summary{padding:var(--s2) 0}
}
@media (forced-colors:active){header.top nav a[aria-current=page]{border-bottom:4px solid ButtonText}}
html{scroll-padding-top:72px;scroll-padding-bottom:150px}              /* focus must not end up under the sticky strip or the action bar */
.statusline+.card{margin-top:0}
.metaline{margin:0 0 var(--s2)}
/* Short screens (a phone on its side, 400% zoom): a sticky strip would eat half the view, so it scrolls away instead. */
@media (max-height:520px){.statusline{position:static}html{scroll-padding-top:0}}
/* Forced-colours (Windows High Contrast): keep control edges and show the chosen option by shape, not only by colour. */
@media (forced-colors:active){
  button.primary,.seg button,.card{border:1px solid ButtonText}
  .seg button[aria-checked=true]{background:Highlight;color:HighlightText;forced-color-adjust:none;border-bottom:4px solid HighlightText;font-weight:700}
  .seg button:focus-visible{outline-color:CanvasText}
}
</style>
</head>
<body>
<header class="top"><h1>datapipe</h1>
<nav aria-label="Sections"><a href="#/run" id="nav-run">Run a file</a><a href="#/" id="nav-review">Review mappings</a><a href="#/settings" id="nav-settings" class="gear" aria-label="Settings"><span aria-hidden="true">⚙</span><span class="navtext"> Settings</span></a></nav>
<span class="sub" id="whoami"></span></header>
<main id="app"></main>
<div id="status" class="sr-only" role="status" aria-live="polite"></div>
<script nonce="{{NONCE}}">
(function () {
  'use strict';
  var csrf = document.querySelector('meta[name=csrf]').content;
  var fixedReviewer = document.querySelector('meta[name=fixed-reviewer]').content;
  var app = document.getElementById('app');
  var ID_RE = /^[0-9a-f]{64}$/;
  var unsaved = null;                       // set by the detail view; returns true while the reviewer has unsubmitted work

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
    document.title = title + ' – datapipe';
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
  // same normalisation as the server's four-eyes check (case, spacing, invisible characters); the server decides, this only warns early
  function person(n) { return String(n || '').normalize('NFKC').replace(/[\u200B-\u200F\u202A-\u202E\u2060-\u2064\u2066-\u2069\uFEFF]/g, '').replace(/\s+/g, ' ').trim().toLowerCase(); }
  // Names come from files we do not control. Invisible and direction-changing characters (e.g. U+202E, which makes "pa<U+202E>di" read as "paid")
  // are shown as a visible [U+XXXX] marker instead of silently changing what the reviewer reads.
  var HIDDEN = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F\u200B-\u200F\u202A-\u202E\u2060-\u2064\u2066-\u2069\uFEFF]/g;
  function show(s) {
    return String(s === null || s === undefined ? '' : s).replace(HIDDEN, function (c) { return '[U+' + ('0000' + c.charCodeAt(0).toString(16).toUpperCase()).slice(-4) + ']'; });
  }
  function pct(x) { return (x === null || x === undefined) ? 'n/a' : Math.round(x * 100) + '%'; }
  function when(iso) { var d = new Date(iso); return isNaN(d) ? String(iso || '') : d.toISOString().slice(0, 16).replace('T', ' ') + ' UTC'; }

  function api(path, opts) {
    var o = opts || {};
    var headers = { 'Accept': 'application/json' };
    if (o.body !== undefined) { headers['Content-Type'] = 'application/json'; headers['X-DataPipe-CSRF'] = csrf; }
    return fetch(path, { method: o.method || 'GET', credentials: 'same-origin', headers: headers,
                         body: o.body === undefined ? undefined : JSON.stringify(o.body) })
      .catch(function () {
        throw new Error('Cannot reach the review server. Check that “datapipe review” is still running in your terminal, then try again. Nothing you entered on this page has been lost.');
      })
      .then(function (r) {
        return r.json().catch(function () { return null; }).then(function (body) {
          if (!r.ok) throw new Error(friendly(r.status, body && body.error));
          return body;
        });
      });
  }
  // Server messages that are already written for people pass through; the bare ones get a next step.
  function friendly(status, msg) {
    if (status === 401) return 'This page is no longer signed in (the review server was restarted, or the link was opened in another browser). Open the link printed by “datapipe review” again.';
    if (status === 403) return 'The server refused this request because the page is out of date (the review server was restarted). Reload the page, then repeat your last step.';
    if (status === 421) return 'Open this page with the address printed by “datapipe review” (127.0.0.1), not through another host name.';
    if (status === 500) return 'The review server hit an internal error. Look at the terminal where “datapipe review” runs; nothing was saved.';
    if (status === 413) return 'The note or selection is too large to send. Shorten the note and try again.';
    return msg || ('The server answered with an error (' + status + '). Reload the page and try again.');
  }

  var STATE_BADGE = { pending: ['b-warn', 'Pending review'], approved: ['b-ok', 'Approved'], rejected: ['b-bad', 'Rejected'] };
  var STATUS_BADGE = { accepted: ['b-ok', 'Verified'], needs_review: ['b-warn', 'Needs your review'], rejected: ['b-bad', 'Rejected by verification'] };
  function egressText(mode) {
    if (mode === 'none') return 'Nothing left this machine (offline provider)';
    if (mode === 'local') return 'Column names and value shapes were sent to a model on this machine; nothing left it';
    if (mode === 'shapes') return 'Column names and value shapes were sent to the LLM';
    if (mode === 'shapes+samples') return 'Column names, value shapes and a few sample values were sent to the LLM';
    return String(mode);
  }
  function badge(map, key) { var b = map[key] || ['b-neutral', String(key)]; return h('span', { class: 'badge ' + b[0], text: b[1] }); }

  // ------------------------------------------------------------------ list view
  // Every navigation gets a number; an answer that arrives for an older navigation is dropped, so a slow list can never paint over the proposal the reviewer just opened.
  var navSeq = 0;
  function showList() {
    var seq = ++navSeq;
    clear(app);
    app.appendChild(h('p', { class: 'muted', text: 'Loading proposals…' }));
    api('/api/proposals').then(function (data) {
      if (seq !== navSeq) return;
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
    }).catch(function (e) { if (seq === navSeq) showError(e); });
  }

  function showError(e) {
    clear(app);
    app.appendChild(h('div', { class: 'banner bad', role: 'alert', text: e.message || String(e) }));
    app.appendChild(h('div', { class: 'btns' },
      h('button', { type: 'button', class: 'secondary', onclick: function () { route(); }, text: 'Try again' }),
      h('button', { type: 'button', class: 'back', onclick: function () { if (location.hash === '#/' || !location.hash) route(); else location.hash = '#/'; }, text: '← Back to proposals' })));
    arrived('Error', null, 'Error: ' + (e.message || String(e)));
  }

  // ------------------------------------------------------------------ detail view
  function showDetail(id) {
    var seq = ++navSeq;
    clear(app);
    app.appendChild(h('p', { class: 'muted', text: 'Loading…' }));
    api('/api/proposals/' + id).then(function (data) { if (seq === navSeq) buildDetail(id, data); })
      .catch(function (e) { if (seq === navSeq) showError(e); });
  }

  function buildDetail(id, data) {
    var p = data.proposal;
    var decided = data.state.state !== 'pending';
    var remap = data.manual_remap || { available: false, reason: '', columns: [] };
    var cols = (remap.columns && remap.columns.length) ? remap.columns : (p.source.columns || []);
    var st = { decisions: {}, manual: {}, remapErr: {}, checking: {}, result: null, busy: false, error: '', refocus: null, want: null, onlyOpen: false };
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
    var jumpBtn = h('button', { type: 'button', class: 'secondary small jump', text: 'Go to approve / reject',
                                onclick: function () { bar.scrollIntoView({ block: 'end' }); (decided || st.result ? bar : reviewer).focus(); } });
    var remapBox = h('div', { id: 'remap' });
    var bar = h('div', { class: 'actionbar' });
    // Wide schemas (dozens of columns): jump to the next column that still needs a decision, or hide the ones the checks already settled.
    var nextBtn = h('button', { type: 'button', class: 'secondary small', id: 'next-open', text: 'Next to decide', onclick: function () {
      var c = itemsBox.querySelector('[data-open="1"]');
      if (!c) return;
      c.scrollIntoView({ block: 'center' });
      var f = c.querySelector('button[tabindex="0"], select');
      if (f) f.focus({ preventScroll: true });
    } });
    var filterCount = h('span');
    var onlyBox = h('input', { type: 'checkbox', id: 'only-open', onchange: function () { st.onlyOpen = onlyBox.checked; drawItems(); } });
    var filterLabel = h('label', { for: 'only-open' }, onlyBox, h('span', { text: 'Only columns that need me' }), filterCount);

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
      var same = reviewer.value.trim() && person(reviewer.value) === person(p.actor);
      var msg = pr.length ? 'To approve: ' + pr.join('; ') + '.' : 'Ready to approve.';
      var rej = [];
      if (!reviewer.value.trim()) rej.push('your name');
      if (!note.value.trim()) rej.push('a note');
      if (!data.integrity_ok) msg = 'This proposal was changed after it was created, so it can be neither approved nor rejected here. Create a fresh one with “datapipe map”.';
      else if (rej.length) msg += ' To reject, add ' + rej.join(' and ') + '.';
      if (same) msg += ' Note: the reviewer must be a different person than the proposer (' + p.actor + ').';
      why.textContent = msg;
      errBox.textContent = st.error;
    }

    // Two-option radio group: one tab stop (the checked option, or the first while nothing is chosen), arrow keys switch and move focus.
    // `chosen` is true / false, or undefined while the reviewer has not decided yet (then neither option looks selected).
    function segGroup(target, chosen, locked, setTo) {
      function opt(label, value) {
        var on = chosen === value;
        var stop = chosen === undefined ? value === true : on;
        return h('button', { type: 'button', role: 'radio', 'aria-checked': on ? 'true' : 'false', tabindex: stop ? '0' : '-1',
                             'data-fid': 'seg:' + target + ':' + label, disabled: locked, onclick: setTo(value),
                             onkeydown: function (ev) {
                               if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].indexOf(ev.key) < 0) return;
                               ev.preventDefault();
                               st.want = 'seg:' + target + ':' + (value ? 'Exclude' : 'Include');     // the redraw would otherwise hand focus back to the option just left
                               setTo(!value)();
                             }, text: label });
      }
      return h('div', { class: 'seg', role: 'radiogroup', 'aria-label': 'Decision for ' + show(target) }, opt('Include', true), opt('Exclude', false));
    }

    function evidenceChips(e, withAlternatives) {
      var chips = h('div', { class: 'evidence' });
      chips.appendChild(h('span', { class: 'chip', text: 'Values fit target type: ' + pct(e.parse_rate) + ' of ' + e.non_null }));
      chips.appendChild(h('span', { class: 'chip', text: 'Distinct values: ' + pct(e.distinct_ratio) }));
      chips.appendChild(h('span', { class: 'chip', text: 'Name similarity: ' + (e.name_score === undefined ? 'n/a' : pct(e.name_score)) }));
      if (withAlternatives && e.alternatives && e.alternatives.length) chips.appendChild(h('span', { class: 'chip', text: 'Other columns that also fit: ' + e.alternatives.join(', ') }));
      return chips;
    }
    // The target column name is the card's heading (so a screen reader can list and jump between the columns).
    function nameRow(source, target, required, rightBadge, hid) {
      return h('div', { class: 'row spread' },
        h('div', { class: 'row' },
          source === null ? null : h('span', { class: 'name', 'data-role': 'source', text: show(source) }),
          source === null ? null : h('span', { class: 'arrow' }, h('span', { 'aria-hidden': 'true', text: '→' }), h('span', { class: 'sr-only', text: ' maps to ' })),
          h('h3', { id: hid }, h('span', { class: 'name', 'data-role': 'target', text: show(target) })),
          required ? h('span', { class: 'badge b-neutral', text: 'required' }) : null),
        rightBadge);
    }

    // "Open" = still needs the reviewer: a proposal with no decision yet, or a required column with no source.
    function isOpen(c) {
      if (decided || st.manual[c.name]) return false;
      var it = itemFor[c.name];
      if (it) return it.status === 'needs_review' && st.decisions[it.target] === undefined;
      return !!c.required;
    }
    // One card per schema column: the proposal and its evidence, the reviewer's decision, and the way to choose another source.
    function targetCard(c, n) {
      var item = itemFor[c.name], man = st.manual[c.name];
      var status = man ? 'manual' : (item ? item.status : 'unmapped');
      var hid = 'col-' + n;
      var card = h('section', { class: 'card', 'data-target': c.name, 'data-status': status, 'aria-labelledby': hid });
      if (isOpen(c)) card.setAttribute('data-open', '1');
      if (man) {
        card.appendChild(nameRow(man.source, c.name, c.required, h('span', { class: 'badge b-manual', text: 'Manual (by you)' }), hid));
        card.appendChild(evidenceChips(man.evidence, false));
        if (man.evidence.warnings && man.evidence.warnings.length) {
          card.appendChild(h('ul', { class: 'reasons' }, man.evidence.warnings.map(function (w) { return h('li', { text: w }); })));
        }
        card.appendChild(h('div', { class: 'quote', text: 'Chosen by you and checked against the source file just now. A note is required when you approve.' }));
        if (item) card.appendChild(h('div', { class: 'superseded', 'data-role': 'superseded', text: 'This replaces the proposed mapping from ' + show(item.source) + '.' }));
        if (!decided) card.appendChild(h('button', { type: 'button', class: 'secondary small', text: 'Remove manual mapping', 'data-fid': 'rm:' + c.name,
          onclick: function () { delete st.manual[c.name]; st.want = 'sel:' + c.name; draw(); } }));
      } else if (item) {
        var locked = decided || item.status === 'rejected';
        var e = item.evidence || {};
        card.appendChild(nameRow(item.source, c.name, c.required, badge(STATUS_BADGE, item.status), hid));
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
          // A verified item starts as Include. An item that needs review starts with NOTHING chosen (it is left out unless you include it).
          var chosen = item.status === 'accepted' ? included(item) : st.decisions[item.target];
          card.appendChild(segGroup(item.target, chosen, locked, setTo));
          if (chosen === undefined && !locked) card.appendChild(h('div', { class: 'undecided', 'data-role': 'undecided', text: 'Not decided yet. If you leave it, this column is left out.' }));
        }
      } else {
        card.appendChild(nameRow(null, c.name, c.required,
          h('span', { class: 'badge ' + (c.required ? 'b-bad' : 'b-neutral'), text: c.required ? 'Required, not mapped' : 'Not mapped' }), hid));
        card.appendChild(h('div', { class: 'locked', text: c.required ? 'The provider found no source for this required column. Approval is blocked until you choose one.' : 'No source column chosen.' }));
      }
      if (!decided && remap.available) card.appendChild(changeSource(c, n));
      return card;
    }

    function currentText(c) {
      if (st.manual[c.name]) return 'Manual: ← ' + show(st.manual[c.name].source);
      var it = itemFor[c.name];
      if (it && it.status !== 'rejected') {
        if (included(it)) return 'Proposed: ← ' + show(it.source) + (it.status === 'accepted' ? ' (verified)' : ' (reviewed by you)');
        return 'Proposed: ← ' + show(it.source) + (st.decisions[it.target] === undefined ? ' (not decided yet, left out for now)' : ' (currently excluded)');
      }
      return 'Not mapped';
    }
    function checkManual(t, idx) {
      st.remapErr[t] = ''; st.checking[t] = true; st.want = 'sel:' + t; draw();
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
        sel.appendChild(h('option', { value: String(idx), text: show(name) + (owner ? '   (used for ' + show(owner) + ')' : ''), disabled: !!owner }));
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
      summaryBox.textContent = data.integrity_ok
        ? nDecide + ' need your decision' + (nOpen ? ' · ' + nOpen + ' required column(s) unmapped' : '') + ' · ' + nVerified +
          ' verified · ' + nRefused + ' refused by the checks' + (nManual ? ' · ' + nManual + ' mapped by you' : '')
        : 'Integrity check failed: the verdicts below cannot be trusted, so this proposal cannot be approved.';
      // "Only the columns that need me" is decided from the server's verdicts (rank 0), not from clicks, so a card never vanishes under the reviewer's hands.
      var shown = order.filter(function (x) { return !st.onlyOpen || x.rank === 0; });
      shown.forEach(function (x) { itemsBox.appendChild(targetCard(x.c, x.n)); });
      if (!order.length) itemsBox.appendChild(h('div', { class: 'card empty', text: 'The schema has no columns.' }));
      else if (!shown.length) itemsBox.appendChild(h('div', { class: 'card empty', text: 'Every column is verified or refused by the checks; nothing needs your decision.' }));
      nextBtn.disabled = itemsBox.querySelector('[data-open="1"]') === null;
      filterCount.textContent = ' (' + order.filter(function (x) { return x.rank === 0; }).length + ' of ' + order.length + ')';
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
        // Absolute paths, so the command works from any folder (the schema lives in the review's work folder, not the current one).
        function q(s) { return /^[A-Za-z0-9_@%+=:,.\/\\-]+$/.test(s) ? s : '"' + s.replace(/"/g, '\\"') + '"'; }
        var cmd = 'python -m datapipe --workdir ' + q(r.workdir || 'work') + ' run <your-file> --schema ' + q(r.schema_path || r.schema_file) +
                  ' --policy ' + p.policy + ' --analysis <analysis.json>';
        var dl = h('button', { class: 'secondary', type: 'button', id: 'download', text: 'Download schema JSON', onclick: function () {
          var blob = new Blob([JSON.stringify(r.schema, null, 2) + '\n'], { type: 'application/json' });
          var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = r.schema_file.split('/').pop();
          document.body.appendChild(a); a.click(); a.remove();
        } });
        bar.appendChild(h('div', { class: 'inner', id: 'result', tabindex: '-1' },
          h('div', { class: 'banner ok', role: 'status', text: 'Approved. Schema created: ' + r.schema_file + ' (fingerprint ' + r.fingerprint.slice(0, 12) + ')' }),
          h('div', { class: 'small muted', text: 'Next, run your data file with this schema. Replace <your-file> and <analysis.json> with your own files:' }),
          h('div', { class: 'cmd', text: cmd }), h('div', { class: 'btns' }, dl,
            h('button', { class: 'secondary', type: 'button', text: 'Back to proposals', onclick: function () { location.hash = '#/'; route(); } }))));
        return;
      }
      bar.appendChild(h('div', { class: 'inner' },
        h('div', { class: 'fields' },
          h('div', null, h('label', { class: 'f', for: 'reviewer', text: 'Reviewer' }), reviewer),
          h('div', null, h('label', { class: 'f', for: 'note', text: 'Note' }), note)),
        h('div', { class: 'btns' }, approveBtn, rejectBtn),
        h('div', { class: 'msgs' }, why, errBox)));
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
      var want = st.want; st.want = null;                                  // an action that knows where focus should go (arrow keys, removing a mapping) wins over "stay put"
      if (want) st.refocus = want;
      else if (fid) st.refocus = fid;
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
      submit('approve', body({ include: o.include, exclude: o.exclude, manual: manualList() }), function (res) {
        st.result = res; draw();
        var done = document.getElementById('result');                       // the Approve button is gone: put focus on the outcome instead of dropping it to the page
        if (done) done.focus();
      });
    });
    rejectBtn.addEventListener('click', function () {
      submit('reject', body(), function () { showDetail(id); });
    });
    reviewer.addEventListener('input', updateBar);
    note.addEventListener('input', updateBar);
    unsaved = function () {
      return !decided && !st.result && (Object.keys(st.decisions).length > 0 || Object.keys(st.manual).length > 0 || note.value.trim() !== '');
    };

    // ---- static parts
    clear(app);
    var detailHeading = h('h2', { text: p.source.name || '(unnamed file)' });
    app.appendChild(h('div', { class: 'row spread titlerow' },
      h('div', { class: 'row' }, h('button', { type: 'button', class: 'back', onclick: function () { location.hash = '#/'; }, text: '← All proposals' }), detailHeading),
      badge(STATE_BADGE, data.state.state)));
    arrived(p.source.name || 'Proposal', detailHeading, 'Proposal ' + (p.source.name || '') + ', ' + data.state.state);
    if (!data.integrity_ok) app.appendChild(h('div', { class: 'banner bad', role: 'alert', text: 'INTEGRITY CHECK FAILED: this proposal was modified after it was created. Do not approve it.' }));
    reqBanner = h('div', { class: 'banner warn', id: 'req-banner' });
    app.appendChild(reqBanner);

    // What the reviewer's decision depends on stays in view (who proposed, which tier, what left the machine); the rest is one click away.
    var wide = order.length > 8;
    app.appendChild(h('div', { class: 'statusline' }, summaryBox, wide ? filterLabel : null, wide ? nextBtn : null, jumpBtn));
    // The three facts a reviewer needs before deciding stay visible but take two lines; everything else is one click away.
    app.appendChild(h('p', { class: 'small metaline' },
      h('span', { class: 'muted', text: 'Proposed by ' }), h('span', { text: p.actor + ' · ' + when(p.created) + ' · ' + p.policy }),
      h('br'), h('span', { class: 'muted', text: 'Data sent out: ' }), h('span', { text: egressText((p.egress || {}).mode) })));
    // ONE folded section for everything that is not needed to decide: each separate fold costs a full row, and on a phone with
    // Linux fonts three of them pushed the first decision below the first screen.
    app.appendChild(h('div', { class: 'folds' },
      h('details', { id: 'more' }, h('summary', { text: 'Details and help' }), h('dl', { class: 'meta' },
        h('dt', { text: 'Source file' }), h('dd', { text: show(p.source.name || '') + ' · ' + (p.source.format || '?') + ' · sha256 ' + String(p.source.sha256 || '').slice(0, 12) }),
        h('dt', { text: 'Provider' }), h('dd', { text: (p.provider.name || '?') + (p.provider.model ? ' (' + p.provider.model + ')' : '') + ' · ' + (p.provider.locality || '') }),
        h('dt', { text: 'Thresholds' }), h('dd', { text: 'confidence ≥ ' + (p.thresholds || {}).min_confidence + ', value fit ≥ ' + pct((p.thresholds || {}).min_parse_rate) })),
      (p.egress && p.egress.payload) ? h('details', null, h('summary', { text: 'Exactly what was sent' }), h('pre', { id: 'payload', text: JSON.stringify(p.egress.payload, null, 2) })) : null,
      h('details', { id: 'legend' }, h('summary', { text: 'How to read this page' }),
        h('p', { class: 'small muted', text: 'One card per schema column; the ones that need you come first. Verified items are included by default, items that need review are left out until you include them, and refused items cannot be included. To use a different file column, choose it on the card: it is checked against the real values in the source file, and a note is required.' }),
        h('ul', { class: 'small' },
          h('li', { text: 'Values fit target type: how many of the non-empty values in the file column parse as the target type. This is the hard check; below the threshold the mapping is refused.' }),
          h('li', { text: 'Distinct values: share of values that are different from each other. Expect ~100% for an ID column and a low figure for a flag or category; it only matters when the target must be unique.' }),
          h('li', { text: 'Name similarity: how alike the file column name and the schema column name are. It is a hint, not proof.' }),
          h('li', { text: 'Other columns that also fit: more than one column would pass the type check, so the name and the data alone cannot decide; you must.' }))))));

    app.appendChild(h('h2', { class: 'sr-only', text: 'Mappings' }));
    app.appendChild(remapBox);
    app.appendChild(itemsBox);

    var others = h('div', { class: 'card' });
    others.appendChild(h('h3', { text: 'File columns not used' }));
    others.appendChild(h('p', { class: 'small', text: (p.unmapped_sources || []).length ? p.unmapped_sources.map(show).join(', ') : 'none' }));
    if ((p.unmapped_sources || []).length) others.appendChild(h('p', { class: 'small muted', text: 'Under a strict policy, unused file columns count as schema drift and block runs.' }));
    app.appendChild(others);    (p.warnings || []).forEach(function (w) { app.appendChild(h('div', { class: 'banner warn', text: w })); });
    app.appendChild(bar);
    draw();
  }

  // ------------------------------------------------------------------ run view ("Run a file")
  var RUN_STATUS = {
    COMPLETED: ['b-ok', 'Done', 'Every row passed the checks.'],
    COMPLETED_WITH_WARNINGS: ['b-warn', 'Done, with warnings', 'Finished. Some rows were set aside; the list and the reasons are in the files below.'],
    PENDING_SIGNOFF: ['b-warn', 'Waiting for a second person', 'Finished, but this policy needs a different person to sign it off (command: datapipe signoff).'],
    BLOCKED: ['b-bad', 'Stopped by the policy', 'The policy refused to produce results. The reasons are listed below.'],
    FAILED: ['b-bad', 'Could not finish', 'The run stopped with an error. The reasons are listed below.'],
    NEEDS_SCHEMA_CONFIRMATION: ['b-warn', 'Schema needs confirming', 'Review the schema, then run again.']
  };
  var RUN_ID_RE = /^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}$/;
  var runForm = { file: '', schema: '', analysis: '', policy: '', actor: '' };
  var POLICY_TEXT = {
    low: 'Low: personal columns are kept as they are. Use for data that is not sensitive.',
    business: 'Business: personal columns are masked in the outputs. A small share of bad rows is tolerated (5%).',
    regulated: 'Regulated: stricter. Any bad row stops the run, and a second person must sign off.'
  };

  function showRun(runId) {
    var seq = ++navSeq;
    clear(app);
    app.appendChild(h('p', { class: 'muted', text: 'Loading…' }));
    api('/api/run/options').then(function (o) { if (seq === navSeq) buildRun(o, runId, seq); })
      .catch(function (e) { if (seq === navSeq) showError(e); });
  }

  function opt(item) { return h('option', { value: item.id, text: item.name + '  (' + item.size + ', ' + item.where + ')' }); }
  function field(label, id, control, hint) {
    return h('div', { class: 'runfield' }, h('label', { class: 'f', for: id, text: label }), control, hint ? h('div', { class: 'small muted', text: hint }) : null);
  }

  function buildRun(o, runId, seq) {
    clear(app);
    var heading = h('h2', { text: 'Run a file' });
    app.appendChild(heading);
    arrived('Run a file', heading, 'Run a file');
    var st = o.settings || {};
    if (!runForm.actor) runForm.actor = st.actor || o.default_actor || '';
    if (!runForm.policy) runForm.policy = st.policy || 'business';
    var files = h('select', { id: 'run-file' }, h('option', { value: '', text: o.files.length ? 'Choose a file…' : 'No data files found' }), o.files.map(opt));
    var schemas = h('select', { id: 'run-schema' }, h('option', { value: '', text: o.schemas.length ? 'Choose a schema…' : 'No schema files found' }), o.schemas.map(opt));
    var analyses = h('select', { id: 'run-analysis' }, h('option', { value: '', text: 'No metrics (cleaning only)' }), o.analyses.map(opt));
    var policy = h('select', { id: 'run-policy' }, o.policies.map(function (p) { return h('option', { value: p.name, text: p.name }); }));
    var actor = h('input', { id: 'run-actor', type: 'text', maxlength: '80', autocomplete: 'off', value: runForm.actor });
    var go = h('button', { type: 'button', class: 'primary', id: 'run-go', text: 'Run' });
    var draft = h('button', { type: 'button', class: 'secondary small', id: 'run-draft', text: 'Draft a schema from the chosen file' });
    var sample = h('button', { type: 'button', class: 'secondary small', id: 'run-sample', text: 'Create a fake sample file to try' });
    var msg = h('div', { class: 'small', id: 'run-msg', role: 'status' });
    var policyNote = h('div', { class: 'small muted', id: 'policy-note' });
    var resultBox = h('div', { id: 'run-result' });
    [['file', files], ['schema', schemas], ['analysis', analyses], ['policy', policy]].forEach(function (x) {
      var wanted = runForm[x[0]];
      if (wanted && Array.prototype.some.call(x[1].options, function (op) { return op.value === wanted; })) x[1].value = wanted;
    });
    function remember() { runForm.file = files.value; runForm.schema = schemas.value; runForm.analysis = analyses.value; runForm.policy = policy.value; runForm.actor = actor.value; }
    var fit = h('div', { class: 'small', id: 'run-fit', role: 'status' });
    var fitSeq = 0;
    function refresh() {
      remember();
      policyNote.textContent = (POLICY_TEXT[policy.value] || '') + (st.max_file_mb ? ' Your Settings limit files to ' + st.max_file_mb + ' MB.' : '') + (st.max_memory_gb ? ' Memory limit from Settings: ' + st.max_memory_gb + ' GB.' : '');
      go.disabled = !(files.value && schemas.value && actor.value.trim());
      draft.disabled = !files.value;
    }
    // Before anything runs: does the chosen schema describe this file? If not, say so and offer the schema that does.
    function checkFit() {
      var mine = ++fitSeq;
      clear(fit);
      if (!files.value) return;
      api('/api/run/check', { method: 'POST', body: { file: files.value, schema: schemas.value } }).then(function (r) {
        if (mine !== fitSeq || !r.known) return;
        var best = r.best, ch = r.chosen;
        if (!schemas.value && best && best.missing_required_count === 0) {
          schemas.value = best.id;                                                              // nothing chosen yet: pick the schema that fits...
          if (!analyses.value) {                                                                 // ...and the metrics file that goes with it (schema_x.json -> analysis_x.json)
            var twin = best.name.replace(/^schema_/, 'analysis_');
            Array.prototype.forEach.call(analyses.options, function (op) { if (op.text.indexOf(twin + ' ') === 0) analyses.value = op.value; });
          }
          remember(); refresh(); checkFit(); return;
        }
        if (ch && ch.missing_required_count === 0) {
          fit.className = 'small ok-note';
          fit.textContent = '✓ This schema fits the file: ' + ch.matched + ' of its ' + ch.schema_columns + ' columns are in the file' + (ch.extra_in_file ? ' (' + ch.extra_in_file + ' file columns are not in the schema)' : '') + '.';
        } else if (ch) {
          fit.className = 'small warn-note';
          fit.appendChild(h('span', { text: '⚠ This schema does not fit this file: ' + ch.missing_required_count + ' required columns are missing (for example ' + ch.missing_required.slice(0, 4).join(', ') + '), and only ' + ch.matched + ' of ' + ch.schema_columns + ' schema columns are in the file. The run would be stopped. ' }));
          if (best && best.id !== ch.id && best.missing_required_count === 0) {
            fit.appendChild(h('button', { type: 'button', class: 'secondary small', text: 'Use ' + best.name + ' instead (fits)', onclick: function () { schemas.value = best.id; remember(); refresh(); checkFit(); } }));
          } else if (!best || best.missing_required_count > 0) {
            fit.appendChild(h('span', { text: 'None of the listed schemas fits; use “Draft a schema from the chosen file”.' }));
          }
        }
      }).catch(function () {});
    }
    [files, schemas].forEach(function (c) { c.addEventListener('change', checkFit); });
    [files, schemas, analyses, policy].forEach(function (c) { c.addEventListener('change', refresh); });
    actor.addEventListener('input', refresh);

    app.appendChild(h('div', { class: 'card' },
      h('p', { class: 'small muted', text: 'Everything stays on this computer. Pick a data file and the schema that describes it; the cleaned data, the bad rows and the metrics are written to a new run folder.' }),
      h('div', { class: 'runfields' },
        field('Data file', 'run-file', files, o.files.length ? 'No file here? See “Add your own files” below.' : 'No data files found. See “Add your own files” below.'),
        field('Schema (what each column should look like)', 'run-schema', schemas, 'No schema yet? Choose the file, then use the draft button below.'),
        field('Metrics (what the report should answer)', 'run-analysis', analyses),
        field('Policy', 'run-policy', policy), field('Your name (goes into the audit log)', 'run-actor', actor)),
      fit, policyNote, h('div', { class: 'btns' }, go, draft, sample), msg));
    app.appendChild(resultBox);

    var addBox = h('details', { id: 'add-files', class: 'folds' }, h('summary', { text: 'Add your own files' }),
      h('p', { class: 'small', text: 'The app only reads files from these folders (it never takes a typed path, so it cannot be pointed at anything else):' }),
      h('ul', { class: 'small' }, (o.folders || []).map(function (f) { return h('li', { class: 'mono', text: f }); })),
      h('p', { class: 'small', text: 'To use a file from somewhere else, copy it into one of these folders (the inbox folder inside the work folder is meant for that), or stop the app and start it again with another folder: python -m datapipe app --data-dir <folder> (repeat the option for several folders). Schema and metrics files are found in the same folders and in examples/.' }),
      h('button', { type: 'button', class: 'secondary small', id: 'run-refresh', text: 'Refresh the lists', onclick: function () { remember(); showRun(runId); } }));
    app.appendChild(addBox);

    var recent = h('div', { class: 'card' }, h('h3', { text: 'Earlier runs' }));
    if (!o.recent.length) recent.appendChild(h('p', { class: 'small muted', text: 'None yet.' }));
    o.recent.forEach(function (r) {
      var c = r.counts || {};
      recent.appendChild(h('div', { class: 'row small' },
        h('button', { type: 'button', class: 'back', text: (r.file || '(file)') + ' · ' + r.run_id.slice(0, 15), onclick: function () { location.hash = '#/run/' + r.run_id; } }),
        badge(RUN_STATUS_BADGE, r.status), c.rows_total !== undefined ? h('span', { class: 'muted', text: c.valid + ' valid of ' + c.rows_total + ' rows' }) : null));
    });
    app.appendChild(recent);
    refresh();
    checkFit();

    sample.addEventListener('click', function () {
      sample.disabled = true; msg.textContent = 'Creating a fake file of about 2 MB…';
      api('/api/run/sample', { method: 'POST', body: {} }).then(function (res) {
        runForm.file = res.id; runForm.schema = ''; runForm.analysis = '';
        showRun(runId);
      }).catch(function (e) { msg.textContent = e.message; sample.disabled = false; });
    });
    draft.addEventListener('click', function () {
      msg.textContent = 'Reading the file…'; draft.disabled = true;
      api('/api/run/draft-schema', { method: 'POST', body: { file: files.value } }).then(function (res) {
        msg.textContent = 'Draft saved (' + res.columns + ' columns): ' + res.saved_as + '. ' + res.note + ' Reload this page to pick it from the list.';
        refresh();
      }).catch(function (e) { msg.textContent = e.message; refresh(); });
    });
    go.addEventListener('click', function () {
      go.disabled = true; msg.textContent = 'Starting…';
      clear(resultBox);
      api('/api/run/start', { method: 'POST', body: { file: files.value, schema: schemas.value, analysis: analyses.value, policy: policy.value, actor: actor.value.trim() } })
        .then(function () { watch(seq, msg, go, refresh); })
        .catch(function (e) { msg.textContent = e.message; refresh(); });
    });

    if (runId && RUN_ID_RE.test(runId)) {
      api('/api/run/result?run=' + runId).then(function (r) { if (seq === navSeq) renderResult(resultBox, r); })
        .catch(function (e) { if (seq === navSeq) msg.textContent = e.message; });
    } else {
      api('/api/run/status').then(function (s) { if (seq === navSeq && s.state === 'running') watch(seq, msg, go, refresh); }).catch(function () {});
    }
  }

  var RUN_STATUS_BADGE = {};
  Object.keys(RUN_STATUS).forEach(function (k) { RUN_STATUS_BADGE[k] = [RUN_STATUS[k][0], RUN_STATUS[k][1]]; });

  // Poll while a run is going. Stops by itself when the reviewer leaves the page (navSeq changes).
  function watch(seq, msg, go, refresh) {
    go.disabled = true;
    api('/api/run/status').then(function (s) {
      if (seq !== navSeq) return;
      if (s.state === 'running') {
        msg.textContent = 'Running ' + (s.file || '') + '… ' + s.elapsed + ' s. A 100 MB file takes about 1–2 minutes; keep this page open.';
        setTimeout(function () { watch(seq, msg, go, refresh); }, 1500);
      } else if (s.state === 'done') {
        location.hash = '#/run/' + s.run_id;
      } else if (s.state === 'error') {
        msg.textContent = 'The run could not start: ' + (s.error || 'unknown error'); refresh();
      } else { refresh(); }
    }).catch(function (e) {
      if (seq !== navSeq) return;
      msg.textContent = e.message; refresh();
    });
  }

  function renderResult(box, r) {
    clear(box);
    var st = RUN_STATUS[r.status] || ['b-neutral', String(r.status), ''];
    var card = h('div', { class: 'card', id: 'run-summary' },
      h('div', { class: 'row spread' }, h('h3', { text: 'Result for ' + (r.source || 'file') }), h('span', { class: 'badge ' + st[0], text: st[1] })),
      h('p', { class: 'small', text: st[2] }));
    var c = r.counts;
    if (c) {
      card.appendChild(h('div', { class: 'row small' },
        h('span', { class: 'chip', text: c.rows_total + ' rows read' }), h('span', { class: 'chip', text: c.valid + ' valid' }),
        h('span', { class: 'chip', text: c.quarantined + ' set aside' }),
        r.reconciliation && r.reconciliation.checks ? h('span', { class: 'chip', text: r.reconciliation.checks + ' cross-checks, ' + r.reconciliation.mismatches + ' mismatches' }) : null));
    }
    if (r.reasons.length) card.appendChild(h('ul', { class: 'reasons' + (r.status === 'FAILED' || r.status === 'BLOCKED' ? ' rej' : '') }, r.reasons.map(function (x) { return h('li', { text: x }); })));
    if (r.warnings.length) card.appendChild(h('ul', { class: 'reasons' }, r.warnings.map(function (x) { return h('li', { text: x }); })));
    var labels = { 'clean.csv': 'Cleaned data (clean.csv)', 'quarantine.csv': 'Bad rows and why (quarantine.csv)', 'report.md': 'Report (report.md)', 'result.json': 'Result (result.json)', 'issues.json': 'Issues (issues.json)' };
    if (r.files.length && RUN_ID_RE.test(r.run_id)) {
      var links = h('div', { class: 'btns' });
      r.files.forEach(function (n) {
        if (!labels[n]) return;
        links.appendChild(h('a', { class: 'dl', href: '/api/run/download/' + r.run_id + '/' + n, text: labels[n] }));
      });
      card.appendChild(h('p', { class: 'small muted', text: 'Download (saved copies are also in the run folder):' }));
      card.appendChild(links);
    }
    var metricNames = Object.keys(r.metrics);
    if (metricNames.length && RUN_ID_RE.test(r.run_id)) {
      card.appendChild(h('p', { class: 'small muted', text: 'The metrics as CSV, to open in Excel or Numbers:' }));
      card.appendChild(h('div', { class: 'btns' }, h('a', { class: 'dl', id: 'dl-metrics-zip', href: '/api/run/metrics/' + r.run_id + '/all.zip', text: 'All metrics (CSV files in a .zip)' })));
    }
    card.appendChild(h('p', { class: 'small muted', text: 'Run folder: ' + r.folder }));
    if (r.outputs && r.outputs.clean_csv && r.policy !== 'low') card.appendChild(h('p', { class: 'small muted', text: 'Do not edit and re-save clean.csv: its checksum is recorded in the audit log.' }));
    box.appendChild(card);
    Object.keys(r.metrics).forEach(function (name) {
      var m = r.metrics[name];
      var numeric = m.columns.map(function (cn, i) { return m.rows.length > 0 && m.rows.every(function (row) { return /^-?[0-9]+(\.[0-9]+)?$/.test(row[i]); }); });
      var head = h('tr', null, m.columns.map(function (cn, i) { return h('th', { scope: 'col', class: numeric[i] ? 'num' : null, text: cn }); }));
      var table = h('table', { class: 'metric' }, h('caption', { text: name.replace(/_/g, ' ') }), h('thead', null, head),
        h('tbody', null, m.rows.map(function (row) { return h('tr', null, row.map(function (v, i) { return h('td', { class: numeric[i] ? 'num' : null, text: v }); })); })));
      var csvLink = (/^[A-Za-z0-9_-]{1,80}$/.test(name) && RUN_ID_RE.test(r.run_id)) ? h('a', { class: 'dl small', href: '/api/run/metrics/' + r.run_id + '/' + name + '.csv', text: 'Download this table (CSV)' }) : null;
      box.appendChild(h('div', { class: 'card tablecard' }, table, csvLink,
        m.total_rows > m.rows.length ? h('p', { class: 'small muted', text: 'Showing the first ' + m.rows.length + ' of ' + m.total_rows + ' rows. The report file has all of them.' }) : null));
    });
    box.scrollIntoView({ block: 'start' });
  }

  // ------------------------------------------------------------------ settings view (the gear)
  function applyTheme(theme) {
    var root = document.documentElement;
    if (theme === 'light' || theme === 'dark') root.setAttribute('data-theme', theme); else root.removeAttribute('data-theme');
    document.querySelector('meta[name=color-scheme]').setAttribute('content', theme === 'light' ? 'light' : (theme === 'dark' ? 'dark' : 'light dark'));
  }

  function showSettings() {
    var seq = ++navSeq;
    clear(app);
    app.appendChild(h('p', { class: 'muted', text: 'Loading…' }));
    api('/api/settings').then(function (d) { if (seq === navSeq) buildSettings(d.settings, d.info); })
      .catch(function (e) { if (seq === navSeq) showError(e); });
  }

  function buildSettings(s, info) {
    clear(app);
    var heading = h('h2', { text: 'Settings' });
    app.appendChild(heading);
    arrived('Settings', heading, 'Settings');
    var name = h('input', { id: 'set-actor', type: 'text', maxlength: '80', autocomplete: 'off', value: s.actor || '', placeholder: 'Your name' });
    var policy = h('select', { id: 'set-policy' }, Object.keys(info.policies).map(function (p) { return h('option', { value: p, text: p }); }));
    policy.value = s.policy;
    var maxFile = h('input', { id: 'set-maxfile', type: 'number', min: '1', step: 'any', inputmode: 'decimal', value: s.max_file_mb === null ? '' : String(s.max_file_mb) });
    var maxMem = h('input', { id: 'set-maxmem', type: 'number', min: '0.5', step: 'any', inputmode: 'decimal', value: s.max_memory_gb === null ? '' : String(s.max_memory_gb) });
    var theme = h('select', { id: 'set-theme' }, [['system', 'Follow my computer'], ['light', 'Light'], ['dark', 'Dark']].map(function (t) { return h('option', { value: t[0], text: t[1] }); }));
    theme.value = s.theme;
    var limitHint = h('div', { class: 'small muted', id: 'set-limit-hint' });
    var msg = h('div', { class: 'small', id: 'set-msg', role: 'status' });
    var save = h('button', { type: 'button', class: 'primary', id: 'set-save', text: 'Save settings' });
    function hint() {
      var p = info.policies[policy.value];
      limitHint.textContent = 'Left empty, the ' + policy.value + ' policy allows files up to ' + p.max_file_mb + ' MB and an estimated ' + p.max_memory_gb + ' GB of memory. ' +
        'Raise the memory limit only on a computer that really has that much free RAM (a file needs about 25 times its size).';
    }
    policy.addEventListener('change', hint); hint();
    function num(input) { var v = input.value.trim(); return v === '' ? null : (isNaN(Number(v)) ? v : Number(v)); }
    save.addEventListener('click', function () {
      save.disabled = true; msg.textContent = 'Saving…';
      api('/api/settings', { method: 'POST', body: { actor: name.value, policy: policy.value, theme: theme.value, max_file_mb: num(maxFile), max_memory_gb: num(maxMem) } })
        .then(function (res) {
          applyTheme(res.settings.theme);
          runForm.actor = ''; runForm.policy = '';                       // the Run page re-reads its defaults
          msg.textContent = 'Saved. The next run uses these settings.';
        }).catch(function (e) { msg.textContent = e.message; })
        .then(function () { save.disabled = false; });
    });
    app.appendChild(h('div', { class: 'card' },
      h('p', { class: 'small muted', text: 'Saved in the work folder on this computer, so they are still here the next time you start the app.' }),
      h('div', { class: 'runfields' },
        field('Your name (default for new runs)', 'set-actor', name),
        field('Default policy', 'set-policy', policy),
        field('Largest file to accept, in MB (empty = policy limit)', 'set-maxfile', maxFile),
        field('Memory limit in GB (empty = policy limit)', 'set-maxmem', maxMem),
        field('Appearance', 'set-theme', theme)),
      limitHint, h('div', { class: 'btns' }, save), msg));

    var audit = h('button', { type: 'button', class: 'secondary small', id: 'set-audit', text: 'Check the audit log', onclick: function () {
      auditMsg.textContent = 'Checking…';
      api('/api/settings/audit').then(function (r) { auditMsg.textContent = (r.ok ? 'OK: ' : 'PROBLEM: ') + r.records + ' records. ' + r.message; })
        .catch(function (e) { auditMsg.textContent = e.message; });
    } });
    var auditMsg = h('div', { class: 'small', id: 'set-audit-msg', role: 'status' });
    function row(dt, dd) { return [h('dt', { text: dt }), h('dd', { text: dd })]; }
    app.appendChild(h('div', { class: 'card' }, h('h3', { text: 'About this installation' }), h('dl', { class: 'meta' },
      row('Version', 'datapipe ' + info.version + (info.frozen ? ' (standalone program)' : '')),
      row('Engine', 'Python ' + info.python + ', DuckDB ' + info.duckdb),
      row('System', info.system),
      row('Work folder (results, audit log, settings)', info.workdir),
      row('Folders it reads data from', info.folders.length ? info.folders.join('   ') : '(none)')),
      h('div', { class: 'btns' }, audit), auditMsg));
  }

  // ------------------------------------------------------------------ routing
  function markNav(which) {
    document.getElementById('nav-run').setAttribute('aria-current', which === 'run' ? 'page' : 'false');
    document.getElementById('nav-review').setAttribute('aria-current', which === 'review' ? 'page' : 'false');
    document.getElementById('nav-settings').setAttribute('aria-current', which === 'settings' ? 'page' : 'false');
  }
  function route() {
    var m = /^#\/p\/([0-9a-f]{64})$/.exec(location.hash);
    var r = /^#\/run(?:\/([0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}))?$/.exec(location.hash);
    var gear = location.hash === '#/settings';
    markNav(gear ? 'settings' : (r ? 'run' : 'review'));
    if (gear) showSettings();
    else if (r) showRun(r[1]);
    else if (m && ID_RE.test(m[1])) showDetail(m[1]); else showList();
  }
  // Unsaved work (decisions, manual mappings, a typed note) lives only in this page: ask before reload, close or leaving the proposal.
  var currentHash = location.hash;
  window.addEventListener('beforeunload', function (ev) {
    if (unsaved && unsaved()) { ev.preventDefault(); ev.returnValue = ''; }
  });
  window.addEventListener('hashchange', function () {
    if (location.hash === currentHash) return;               // our own restore below
    if (unsaved && unsaved() && !window.confirm('You have decisions that are not saved yet. Leave this proposal and discard them?')) {
      location.hash = currentHash;
      return;
    }
    unsaved = null; currentHash = location.hash;
    route();
  });
  if (fixedReviewer) document.getElementById('whoami').textContent = 'reviewing as ' + fixedReviewer;
  route();
})();
</script>
</body>
</html>
"""


def render_page(nonce: str, csrf: str, fixed_reviewer_escaped: str, theme: str = "system") -> str:
    theme = theme if theme in ("light", "dark") else "system"          # only these three words ever reach the HTML
    return (_TEMPLATE.replace("{{NONCE}}", nonce).replace("{{CSRF}}", csrf)
            .replace("{{FIXED}}", fixed_reviewer_escaped)
            .replace("{{THEME_ATTR}}", "" if theme == "system" else f' data-theme="{theme}"')
            .replace("{{SCHEME}}", "light dark" if theme == "system" else theme))
