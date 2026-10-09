"""The single-page review UI. Everything (HTML, CSS, JS) is inline so a strict CSP can forbid all other sources.

Rule for the JavaScript below: untrusted text (column names, provider rationale, notes, file names) is only ever
put into the page with textContent / createTextNode. There is no innerHTML, no eval, no javascript: URLs.
"""
from .i18n import LANGUAGES, embedded

_TEMPLATE = r"""<!doctype html>
<html lang="en"{{THEME_ATTR}}>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="{{SCHEME}}">
<meta name="language" content="{{LANGPREF}}">
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
  --page-max:1440px;   /* the widest the page grows: a big screen gets more room, but a row is never stretched across all of it. The one number to change for a wider or narrower page */
  --measure:34em;      /* the longest line of running text: about 65 to 80 characters (WCAG 1.4.8 asks for at most 80). In em, not ch: the width of a digit varies between system fonts far more than the width of a letter, and 66ch held 90 characters a line on macOS */
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
main{max-width:var(--page-max);margin:0 auto;padding:var(--s4) var(--s4) var(--s6);overflow-wrap:anywhere}      /* file names and column names are one long token: they must wrap, never widen the page (WCAG 1.4.10) */
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
h2[tabindex="-1"]:focus,h3[tabindex="-1"]:focus{outline:none}
.settings-jump{display:flex;flex-wrap:wrap;gap:var(--s2);margin:0 0 var(--s3)}      /* not .jump: that is the review page's own button */
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
/* Running text keeps a readable line however wide the page is; boxes, rows, tables and form grids use the room. :where() keeps this rule weak, so a component can lift it. */
main :where(p,ul,ol,div.small:not(.row),.quote,.locked,.undecided,.superseded){max-width:var(--measure)}
.statusline p,.metaline,.card.click div.small{max-width:none}            /* a flex item that has to grow, the two facts a reviewer needs before deciding, and a list card's one line of details: all stay as laid out */
.empty p{margin-left:auto;margin-right:auto}                             /* centred in its card, not at the left of a centred block */
/* Cards go side by side once there is room for two. A lone card keeps its own width instead of stretching across the page; min(100%,36rem) stops one column overflowing a phone. */
.cardgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,36rem),1fr));gap:var(--s3);margin:0 0 var(--s3)}
.cardgrid>.card{margin:0}
.cardgrid>.empty{grid-column:1 / -1}
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
  .actionbar .inner{max-width:calc(var(--page-max) - 2 * var(--s4));margin:0 auto;gap:var(--s1) var(--s3);grid-template-columns:1fr auto;align-items:end}      /* the width of the page's own content, so the bar lines up with the cards above it */
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
header.top nav a{color:var(--text);text-decoration:none;padding:var(--s2) var(--s3);border-radius:var(--r-md);min-height:var(--tap-sm);display:inline-flex;align-items:center;gap:.3em}   /* the gap is the space between "Run" and "a file": flex items drop it */
header.top nav a[aria-current=page]{background:var(--accent);color:var(--accent-ink);font-weight:650}
header.top nav a:focus-visible,a.dl:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.runfields{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:var(--s3);margin:var(--s3) 0}
@media (min-width:900px){.runfields{grid-template-columns:repeat(3,minmax(0,1fr))}}      /* three to a row, however wide the page: room for a whole file name in each list, and the rows line up */
.runfield select,.runfield input{width:100%}
a.dl{display:inline-flex;align-items:center;min-height:var(--tap);padding:var(--s2) var(--s4);border:1px solid var(--line-strong);border-radius:var(--r-md);color:var(--text);text-decoration:none}
a.dl:hover{border-color:var(--accent)}
a.dl.main{background:var(--accent);color:var(--accent-ink);border-color:transparent;font-weight:650}
a.dl.small{min-height:var(--tap-sm);font-size:var(--fs-sm);margin-top:var(--s2)}
.tablecard{overflow-x:auto}
table.metric{border-collapse:collapse;width:auto;min-width:min(100%,30rem);font-size:var(--fs-sm)}      /* as wide as its columns need: on a wide page a value stays next to its label instead of far across the card; a table with many columns still scrolls inside its card */
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
  header.top nav a .navtext{display:none}                                  /* phones: short labels; the full names stay as aria-labels */
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
<nav aria-label="Sections"><a href="#/run" id="nav-run" aria-label="Run a file">Run<span class="navtext"> a file</span></a><a href="#/" id="nav-review" aria-label="Review mappings">Review<span class="navtext"> mappings</span></a><a href="#/settings" id="nav-settings" class="gear" aria-label="Settings"><span aria-hidden="true">⚙</span><span class="navtext"> Settings</span></a></nav>
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

  // ---- language. English text is the key; the Ukrainian table is filled in by the server. Only fixed program text goes through
  // t(); names, file contents, provider answers and notes are data and are never translated.
  var I18N = {{I18N}};
  var LANG = (function () {
    var pref = document.querySelector('meta[name=language]').content;
    if (pref === 'uk' || pref === 'en') return pref;
    var prefs = navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language || 'en'];
    return /^uk(-|$)/i.test(String(prefs[0])) ? 'uk' : 'en';          // "follow my computer": the browser's first language decides
  })();
  document.documentElement.setAttribute('lang', LANG);
  function has(s) { return Object.prototype.hasOwnProperty.call(I18N, s); }
  // t("Saved {0} rows in {1}", n, name): one pass, so a value that itself contains "{1}" is left alone.
  var MISSING = window.__i18nMissing = [];                               // phrases asked for but not in the table (the tests read this; empty means complete)
  function t(s) {
    var args = arguments;
    if (LANG === 'uk' && !has(s)) MISSING.push(s);
    var out = (LANG === 'uk' && has(s)) ? I18N[s] : (s.indexOf('||') < 0 ? s : s.slice(0, s.indexOf('||')));
    return out.replace(/\{(\d+)\}/g, function (m, i) { return +i + 1 < args.length ? String(args[+i + 1]) : m; });
  }
  // Sentences written by the server (some with a value inside). Translated when a table entry fits, otherwise shown as they are.
  var PATTERNS = null;
  function tm(msg) {
    if (LANG !== 'uk' || typeof msg !== 'string') return msg;
    if (has(msg)) return I18N[msg];
    if (PATTERNS === null) {
      PATTERNS = Object.keys(I18N).filter(function (k) { return /\{\d+\}/.test(k); }).sort(function (a, b) { return b.length - a.length; }).map(function (k) {
        var re = new RegExp('^' + k.split(/\{\d+\}/).map(function (p) { return p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }).join('([\\s\\S]+?)') + '$');
        return { re: re, nums: (k.match(/\{\d+\}/g) || []).map(function (x) { return x.slice(1, -1); }), to: I18N[k] };
      });
    }
    for (var i = 0; i < PATTERNS.length; i++) {
      var m = PATTERNS[i].re.exec(msg);
      if (m) return PATTERNS[i].to.replace(/\{(\d+)\}/g, function (x, n) { var at = PATTERNS[i].nums.indexOf(n); return at >= 0 ? m[at + 1] : x; });
    }
    return msg;
  }

  if (window.__i18nTest) window.__i18n = { t: t, tm: tm };                // only the tests set the flag; they check the table against the real messages

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
  (function localizeChrome() {
    if (LANG === 'en') return;
    document.querySelector('header.top nav').setAttribute('aria-label', t('Sections'));
    [['nav-run', t('Run||menu'), t(' a file'), t('Run a file')], ['nav-review', t('Review'), t(' mappings'), t('Review mappings')], ['nav-settings', null, t(' Settings'), t('Settings')]].forEach(function (x) {
      var a = document.getElementById(x[0]);
      a.setAttribute('aria-label', x[3]);
      if (x[1] !== null) a.firstChild.nodeValue = x[1];
      a.querySelector('.navtext').textContent = x[2];
    });
  })();
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
  function unitText(s) { return String(s).replace(/ KB$/, ' ' + t('KB')).replace(/ MB$/, ' ' + t('MB')); }
  function pct(x) { return (x === null || x === undefined) ? t('n/a') : Math.round(x * 100) + '%'; }
  function when(iso) { var d = new Date(iso); return isNaN(d) ? String(iso || '') : d.toISOString().slice(0, 16).replace('T', ' ') + ' UTC'; }

  function api(path, opts) {
    var o = opts || {};
    var headers = { 'Accept': 'application/json' };
    if (o.body !== undefined) { headers['Content-Type'] = 'application/json'; headers['X-DataPipe-CSRF'] = csrf; }
    return fetch(path, { method: o.method || 'GET', credentials: 'same-origin', headers: headers,
                         body: o.body === undefined ? undefined : JSON.stringify(o.body) })
      .catch(function () {
        throw new Error(t('Cannot reach the review server. Check that “datapipe review” is still running in your terminal, then try again. Nothing you entered on this page has been lost.'));
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
    if (status === 401) return t('This page is no longer signed in (the review server was restarted, or the link was opened in another browser). Open the link printed by “datapipe review” again.');
    if (status === 403) return t('The server refused this request because the page is out of date (the review server was restarted). Reload the page, then repeat your last step.');
    if (status === 421) return t('Open this page with the address printed by “datapipe review” (127.0.0.1), not through another host name.');
    if (status === 500) return t('The review server hit an internal error. Look at the terminal where “datapipe review” runs; nothing was saved.');
    if (status === 413) return t('The note or selection is too large to send. Shorten the note and try again.');
    return tm(msg) || t('The server answered with an error ({0}). Reload the page and try again.', status);
  }

  var STATE_BADGE = { pending: ['b-warn', t('Pending review')], approved: ['b-ok', t('Approved')], rejected: ['b-bad', t('Rejected')] };
  var STATUS_BADGE = { accepted: ['b-ok', t('Verified')], needs_review: ['b-warn', t('Needs your review')], rejected: ['b-bad', t('Rejected by verification')] };
  function egressText(mode) {
    if (mode === 'none') return t('Nothing left this machine (offline provider)');
    if (mode === 'local') return t('Column names and value shapes were sent to a model on this machine; nothing left it');
    if (mode === 'shapes') return t('Column names and value shapes were sent to the LLM');
    if (mode === 'shapes+samples') return t('Column names, value shapes and a few sample values were sent to the LLM');
    return String(mode);
  }
  function badge(map, key) { var b = map[key] || ['b-neutral', String(key)]; return h('span', { class: 'badge ' + b[0], text: b[1] }); }

  // ------------------------------------------------------------------ list view
  // Every navigation gets a number; an answer that arrives for an older navigation is dropped, so a slow list can never paint over the proposal the reviewer just opened.
  var navSeq = 0;
  function showList() {
    var seq = ++navSeq;
    clear(app);
    app.appendChild(h('p', { class: 'muted', text: t('Loading proposals…') }));
    api('/api/proposals').then(function (data) {
      if (seq !== navSeq) return;
      clear(app);
      if (data.reviewer_fixed) document.getElementById('whoami').textContent = t('reviewing as {0}', data.reviewer_fixed);
      var listHeading = h('h2', { text: t('Mapping proposals') });
      app.appendChild(listHeading);
      var pendingCount = data.proposals.filter(function (p) { return p.state === 'pending'; }).length;
      arrived(t('Proposals'), listHeading, t('{0} proposals, {1} pending review', data.proposals.length, pendingCount));
      if (!data.proposals.length) {
        app.appendChild(h('div', { class: 'card empty' },
          h('p', { text: t('No proposals found.') }),
          h('p', { class: 'small', text: t('Create one with: datapipe map <file> --schema <schema.json>. Looking in: {0}', data.mappings_dir) })));
      }
      var grid = h('div', { class: 'cardgrid' });
      data.proposals.forEach(function (p) {
        var s = p.summary || {};
        var card = h('div', { class: 'card click', role: 'link', tabindex: '0' },
          h('div', { class: 'row spread' },
            h('h3', { text: p.source_name || t('(unnamed file)') }), badge(STATE_BADGE, p.state)),
          h('div', { class: 'row small muted' },
            h('span', { class: 'chip', text: t('{0} verified', s.accepted || 0) }),
            h('span', { class: 'chip', text: t('{0} need review', s.needs_review || 0) }),
            h('span', { class: 'chip', text: t('{0} rejected', s.rejected || 0) }),
            p.required_unmapped ? h('span', { class: 'badge b-bad', text: t('{0} required unmapped', p.required_unmapped) }) : null,
            p.integrity_ok ? null : h('span', { class: 'badge b-bad', text: t('INTEGRITY FAILED') })),
          h('div', { class: 'small muted', text: t('Proposed by {0} · {1}{2} · policy {3} · {4}', p.actor, p.provider || '?', p.model ? ' (' + p.model + ')' : '', p.policy, when(p.created)) }));
        var open = function () { location.hash = '#/p/' + p.id; };
        card.addEventListener('click', open);
        card.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); } });
        grid.appendChild(card);
      });
      if (data.proposals.length) app.appendChild(grid);
      if (data.skipped.length) {
        app.appendChild(h('details', null, h('summary', { text: t('{0} file(s) skipped', data.skipped.length) }),
          h('ul', null, data.skipped.map(function (x) { return h('li', { class: 'small', text: x.file + ': ' + tm(x.error) }); }))));
      }
    }).catch(function (e) { if (seq === navSeq) showError(e); });
  }

  function showError(e) {
    clear(app);
    app.appendChild(h('div', { class: 'banner bad', role: 'alert', text: e.message || String(e) }));
    app.appendChild(h('div', { class: 'btns' },
      h('button', { type: 'button', class: 'secondary', onclick: function () { route(); }, text: t('Try again') }),
      h('button', { type: 'button', class: 'back', onclick: function () { if (location.hash === '#/' || !location.hash) route(); else location.hash = '#/'; }, text: t('← Back to proposals') })));
    arrived(t('Error'), null, t('Error: {0}', e.message || String(e)));
  }

  // ------------------------------------------------------------------ detail view
  function showDetail(id) {
    var seq = ++navSeq;
    clear(app);
    app.appendChild(h('p', { class: 'muted', text: t('Loading…') }));
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
                                placeholder: t('Your name'), value: data.reviewer_fixed || '' });
    if (data.reviewer_fixed) { reviewer.value = data.reviewer_fixed; reviewer.readOnly = true; }
    var note = h('textarea', { id: 'note', maxlength: '500', placeholder: t('Why? (required for overrides and rejections)') });
    var approveBtn = h('button', { class: 'primary', id: 'approve', type: 'button', text: t('Approve and create schema') });
    var rejectBtn = h('button', { class: 'secondary danger', id: 'reject', type: 'button', text: t('Reject proposal') });
    var why = h('div', { class: 'why', id: 'why' });
    var errBox = h('div', { class: 'err', id: 'errbox', role: 'alert' });
    var itemsBox = h('div', { id: 'items', class: 'cardgrid' });
    var summaryBox = h('p', { class: 'small', id: 'summary' });
    var jumpBtn = h('button', { type: 'button', class: 'secondary small jump', text: t('Go to approve / reject'),
                                onclick: function () { bar.scrollIntoView({ block: 'end' }); (decided || st.result ? bar : reviewer).focus(); } });
    var remapBox = h('div', { id: 'remap' });
    var bar = h('div', { class: 'actionbar' });
    // Wide schemas (dozens of columns): jump to the next column that still needs a decision, or hide the ones the checks already settled.
    var nextBtn = h('button', { type: 'button', class: 'secondary small', id: 'next-open', text: t('Next to decide'), onclick: function () {
      var c = itemsBox.querySelector('[data-open="1"]');
      if (!c) return;
      c.scrollIntoView({ block: 'center' });
      var f = c.querySelector('button[tabindex="0"], select');
      if (f) f.focus({ preventScroll: true });
    } });
    var filterCount = h('span');
    var onlyBox = h('input', { type: 'checkbox', id: 'only-open', onchange: function () { st.onlyOpen = onlyBox.checked; drawItems(); } });
    var filterLabel = h('label', { for: 'only-open' }, onlyBox, h('span', { text: t('Only columns that need me') }), filterCount);

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
      if (!data.integrity_ok) out.push(t('the proposal failed its integrity check'));
      if (!reviewer.value.trim()) out.push(t('enter your name'));
      var un = uncovered();
      if (un.length) out.push(t('required column(s) not mapped: {0}', un.join(', ')));
      var o = overrides();
      if ((o.include.length || o.exclude.length || manualList().length) && !note.value.trim()) out.push(t('add a note explaining your overrides and manual mappings'));
      return out;
    }
    function updateBar() {
      if (decided || st.result) return;
      var pr = problems();
      approveBtn.disabled = st.busy || pr.length > 0;
      rejectBtn.disabled = st.busy || !reviewer.value.trim() || !note.value.trim() || !data.integrity_ok;
      var same = reviewer.value.trim() && person(reviewer.value) === person(p.actor);
      var msg = pr.length ? t('To approve: {0}.', pr.join('; ')) : t('Ready to approve.');
      var rej = [];
      if (!reviewer.value.trim()) rej.push(t('your name'));
      if (!note.value.trim()) rej.push(t('a note'));
      if (!data.integrity_ok) msg = t('This proposal was changed after it was created, so it can be neither approved nor rejected here. Create a fresh one with “datapipe map”.');
      else if (rej.length) msg += ' ' + t('To reject, add {0}.', rej.join(t(' and ')));
      if (same) msg += ' ' + t('Note: the reviewer must be a different person than the proposer ({0}).', p.actor);
      why.textContent = msg;
      errBox.textContent = st.error;
    }

    // Two-option radio group: one tab stop (the checked option, or the first while nothing is chosen), arrow keys switch and move focus.
    // `chosen` is true / false, or undefined while the reviewer has not decided yet (then neither option looks selected).
    var SEG_LABEL = { Include: t('Include'), Exclude: t('Exclude') };      // the English words stay in the focus ids; only what is shown changes
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
                             }, text: SEG_LABEL[label] });
      }
      return h('div', { class: 'seg', role: 'radiogroup', 'aria-label': t('Decision for {0}', show(target)) }, opt('Include', true), opt('Exclude', false));
    }

    function evidenceChips(e, withAlternatives) {
      var chips = h('div', { class: 'evidence' });
      chips.appendChild(h('span', { class: 'chip', text: t('Values fit target type: {0} of {1}', pct(e.parse_rate), e.non_null) }));
      chips.appendChild(h('span', { class: 'chip', text: t('Distinct values: {0}', pct(e.distinct_ratio)) }));
      chips.appendChild(h('span', { class: 'chip', text: t('Name similarity: {0}', e.name_score === undefined ? t('n/a') : pct(e.name_score)) }));
      if (withAlternatives && e.alternatives && e.alternatives.length) chips.appendChild(h('span', { class: 'chip', text: t('Other columns that also fit: {0}', e.alternatives.join(', ')) }));
      return chips;
    }
    // The target column name is the card's heading (so a screen reader can list and jump between the columns).
    function nameRow(source, target, required, rightBadge, hid) {
      return h('div', { class: 'row spread' },
        h('div', { class: 'row' },
          source === null ? null : h('span', { class: 'name', 'data-role': 'source', text: show(source) }),
          source === null ? null : h('span', { class: 'arrow' }, h('span', { 'aria-hidden': 'true', text: '→' }), h('span', { class: 'sr-only', text: t(' maps to ') })),
          h('h3', { id: hid }, h('span', { class: 'name', 'data-role': 'target', text: show(target) })),
          required ? h('span', { class: 'badge b-neutral', text: t('required') }) : null),
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
        card.appendChild(nameRow(man.source, c.name, c.required, h('span', { class: 'badge b-manual', text: t('Manual (by you)') }), hid));
        card.appendChild(evidenceChips(man.evidence, false));
        if (man.evidence.warnings && man.evidence.warnings.length) {
          card.appendChild(h('ul', { class: 'reasons' }, man.evidence.warnings.map(function (w) { return h('li', { text: tm(w) }); })));
        }
        card.appendChild(h('div', { class: 'quote', text: t('Chosen by you and checked against the source file just now. A note is required when you approve.') }));
        if (item) card.appendChild(h('div', { class: 'superseded', 'data-role': 'superseded', text: t('This replaces the proposed mapping from {0}.', show(item.source)) }));
        if (!decided) card.appendChild(h('button', { type: 'button', class: 'secondary small', text: t('Remove manual mapping'), 'data-fid': 'rm:' + c.name,
          onclick: function () { delete st.manual[c.name]; st.want = 'sel:' + c.name; draw(); } }));
      } else if (item) {
        var locked = decided || item.status === 'rejected';
        var e = item.evidence || {};
        card.appendChild(nameRow(item.source, c.name, c.required, badge(STATUS_BADGE, item.status), hid));
        var conf = h('div', { class: 'row' });
        var bar_ = h('div', { class: 'bar', role: 'img', 'aria-label': t('confidence {0}', pct(item.confidence)) }, h('span'));
        bar_.firstChild.style.width = Math.max(0, Math.min(100, Math.round(item.confidence * 100))) + '%';
        conf.appendChild(h('span', { class: 'small muted', text: t('Provider confidence') }));
        conf.appendChild(bar_);
        conf.appendChild(h('span', { class: 'small', text: pct(item.confidence) }));
        card.appendChild(h('div', { class: 'row' }, conf));
        card.appendChild(item.evidence ? evidenceChips(e, true) : h('div', { class: 'evidence' }));
        if (item.reasons && item.reasons.length) {
          card.appendChild(h('ul', { class: 'reasons' + (item.status === 'rejected' ? ' rej' : '') },
            item.reasons.map(function (r) { return h('li', { text: tm(r) }); })));
        }
        if (item.rationale) {
          card.appendChild(h('div', { class: 'quote' }, h('span', { class: 'small', text: t('Provider says (unverified text): ') }), h('span', { text: item.rationale })));
        }
        if (item.status === 'rejected') {
          card.appendChild(h('div', { class: 'locked', text: t('Rejected by the deterministic checks. It cannot be included.') }));
        } else {
          var setTo = function (v) { return function () { if (locked) return; st.decisions[item.target] = v; draw(); }; };
          // A verified item starts as Include. An item that needs review starts with NOTHING chosen (it is left out unless you include it).
          var chosen = item.status === 'accepted' ? included(item) : st.decisions[item.target];
          card.appendChild(segGroup(item.target, chosen, locked, setTo));
          if (chosen === undefined && !locked) card.appendChild(h('div', { class: 'undecided', 'data-role': 'undecided', text: t('Not decided yet. If you leave it, this column is left out.') }));
        }
      } else {
        card.appendChild(nameRow(null, c.name, c.required,
          h('span', { class: 'badge ' + (c.required ? 'b-bad' : 'b-neutral'), text: c.required ? t('Required, not mapped') : t('Not mapped') }), hid));
        card.appendChild(h('div', { class: 'locked', text: c.required ? t('The provider found no source for this required column. Approval is blocked until you choose one.') : t('No source column chosen.') }));
      }
      if (!decided && remap.available) card.appendChild(changeSource(c, n));
      return card;
    }

    function currentText(c) {
      if (st.manual[c.name]) return t('Manual: ← {0}', show(st.manual[c.name].source));
      var it = itemFor[c.name];
      if (it && it.status !== 'rejected') {
        if (included(it)) return it.status === 'accepted' ? t('Proposed: ← {0} (verified)', show(it.source)) : t('Proposed: ← {0} (reviewed by you)', show(it.source));
        return st.decisions[it.target] === undefined ? t('Proposed: ← {0} (not decided yet, left out for now)', show(it.source)) : t('Proposed: ← {0} (currently excluded)', show(it.source));
      }
      return t('Not mapped');
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
      sel.appendChild(h('option', { value: '', text: st.manual[c.name] ? t('Remove my manual mapping')
                                                     : (hasProposal ? t('Keep the proposed mapping (or choose another)…') : t('Choose a file column…')) }));
      cols.forEach(function (name, idx) {
        var owner = usedBy(name, c.name);
        sel.appendChild(h('option', { value: String(idx), text: show(name) + (owner ? '   ' + t('(used for {0})', show(owner)) : ''), disabled: !!owner }));
      });
      if (st.manual[c.name]) sel.value = String(cols.indexOf(st.manual[c.name].source));
      sel.addEventListener('change', function () {
        if (sel.value === '') { delete st.manual[c.name]; st.remapErr[c.name] = ''; draw(); }
        else checkManual(c.name, Number(sel.value));
      });
      var box = h('div', { class: 'change' },
        h('div', { class: 'cur', 'data-role': 'current', text: currentText(c) }),
        h('div', null, h('label', { class: 'f', for: selId, text: t('Use a different file column') }), sel));
      if (st.checking[c.name]) box.appendChild(h('div', { class: 'small muted full', text: t('Checking against the source file…') }));
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
        ? [t('{0} need your decision', nDecide)].concat(nOpen ? [t('{0} required column(s) unmapped', nOpen)] : [],
            [t('{0} verified', nVerified), t('{0} refused by the checks', nRefused)], nManual ? [t('{0} mapped by you', nManual)] : []).join(' · ')
        : t('Integrity check failed: the verdicts below cannot be trusted, so this proposal cannot be approved.');
      // "Only the columns that need me" is decided from the server's verdicts (rank 0), not from clicks, so a card never vanishes under the reviewer's hands.
      var shown = order.filter(function (x) { return !st.onlyOpen || x.rank === 0; });
      shown.forEach(function (x) { itemsBox.appendChild(targetCard(x.c, x.n)); });
      if (!order.length) itemsBox.appendChild(h('div', { class: 'card empty', text: t('The schema has no columns.') }));
      else if (!shown.length) itemsBox.appendChild(h('div', { class: 'card empty', text: t('Every column is verified or refused by the checks; nothing needs your decision.') }));
      nextBtn.disabled = itemsBox.querySelector('[data-open="1"]') === null;
      filterCount.textContent = ' ' + t('({0} of {1})', order.filter(function (x) { return x.rank === 0; }).length, order.length);
    }
    function drawRemap() {
      clear(remapBox);
      if (decided || remap.available) return;
      remapBox.appendChild(h('div', { class: 'card' }, h('p', { class: 'small', id: 'remap-unavailable',
        text: t('Choosing a different file column needs the original data file to verify your choice.') + (remap.reason ? ' ' + tm(remap.reason) : '') }),
        h('p', { class: 'small muted', text: t('Start the review with --data-dir <folder containing the file>.') })));
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
          text: (d.state === 'approved' ? t('Approved by {0} on {1}', d.by, when(d.ts)) : t('Rejected by {0} on {1}', d.by, when(d.ts))) + (d.note ? ' — ' + d.note : '') +
                (d.schema_file ? t('. Schema file: {0}', d.schema_file) : '') }));
        bar.appendChild(box);
        return;
      }
      if (st.result) {
        var r = st.result;
        // Absolute paths, so the command works from any folder (the schema lives in the review's work folder, not the current one).
        function q(s) { return /^[A-Za-z0-9_@%+=:,.\/\\-]+$/.test(s) ? s : '"' + s.replace(/"/g, '\\"') + '"'; }
        var cmd = 'python -m datapipe --workdir ' + q(r.workdir || 'work') + ' run <your-file> --schema ' + q(r.schema_path || r.schema_file) +
                  ' --policy ' + p.policy + ' --analysis <analysis.json>';
        var dl = h('button', { class: 'secondary', type: 'button', id: 'download', text: t('Download schema JSON'), onclick: function () {
          var blob = new Blob([JSON.stringify(r.schema, null, 2) + '\n'], { type: 'application/json' });
          var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = r.schema_file.split('/').pop();
          document.body.appendChild(a); a.click(); a.remove();
        } });
        bar.appendChild(h('div', { class: 'inner', id: 'result', tabindex: '-1' },
          h('div', { class: 'banner ok', role: 'status', text: t('Approved. Schema created: {0} (fingerprint {1})', r.schema_file, r.fingerprint.slice(0, 12)) }),
          h('div', { class: 'small muted', text: t('Next, run your data file with this schema. Replace <your-file> and <analysis.json> with your own files:') }),
          h('div', { class: 'cmd', text: cmd }), h('div', { class: 'btns' }, dl,
            h('button', { class: 'secondary', type: 'button', text: t('Back to proposals'), onclick: function () { location.hash = '#/'; route(); } }))));
        return;
      }
      bar.appendChild(h('div', { class: 'inner' },
        h('div', { class: 'fields' },
          h('div', null, h('label', { class: 'f', for: 'reviewer', text: t('Reviewer') }), reviewer),
          h('div', null, h('label', { class: 'f', for: 'note', text: t('Note') }), note)),
        h('div', { class: 'btns' }, approveBtn, rejectBtn),
        h('div', { class: 'msgs' }, why, errBox)));
      updateBar();
    }

    var reqBanner = null;
    function drawBanner() {
      if (!reqBanner) return;
      var open = (p.unmapped_targets || []).filter(function (t) { return t.required && !st.manual[t.target]; });
      reqBanner.hidden = !open.length;
      reqBanner.textContent = open.length ? t('Required columns without any mapping: {0}. Approval is blocked until they are mapped (use “Use a different file column” on the card).', open.map(function (u) { return u.target; }).join(', ')) : '';
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
    var detailHeading = h('h2', { text: p.source.name || t('(unnamed file)') });
    app.appendChild(h('div', { class: 'row spread titlerow' },
      h('div', { class: 'row' }, h('button', { type: 'button', class: 'back', onclick: function () { location.hash = '#/'; }, text: t('← All proposals') }), detailHeading),
      badge(STATE_BADGE, data.state.state)));
    arrived(p.source.name || t('Proposal'), detailHeading, t('Proposal {0}, {1}', p.source.name || '', (STATE_BADGE[data.state.state] || [0, data.state.state])[1]));
    if (!data.integrity_ok) app.appendChild(h('div', { class: 'banner bad', role: 'alert', text: t('INTEGRITY CHECK FAILED: this proposal was modified after it was created. Do not approve it.') }));
    reqBanner = h('div', { class: 'banner warn', id: 'req-banner' });
    app.appendChild(reqBanner);

    // What the reviewer's decision depends on stays in view (who proposed, which tier, what left the machine); the rest is one click away.
    var wide = order.length > 8;
    app.appendChild(h('div', { class: 'statusline' }, summaryBox, wide ? filterLabel : null, wide ? nextBtn : null, jumpBtn));
    // The three facts a reviewer needs before deciding stay visible but take two lines; everything else is one click away.
    app.appendChild(h('p', { class: 'small metaline' },
      h('span', { class: 'muted', text: t('Proposed by ') }), h('span', { text: p.actor + ' · ' + when(p.created) + ' · ' + p.policy }),
      h('br'), h('span', { class: 'muted', text: t('Data sent out: ') }), h('span', { text: egressText((p.egress || {}).mode) })));
    // ONE folded section for everything that is not needed to decide: each separate fold costs a full row, and on a phone with
    // Linux fonts three of them pushed the first decision below the first screen.
    app.appendChild(h('div', { class: 'folds' },
      h('details', { id: 'more' }, h('summary', { text: t('Details and help') }), h('dl', { class: 'meta' },
        h('dt', { text: t('Source file') }), h('dd', { text: show(p.source.name || '') + ' · ' + (p.source.format || '?') + ' · sha256 ' + String(p.source.sha256 || '').slice(0, 12) }),
        h('dt', { text: t('Provider') }), h('dd', { text: (p.provider.name || '?') + (p.provider.model ? ' (' + p.provider.model + ')' : '') + ' · ' + (p.provider.locality || '') }),
        h('dt', { text: t('Thresholds') }), h('dd', { text: t('confidence ≥ {0}, value fit ≥ {1}', (p.thresholds || {}).min_confidence, pct((p.thresholds || {}).min_parse_rate)) })),
      (p.egress && p.egress.payload) ? h('details', null, h('summary', { text: t('Exactly what was sent') }), h('pre', { id: 'payload', text: JSON.stringify(p.egress.payload, null, 2) })) : null,
      h('details', { id: 'legend' }, h('summary', { text: t('How to read this page') }),
        h('p', { class: 'small muted', text: t('One card per schema column; the ones that need you come first. Verified items are included by default, items that need review are left out until you include them, and refused items cannot be included. To use a different file column, choose it on the card: it is checked against the real values in the source file, and a note is required.') }),
        h('ul', { class: 'small' },
          h('li', { text: t('Values fit target type: how many of the non-empty values in the file column parse as the target type. This is the hard check; below the threshold the mapping is refused.') }),
          h('li', { text: t('Distinct values: share of values that are different from each other. Expect ~100% for an ID column and a low figure for a flag or category; it only matters when the target must be unique.') }),
          h('li', { text: t('Name similarity: how alike the file column name and the schema column name are. It is a hint, not proof.') }),
          h('li', { text: t('Other columns that also fit: more than one column would pass the type check, so the name and the data alone cannot decide; you must.') }))))));

    app.appendChild(h('h2', { class: 'sr-only', text: t('Mappings') }));
    app.appendChild(remapBox);
    app.appendChild(itemsBox);

    var others = h('div', { class: 'card' });
    others.appendChild(h('h3', { text: t('File columns not used') }));
    others.appendChild(h('p', { class: 'small', text: (p.unmapped_sources || []).length ? p.unmapped_sources.map(show).join(', ') : t('none') }));
    if ((p.unmapped_sources || []).length) others.appendChild(h('p', { class: 'small muted', text: t('Under a strict policy, unused file columns count as schema drift and block runs.') }));
    app.appendChild(others);    (p.warnings || []).forEach(function (w) { app.appendChild(h('div', { class: 'banner warn', text: tm(w) })); });
    app.appendChild(bar);
    draw();
  }

  // ------------------------------------------------------------------ run view ("Run a file")
  var RUN_STATUS = {
    COMPLETED: ['b-ok', t('Done'), t('Every row passed the checks.')],
    COMPLETED_WITH_WARNINGS: ['b-warn', t('Done, with warnings'), t('Finished. Some rows were set aside; the list and the reasons are in the files below.')],
    PENDING_SIGNOFF: ['b-warn', t('Waiting for a second person'), t('Finished, but this policy needs a different person to sign it off (command: datapipe signoff).')],
    BLOCKED: ['b-bad', t('Stopped by the policy'), t('The policy refused to produce results. The reasons are listed below.')],
    FAILED: ['b-bad', t('Could not finish'), t('The run stopped with an error. The reasons are listed below.')],
    NEEDS_SCHEMA_CONFIRMATION: ['b-warn', t('Schema needs confirming'), t('Review the schema, then run again.')]
  };
  var RUN_ID_RE = /^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}$/;
  var runForm = { file: '', schema: '', analysis: '', policy: '', actor: '' };
  var POLICY_TEXT = {
    low: t('Low: personal columns are kept as they are. Use for data that is not sensitive.'),
    business: t('Business: personal columns are masked in the outputs. A small share of bad rows is tolerated (5%).'),
    regulated: t('Regulated: stricter. Any bad row stops the run, and a second person must sign off.')
  };

  function showRun(runId) {
    var seq = ++navSeq;
    clear(app);
    app.appendChild(h('p', { class: 'muted', text: t('Loading…') }));
    api('/api/run/options').then(function (o) { if (seq === navSeq) buildRun(o, runId, seq); })
      .catch(function (e) { if (seq === navSeq) showError(e); });
  }

  function opt(item) { return h('option', { value: item.id, text: item.name + '  (' + unitText(item.size) + ', ' + (item.chosen ? t('chosen by you') + ' · ' + item.folder : item.where) + ')' }); }
  function field(label, id, control, hint) {
    return h('div', { class: 'runfield' }, h('label', { class: 'f', for: id, text: label }), control, hint ? h('div', { class: 'small muted', text: hint }) : null);
  }

  function buildRun(o, runId, seq) {
    clear(app);
    var heading = h('h2', { text: t('Run a file') });
    app.appendChild(heading);
    arrived(t('Run a file'), heading, t('Run a file'));
    var st = o.settings || {};
    if (!runForm.actor) runForm.actor = st.actor || o.default_actor || '';
    if (!runForm.policy) runForm.policy = st.policy || 'business';
    var files = h('select', { id: 'run-file' }, h('option', { value: '', text: o.files.length ? t('Choose a file…') : t('No data files found') }), o.files.map(opt));
    var schemas = h('select', { id: 'run-schema' }, h('option', { value: '', text: o.schemas.length ? t('Choose a schema…') : t('No schema files found') }), o.schemas.map(opt));
    var analyses = h('select', { id: 'run-analysis' }, h('option', { value: '', text: t('No metrics (cleaning only)') }), o.analyses.map(opt));
    var policy = h('select', { id: 'run-policy' }, o.policies.map(function (p) { return h('option', { value: p.name, text: p.name }); }));
    var actor = h('input', { id: 'run-actor', type: 'text', maxlength: '80', autocomplete: 'off', value: runForm.actor });
    var go = h('button', { type: 'button', class: 'primary', id: 'run-go', text: t('Run') });
    var draft = h('button', { type: 'button', class: 'secondary small', id: 'run-draft', text: t('Draft a schema from the chosen file') });
    var sample = h('button', { type: 'button', class: 'secondary small', id: 'run-sample', text: t('Create a fake sample file to try') });
    var msg = h('div', { class: 'small', id: 'run-msg', role: 'status' });
    if (runForm.flash) { msg.textContent = runForm.flash; runForm.flash = ''; }
    var policyNote = h('div', { class: 'small muted', id: 'policy-note' });
    var resultBox = h('div', { id: 'run-result' });
    [['file', files], ['schema', schemas], ['analysis', analyses], ['policy', policy]].forEach(function (x) {
      var wanted = runForm[x[0]];
      if (wanted && Array.prototype.some.call(x[1].options, function (op) { return op.value === wanted; })) x[1].value = wanted;
    });
    function remember() { runForm.file = files.value; runForm.schema = schemas.value; runForm.analysis = analyses.value; runForm.policy = policy.value; runForm.actor = actor.value; }
    var fit = h('div', { class: 'small', id: 'run-fit', role: 'status' });
    var metricsFit = h('div', { class: 'small', id: 'run-metrics-fit', role: 'status' });
    var fitSeq = 0;
    var why = h('div', { class: 'small muted', id: 'run-why' });
    var size = h('div', { class: 'small', id: 'run-size', role: 'status' });
    go.setAttribute('aria-describedby', 'run-why');
    // Roughly what loading the chosen file whole needs, against the limit that applies (Settings first, else the policy's).
    function sizeNote() {
      clear(size);
      size.className = 'small';
      var item = o.files.filter(function (f) { return f.id === files.value; })[0];
      if (!item) return;
      var mb = item.bytes / 1048576, sizeText = unitText(item.size);
      var pol = o.policies.filter(function (p) { return p.name === policy.value; })[0] || {};
      var limitGb = st.max_memory_gb || pol.max_memory_gb;
      var table = /\.(csv|tsv)$/i.test(item.name);
      if (table && mb > o.stream_above_mb) {
        size.textContent = t('{0}: a file this big is read in pieces, so memory stays flat (it needs free disk space for the work files).', sizeText);
        return;
      }
      var needGb = item.bytes * 25 / 1073741824;
      var fmt = function (g) { return g >= 0.1 ? g.toFixed(1) : g.toFixed(2); };
      if (limitGb && needGb > limitGb) {
        size.className = 'small warn-note';
        size.textContent = t('⚠ {0}: loading it needs roughly {1} GB of memory, and the limit is {2} GB, so the run will probably be refused. Raise the memory limit in Settings (only if this computer has that much free RAM).', sizeText, fmt(needGb), limitGb);
      } else {
        size.className = 'small muted';
        size.textContent = limitGb ? t('{0}: needs roughly {1} GB of memory (limit {2} GB).', sizeText, fmt(needGb), limitGb) : t('{0}: needs roughly {1} GB of memory.', sizeText, fmt(needGb));
      }
    }
    function refresh() {
      remember();
      policyNote.textContent = (POLICY_TEXT[policy.value] || '') + (st.max_file_mb ? ' ' + t('Your Settings limit files to {0} MB.', st.max_file_mb) : '') + (st.max_memory_gb ? ' ' + t('Memory limit from Settings: {0} GB.', st.max_memory_gb) : '');
      var missing = [];
      if (!files.value) missing.push(t('a data file'));
      if (!schemas.value) missing.push(t('a schema'));
      if (!actor.value.trim()) missing.push(t('your name'));
      go.disabled = missing.length > 0;
      why.textContent = missing.length ? t('To run, choose {0}.', missing.join(', ').replace(/, ([^,]*)$/, function (m, last) { return t(' and ') + last; })) : '';
      draft.disabled = !files.value;
      sizeNote();
    }
    // A file from anywhere on this computer: the program opens the system file window itself (the browser cannot show real paths).
    function added(res) {
      if (!res.added) { msg.textContent = t('No file chosen.'); return; }
      var slot = { file: 'file', schema: 'schema', analysis: 'analysis' }[res.added.kind];
      runForm[slot] = res.added.id;
      if (slot === 'file') { runForm.schema = ''; runForm.analysis = ''; }
      showRun(runId);
    }
    var browse = h('button', { type: 'button', class: 'secondary', id: 'run-browse', text: t('Choose a file on this computer…') });
    var pathBox = h('input', { id: 'run-path', type: 'text', autocomplete: 'off', spellcheck: 'false', placeholder: 'C:\\Users\\you\\Documents\\data.csv' });
    var pathGo = h('button', { type: 'button', class: 'secondary small', id: 'run-path-go', text: t('Use this file') });
    function addFile(body, button) {
      button.disabled = true; msg.textContent = body.path === undefined ? t('The file window is open. Look for it on your desktop, it may be behind this page.') : t('Checking the file…');
      remember();
      api('/api/run/add-file', { method: 'POST', body: body }).then(added)
        .catch(function (e) { msg.textContent = e.message; button.disabled = false; });
    }
    browse.addEventListener('click', function () { addFile({}, browse); });
    pathGo.addEventListener('click', function () { addFile({ path: pathBox.value }, pathGo); });
    pathBox.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') { ev.preventDefault(); pathGo.click(); } });
    // schema_x.json goes with analysis_x.json: choose that metrics file if the list has it
    function pickTwin(schemaName) {
      var twin = schemaName.replace(/^schema_/, 'analysis_');
      var hit = Array.prototype.filter.call(analyses.options, function (op) { return op.text.indexOf(twin + ' ') === 0; })[0];
      if (hit) analyses.value = hit.value;
      return !!hit;
    }
    // Does the chosen metrics file fit the chosen schema? A mismatch used to show up only after the run had stopped, in the database's own words.
    function showMetrics(m) {
      clear(metricsFit);
      var ch = m && m.chosen;
      if (!analyses.value || !ch) return;
      if (ch.ok) { metricsFit.className = 'small ok-note'; metricsFit.textContent = t('✓ The metrics fit this schema.'); return; }
      metricsFit.className = 'small warn-note';
      var why = ch.column
        ? (ch.hidden ? t('⚠ This metrics file does not fit: the metric “{0}” uses “{1}”, which is personal data that this policy keeps out of the metrics, so the run would stop.', ch.metric, ch.column)
                     : t('⚠ This metrics file does not fit this schema: the metric “{0}” needs the column “{1}”, which the schema does not have, so the run would stop.', ch.metric, ch.column))
        : t('⚠ This metrics file does not fit this schema: the metric “{0}” cannot run, so the run would stop.', ch.metric);
      metricsFit.appendChild(h('span', { text: why + ' ' }));
      if (m.best && m.best.id !== ch.id) {
        metricsFit.appendChild(h('button', { type: 'button', class: 'secondary small', id: 'run-metrics-use', text: t('Use {0} instead (fits)', m.best.name), onclick: function () { analyses.value = m.best.id; remember(); refresh(); checkFit(); } }));
      }
      metricsFit.appendChild(h('button', { type: 'button', class: 'secondary small', id: 'run-metrics-none', text: t('Run without metrics'), onclick: function () { analyses.value = ''; remember(); refresh(); checkFit(); } }));
    }
    // Before anything runs: does the chosen schema describe this file? If not, say so and offer the schema that does.
    function checkFit() {
      var mine = ++fitSeq;
      clear(fit);
      clear(metricsFit);
      if (!files.value && !schemas.value) return;
      api('/api/run/check', { method: 'POST', body: { file: files.value, schema: schemas.value, analysis: analyses.value, policy: policy.value } }).then(function (r) {
        if (mine !== fitSeq) return;
        showMetrics(r.metrics);
        if (!r.known) return;
        var best = r.best, ch = r.chosen;
        if (!schemas.value && best && best.missing_required_count === 0) {
          schemas.value = best.id;                                                              // nothing chosen yet: pick the schema that fits...
          if (!analyses.value) pickTwin(best.name);                                             // ...and the metrics file that goes with it
          remember(); refresh(); checkFit(); return;
        }
        if (ch && ch.missing_required_count === 0) {
          fit.className = 'small ok-note';
          fit.textContent = '✓ ' + t('This schema fits the file: {0} of its {1} columns are in the file', ch.matched, ch.schema_columns) + (ch.extra_in_file ? ' ' + t('({0} file columns are not in the schema)', ch.extra_in_file) : '') + '.';
        } else if (ch) {
          fit.className = 'small warn-note';
          fit.appendChild(h('span', { text: t('⚠ This schema does not fit this file: {0} required columns are missing (for example {1}), and only {2} of {3} schema columns are in the file. The run would be stopped.', ch.missing_required_count, ch.missing_required.slice(0, 4).join(', '), ch.matched, ch.schema_columns) + ' ' }));
          if (best && best.id !== ch.id && best.missing_required_count === 0) {
            fit.appendChild(h('button', { type: 'button', class: 'secondary small', text: t('Use {0} instead (fits)', best.name), onclick: function () { schemas.value = best.id; pickTwin(best.name); remember(); refresh(); checkFit(); } }));      // a new schema brings its own metrics file
          } else if (!best || best.missing_required_count > 0) {
            fit.appendChild(h('span', { text: t('None of the listed schemas fits; use “Draft a schema from the chosen file”.') }));
          }
        }
      }).catch(function () {});
    }
    [files, schemas, analyses, policy].forEach(function (c) { c.addEventListener('change', checkFit); });
    [files, schemas, analyses, policy].forEach(function (c) { c.addEventListener('change', refresh); });
    actor.addEventListener('input', refresh);

    var dataField = field(t('Data file'), 'run-file', files, o.files.length ? t('Not in the list? Choose it from anywhere on this computer.') : t('Nothing in the list yet. Choose a file from anywhere on this computer.'));
    if (o.can_browse) dataField.appendChild(h('div', { class: 'btns' }, browse));
    app.appendChild(h('div', { class: 'card' },
      h('p', { class: 'small muted', text: t('Everything stays on this computer. Pick a data file and the schema that describes it; the cleaned data, the bad rows and the metrics are written to a new run folder.') }),
      h('div', { class: 'runfields' },
        dataField,
        field(t('Schema (what each column should look like)'), 'run-schema', schemas, t('No schema yet? Choose the file, then use the draft button below.')),
        field(t('Metrics (what the report should answer)'), 'run-analysis', analyses),
        field(t('Policy'), 'run-policy', policy), field(t('Your name (goes into the audit log)'), 'run-actor', actor)),
      h('details', { id: 'run-path-box', class: 'folds', open: o.can_browse ? null : '' }, h('summary', { text: o.can_browse ? t('Or paste the full path of a file') : t('Paste the full path of a file (no file window is available here)') }),
        h('label', { class: 'f', for: 'run-path', text: t('Full path of a data, schema or metrics file') }), pathBox, h('div', { class: 'btns' }, pathGo)),
      size, fit, metricsFit, policyNote, h('div', { class: 'btns' }, go, draft, sample), why, msg));
    app.appendChild(resultBox);

    var addBox = h('details', { id: 'add-files', class: 'folds' }, h('summary', { text: t('Where the lists come from') }),
      h('p', { class: 'small', text: t('The lists show the files in these folders, plus the files you chose yourself: the app remembers the last {0}, newest first, even after you close it. Choosing never copies a file; the run reads it where it is.', o.max_chosen) }),
      h('ul', { class: 'small' }, (o.folders || []).map(function (f) { return h('li', { class: 'mono', text: f }); })),
      h('p', { class: 'small', text: t('You can also drop a file into the inbox folder inside the work folder, or start the app with another folder: python -m datapipe app --data-dir <folder> (repeat the option for several folders). Schema and metrics files are found in the same folders and in examples/.') }),
      h('p', { class: 'small muted', text: t('The list of chosen files is kept as paths in the work folder (recent-files.json). “Forget the files I chose” empties it; the files themselves are not touched.') }),
      h('div', { class: 'btns' },
        h('button', { type: 'button', class: 'secondary small', id: 'run-refresh', text: t('Refresh the lists'), onclick: function () { remember(); showRun(runId); } }),
        h('button', { type: 'button', class: 'secondary small', id: 'run-forget', text: t('Forget the files I chose'), disabled: !o.chosen_count, onclick: function () {
          api('/api/run/forget-files', { method: 'POST', body: {} }).then(function (res) {
            runForm.file = ''; runForm.schema = ''; runForm.analysis = '';
            runForm.flash = t('Forgot {0} chosen file(s). The files themselves are untouched.', res.forgotten);      // shown once the page is redrawn
            showRun(runId);
          }).catch(function (e) { msg.textContent = e.message; });
        } })));
    app.appendChild(addBox);

    var recent = h('div', { class: 'card' }, h('h3', { text: t('Earlier runs') }));
    if (!o.recent.length) recent.appendChild(h('p', { class: 'small muted', text: t('None yet.') }));
    o.recent.forEach(function (r) {
      var c = r.counts || {};
      recent.appendChild(h('div', { class: 'row small' },
        h('button', { type: 'button', class: 'back', text: (r.file || t('(file)')) + ' · ' + r.run_id.slice(0, 15), onclick: function () { location.hash = '#/run/' + r.run_id; } }),
        badge(RUN_STATUS_BADGE, r.status), c.rows_total !== undefined ? h('span', { class: 'muted', text: t('{0} valid of {1} rows', c.valid, c.rows_total) }) : null));
    });
    app.appendChild(recent);
    refresh();
    checkFit();

    // After a run that stopped on the metrics file: the same file and schema, without metrics. Only offered while the form still shows that file.
    function rerunWithoutMetrics() {
      analyses.value = '';
      remember(); refresh();
      if (!go.disabled) go.click();
    }
    function formShowsFile(name) {
      var o = files.options[files.selectedIndex];
      return !!o && o.value !== '' && o.text.indexOf(name + ' ') === 0;
    }
    sample.addEventListener('click', function () {
      sample.disabled = true; msg.textContent = t('Creating a fake file of about 2 MB…');
      api('/api/run/sample', { method: 'POST', body: {} }).then(function (res) {
        runForm.file = res.id; runForm.schema = ''; runForm.analysis = '';
        showRun(runId);
      }).catch(function (e) { msg.textContent = e.message; sample.disabled = false; });
    });
    draft.addEventListener('click', function () {
      msg.textContent = t('Reading the file…'); draft.disabled = true;
      api('/api/run/draft-schema', { method: 'POST', body: { file: files.value } }).then(function (res) {
        msg.textContent = t('Draft saved ({0} columns): {1}.', res.columns, res.saved_as) + ' ' + tm(res.note) + ' ' + t('Reload this page to pick it from the list.');
        refresh();
      }).catch(function (e) { msg.textContent = e.message; refresh(); });
    });
    go.addEventListener('click', function () {
      go.disabled = true; msg.textContent = t('Starting…');
      clear(resultBox);
      api('/api/run/start', { method: 'POST', body: { file: files.value, schema: schemas.value, analysis: analyses.value, policy: policy.value, actor: actor.value.trim() } })
        .then(function () { watch(seq, msg, go, refresh); })
        .catch(function (e) { msg.textContent = e.message; refresh(); });
    });

    if (runId && RUN_ID_RE.test(runId)) {
      api('/api/run/result?run=' + runId).then(function (r) { if (seq === navSeq) renderResult(resultBox, r, { rerunWithoutMetrics: rerunWithoutMetrics, formShowsFile: formShowsFile }); })
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
        msg.textContent = t('Running {0}… {1} s. A 100 MB file takes about 1–2 minutes; keep this page open.', s.file || '', s.elapsed);
        setTimeout(function () { watch(seq, msg, go, refresh); }, 1500);
      } else if (s.state === 'done') {
        location.hash = '#/run/' + s.run_id;
      } else if (s.state === 'error') {
        msg.textContent = t('The run could not start: {0}', tm(s.error) || t('unknown error')); refresh();
      } else { refresh(); }
    }).catch(function (e) {
      if (seq !== navSeq) return;
      msg.textContent = e.message; refresh();
    });
  }

  // A look at the cleaned data itself: the first rows of clean.csv, exactly as the file has them (so a masked column is masked here too).
  function showPreview(box, r) {
    var body = h('div', { class: 'card tablecard', id: 'run-preview' }, h('h3', { text: t('Preview of the cleaned data') }), h('p', { class: 'small muted', text: t('Loading…') }));
    box.appendChild(body);
    api('/api/run/preview?run=' + r.run_id).then(function (p) {
      clear(body);
      body.appendChild(h('h3', { text: t('Preview of the cleaned data') }));
      var note = p.total_rows !== null && p.total_rows !== undefined ? t('The first {0} of {1} rows of clean.csv; the file has all of them.', p.rows.length, p.total_rows) : t('The first {0} rows of clean.csv; the file has all of them.', p.rows.length);
      if (p.policy && p.policy !== 'low') note += ' ' + t('Personal columns are masked here exactly as in the file.');
      if (p.all_columns > p.columns.length) note += ' ' + t('Only the first {0} of {1} columns are shown.', p.columns.length, p.all_columns);
      body.appendChild(h('p', { class: 'small muted', text: note }));
      var head = h('tr', null, p.columns.map(function (c) { return h('th', { scope: 'col', text: c }); }));
      body.appendChild(h('table', { class: 'metric', id: 'run-preview-table' }, h('caption', { class: 'sr-only', text: t('Preview of the cleaned data') }), h('thead', null, head),
        h('tbody', null, p.rows.map(function (row) { return h('tr', null, row.map(function (v) { return h('td', { text: v }); })); }))));
    }).catch(function (e) { clear(body); body.appendChild(h('h3', { text: t('Preview of the cleaned data') })); body.appendChild(h('p', { class: 'small muted', text: e.message })); });
  }
  var METRIC_PROBLEM = /^(metric query failed|metric SQL does not parse|a metric must be exactly one statement|only SELECT\/WITH queries are allowed in metrics)/;
  function renderResult(box, r, actions) {
    clear(box);
    var st = RUN_STATUS[r.status] || ['b-neutral', String(r.status), ''];
    var card = h('div', { class: 'card', id: 'run-summary' },
      h('div', { class: 'row spread' }, h('h3', { text: t('Result for {0}', r.source || t('file')) }), h('span', { class: 'badge ' + st[0], text: st[1] })),
      h('p', { class: 'small', text: st[2] }));
    var c = r.counts;
    if (c) {
      card.appendChild(h('div', { class: 'row small' },
        h('span', { class: 'chip', text: t('{0} rows read', c.rows_total) }), h('span', { class: 'chip', text: t('{0} valid', c.valid) }),
        h('span', { class: 'chip', text: t('{0} set aside', c.quarantined) }),
        r.reconciliation && r.reconciliation.checks ? h('span', { class: 'chip', text: t('{0} cross-checks, {1} mismatches', r.reconciliation.checks, r.reconciliation.mismatches) }) : null));
    }
    if (r.reasons.length) card.appendChild(h('ul', { class: 'reasons' + (r.status === 'FAILED' || r.status === 'BLOCKED' ? ' rej' : '') }, r.reasons.map(function (x) { return h('li', { text: tm(x) }); })));
    if (r.warnings.length) card.appendChild(h('ul', { class: 'reasons' }, r.warnings.map(function (x) { return h('li', { text: tm(x) }); })));
    var labels = { 'clean.csv': t('Cleaned data (clean.csv)'), 'quarantine.csv': t('Bad rows and why (quarantine.csv)'), 'report.md': t('Report (report.md)'), 'result.json': t('Result (result.json)'), 'issues.json': t('Issues (issues.json)') };
    if (r.files.length && RUN_ID_RE.test(r.run_id)) {
      var links = h('div', { class: 'btns' });
      r.files.forEach(function (n) {
        if (!labels[n]) return;
        links.appendChild(h('a', { class: n === 'clean.csv' ? 'dl main' : 'dl', href: '/api/run/download/' + r.run_id + '/' + n, text: labels[n] }));
      });
      card.appendChild(h('p', { class: 'small muted', text: t('Download (saved copies are also in the run folder):') }));
      card.appendChild(links);
    }
    var metricNames = Object.keys(r.metrics);
    if (metricNames.length && RUN_ID_RE.test(r.run_id)) {
      card.appendChild(h('p', { class: 'small muted', text: t('The metrics as CSV, to open in Excel or Numbers:') }));
      card.appendChild(h('div', { class: 'btns' }, h('a', { class: 'dl', id: 'dl-metrics-zip', href: '/api/run/metrics/' + r.run_id + '/all.zip', text: t('All metrics (CSV files in a .zip)') })));
    }
    card.appendChild(h('p', { class: 'small muted', text: t('Run folder: {0}', r.folder) }));
    if (RUN_ID_RE.test(r.run_id)) {
      var openMsg = h('span', { class: 'small muted', id: 'run-open-msg', role: 'status' });
      card.appendChild(h('div', { class: 'btns' }, h('button', { type: 'button', class: 'secondary small', id: 'run-open-folder', text: t('Open the run folder'), onclick: function () {
        api('/api/run/open-folder', { method: 'POST', body: { run: r.run_id } })
          .then(function () { openMsg.textContent = t('The folder is open. Look for it on your desktop, it may be behind this page.'); })
          .catch(function (e) { openMsg.textContent = e.message; });
      } }), openMsg));
    }
    // The cleaned data is written only when every check passed. After a metrics failure there is none; say so and offer the way forward.
    if (r.status === 'FAILED' && r.reasons.some(function (x) { return METRIC_PROBLEM.test(x); }) && actions && actions.rerunWithoutMetrics && r.source && actions.formShowsFile(r.source)) {
      card.appendChild(h('p', { class: 'small', id: 'run-metrics-failed', text: t('The cleaned data is only written when every check passes, so nothing was written for this run. Running again without the metrics file gives you the cleaned data.') }));
      card.appendChild(h('div', { class: 'btns' }, h('button', { type: 'button', class: 'primary', id: 'run-rerun-nometrics', text: t('Run again without metrics'), onclick: actions.rerunWithoutMetrics })));
    }
    if (r.outputs && r.outputs.clean_csv && r.policy !== 'low') card.appendChild(h('p', { class: 'small muted', text: t('Do not edit and re-save clean.csv: its checksum is recorded in the audit log.') }));
    box.appendChild(card);
    if (r.files.indexOf('clean.csv') >= 0 && RUN_ID_RE.test(r.run_id)) showPreview(box, r);
    Object.keys(r.metrics).forEach(function (name) {
      var m = r.metrics[name];
      var numeric = m.columns.map(function (cn, i) { return m.rows.length > 0 && m.rows.every(function (row) { return /^-?[0-9]+(\.[0-9]+)?$/.test(row[i]); }); });
      var head = h('tr', null, m.columns.map(function (cn, i) { return h('th', { scope: 'col', class: numeric[i] ? 'num' : null, text: cn }); }));
      var table = h('table', { class: 'metric' }, h('caption', { text: name.replace(/_/g, ' ') }), h('thead', null, head),
        h('tbody', null, m.rows.map(function (row) { return h('tr', null, row.map(function (v, i) { return h('td', { class: numeric[i] ? 'num' : null, text: v }); })); })));
      var csvLink = (/^[A-Za-z0-9_-]{1,80}$/.test(name) && RUN_ID_RE.test(r.run_id)) ? h('a', { class: 'dl small', href: '/api/run/metrics/' + r.run_id + '/' + name + '.csv', text: t('Download this table (CSV)') }) : null;
      box.appendChild(h('div', { class: 'card tablecard' }, table, csvLink,
        m.total_rows > m.rows.length ? h('p', { class: 'small muted', text: t('Showing the first {0} of {1} rows. The report file has all of them.', m.rows.length, m.total_rows) }) : null));
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
    app.appendChild(h('p', { class: 'muted', text: t('Loading…') }));
    api('/api/settings').then(function (d) { if (seq === navSeq) buildSettings(d.settings, d.info, d.llm_key, d.llm); })
      .catch(function (e) { if (seq === navSeq) showError(e); });
  }

  function buildSettings(s, info, llmKey, llm) {
    clear(app);
    var heading = h('h2', { text: t('Settings') });
    app.appendChild(heading);
    arrived(t('Settings'), heading, t('Settings'));
    // The page is four cards long. Buttons rather than # links (the address bar's # is the page's own route); each one scrolls to its card
    // and puts keyboard focus on the card's heading, so a screen reader starts reading there.
    var jumpTo = [['set-card-general', t('General')], ['set-llm-card', t('Language model')], ['set-card-key', t('Key')], ['set-card-about', t('About')]];
    app.appendChild(h('nav', { class: 'settings-jump', 'aria-label': t('On this page') }, jumpTo.map(function (j) {
      return h('button', { type: 'button', class: 'secondary small', 'data-jump': j[0], text: j[1], onclick: function () {
        var card = document.getElementById(j[0]);
        card.scrollIntoView({ block: 'start' });
        var title = card.querySelector('h3');
        title.setAttribute('tabindex', '-1');
        title.focus({ preventScroll: true });
      } });
    })));
    var name = h('input', { id: 'set-actor', type: 'text', maxlength: '80', autocomplete: 'off', value: s.actor || '', placeholder: t('Your name') });
    var policy = h('select', { id: 'set-policy' }, Object.keys(info.policies).map(function (p) { return h('option', { value: p, text: p }); }));
    policy.value = s.policy;
    var maxFile = h('input', { id: 'set-maxfile', type: 'number', min: '1', step: 'any', inputmode: 'decimal', value: s.max_file_mb === null ? '' : String(s.max_file_mb) });
    var maxMem = h('input', { id: 'set-maxmem', type: 'number', min: '0.5', step: 'any', inputmode: 'decimal', value: s.max_memory_gb === null ? '' : String(s.max_memory_gb) });
    var theme = h('select', { id: 'set-theme' }, [['system', t('Follow my computer')], ['light', t('Light')], ['dark', t('Dark')]].map(function (x) { return h('option', { value: x[0], text: x[1] }); }));
    theme.value = s.theme;
    // Language names are shown in their own language so they can always be found, and are never translated.
    var language = h('select', { id: 'set-language' }, [['system', t('Follow my computer')], ['en', 'English'], ['uk', 'Українська']].map(function (x) { return h('option', { value: x[0], text: x[1] }); }));
    language.value = s.language || 'system';
    var limitHint = h('div', { class: 'small muted', id: 'set-limit-hint' });
    var msg = h('div', { class: 'small', id: 'set-msg', role: 'status' });
    var save = h('button', { type: 'button', class: 'primary', id: 'set-save', text: t('Save settings') });
    function hint() {
      var p = info.policies[policy.value];
      limitHint.textContent = t('Left empty, the {0} policy loads a file whole up to {1} MB and an estimated {2} GB of memory.', policy.value, p.max_file_mb, p.max_memory_gb) + ' ' +
        t('Raise the memory limit only on a computer that really has that much free RAM (a file needs about 25 times its size).') + ' ' +
        t('A larger CSV or TSV file is read in chunks instead, so memory stays flat: up to {0} GB, using disk space for the work files.', p.max_stream_gb) + ' ' +
        t('A limit entered here applies to both ways of reading.');
    }
    policy.addEventListener('change', hint); hint();
    function num(input) { var v = input.value.trim(); return v === '' ? null : (isNaN(Number(v)) ? v : Number(v)); }
    save.addEventListener('click', function () {
      save.disabled = true; msg.textContent = t('Saving…');
      api('/api/settings', { method: 'POST', body: { actor: name.value, policy: policy.value, theme: theme.value, language: language.value, max_file_mb: num(maxFile), max_memory_gb: num(maxMem) } })
        .then(function (res) {
          if ((res.settings.language || 'system') !== (s.language || 'system')) { location.reload(); return; }      // every word on the page changes: start it afresh
          applyTheme(res.settings.theme);
          runForm.actor = ''; runForm.policy = '';                       // the Run page re-reads its defaults
          msg.textContent = t('Saved. The next run uses these settings.');
        }).catch(function (e) { msg.textContent = e.message; })
        .then(function () { save.disabled = false; });
    });
    app.appendChild(h('div', { class: 'card', id: 'set-card-general' }, h('h3', { text: t('General') }),
      h('p', { class: 'small muted', text: t('Saved in the work folder on this computer, so they are still here the next time you start the app.') }),
      h('div', { class: 'runfields' },
        field(t('Your name (default for new runs)'), 'set-actor', name),
        field(t('Default policy'), 'set-policy', policy),
        field(t('Largest file to accept, in MB (empty = policy limit)'), 'set-maxfile', maxFile),
        field(t('Memory limit in GB (empty = policy limit)'), 'set-maxmem', maxMem),
        field(t('Language'), 'set-language', language),
        field(t('Appearance'), 'set-theme', theme)),
      limitHint, h('div', { class: 'btns' }, save), msg));

    // ---- Language model: which server and model, whether it answers, and where the data would go.
    var conn = llm.connection, presets = llm.presets;
    var preset = h('select', { id: 'set-llm-preset' }, presets.map(function (p) { return h('option', { value: p.id, text: p.name + '  (' + p.base_url + ')' }); }),
      h('option', { value: 'other', text: t('Another server (type the address)') }));
    var llmUrl = h('input', { id: 'set-llm-url', type: 'text', maxlength: '300', autocomplete: 'off', spellcheck: 'false', placeholder: presets[0].base_url });
    var llmModel = h('input', { id: 'set-llm-model', type: 'text', maxlength: '200', autocomplete: 'off', spellcheck: 'false', list: 'set-llm-models', placeholder: t('For example llama3.2') });
    var modelList = h('datalist', { id: 'set-llm-models' });
    var llmWhere = h('div', { class: 'small', id: 'set-llm-where' });
    var llmState = h('div', { class: 'small', id: 'set-llm-state', role: 'status' });
    var llmSaved = h('div', { class: 'small muted', id: 'set-llm-saved' });
    var llmMsg = h('div', { class: 'small', id: 'set-llm-msg', role: 'status' });
    var llmConfirm = h('div', { class: 'small', id: 'set-llm-confirm', role: 'alert' });
    var llmCheck = h('button', { type: 'button', class: 'secondary', id: 'set-llm-check', text: t('Check connection and find models') });
    var llmSave = h('button', { type: 'button', class: 'primary', id: 'set-llm-save', text: t('Save connection') });
    var llmForget = h('button', { type: 'button', class: 'secondary', id: 'set-llm-clear', text: t('Forget connection') });
    function hostIsHere(u) {                                              // advisory only: the server decides when it saves
      try { var host = new URL(u).hostname.toLowerCase(); return host === 'localhost' || host === '127.0.0.1' || host === '[::1]' || host === '::1'; } catch (e) { return null; }
    }
    function showWhere(local) {                                            // never colour alone: a symbol and words as well
      clear(llmWhere);
      if (local === null || local === undefined) return;
      llmWhere.className = 'small ' + (local ? 'ok-note' : 'warn-note');
      llmWhere.textContent = local
        ? t('● Runs on this computer: nothing leaves it.')
        : t('⚠ Remote server or cloud model: the column names and a summary of the value patterns are sent to it. Run the map command with --dry-run to see exactly what.');
    }
    function liveWhere() {
      var here = hostIsHere(llmUrl.value.trim());
      showWhere(here === null ? null : (here && !/(^|[:\-_\/])cloud$/i.test(llmModel.value.trim())));
    }
    function showSaved(c) {
      llmSaved.textContent = c.base_url
        ? (c.model ? t('Saved: {0}, model {1}. The map command uses this when you give no address or model.', c.base_url, c.model)
                   : t('Saved: {0}, no model chosen yet. The map command uses this when you give no address or model.', c.base_url))
        : t('Nothing saved yet. The map command then uses the built-in offline matcher, or the address you give it.');
    }
    function matchPreset() {
      var hit = presets.filter(function (p) { return p.base_url === llmUrl.value.trim().replace(/\/+$/, ''); })[0];
      preset.value = hit ? hit.id : 'other';
    }
    llmUrl.value = conn.base_url || presets[0].base_url; llmModel.value = conn.model || '';
    matchPreset(); liveWhere(); showSaved(conn);
    preset.addEventListener('change', function () {
      var p = presets.filter(function (x) { return x.id === preset.value; })[0];
      if (p) llmUrl.value = p.base_url; else { llmUrl.value = ''; llmUrl.focus(); }
      clear(llmConfirm); liveWhere();
    });
    llmUrl.addEventListener('input', function () { matchPreset(); clear(llmConfirm); liveWhere(); });
    llmModel.addEventListener('input', function () { clear(llmConfirm); liveWhere(); });
    function llmBusy(on) { llmCheck.disabled = on; llmSave.disabled = on; llmForget.disabled = on; }
    llmCheck.addEventListener('click', function () {
      llmBusy(true); clear(llmConfirm); llmMsg.textContent = ''; llmState.className = 'small'; llmState.textContent = t('Checking…');
      api('/api/settings/llm/check', { method: 'POST', body: { base_url: llmUrl.value, model: llmModel.value } }).then(function (r) {
        var good = r.state === 'ok';
        llmState.className = 'small ' + (good ? 'ok-note' : 'warn-note');
        llmState.textContent = (good ? '✓ ' : '✗ ') + tm(r.message) + (good ? ' ' + t('(answered in {0} ms)', r.ms) : '');
        showWhere(r.locality === 'local');
        clear(modelList);
        r.models.forEach(function (m) { modelList.appendChild(h('option', { value: m })); });
        var wanted = llmModel.value.trim();
        if (good && !wanted && r.models.length === 1) llmModel.value = r.models[0];
        else if (good && wanted && r.models.indexOf(wanted) < 0) llmState.textContent += ' ' + t('The model “{0}” is not one of them.', wanted);
        else if (good && !wanted) llmState.textContent += ' ' + t('Click the Model box to pick one.');
      }).catch(function (e) { llmState.className = 'small warn-note'; llmState.textContent = '✗ ' + e.message; })
        .then(function () { llmBusy(false); });
    });
    function saveConnection(confirmed) {
      llmBusy(true); llmMsg.textContent = t('Saving…'); clear(llmConfirm);
      api('/api/settings/llm', { method: 'POST', body: { base_url: llmUrl.value, model: llmModel.value, confirm_remote: confirmed } }).then(function (res) {
        if (res.needs_confirmation) {
          llmMsg.textContent = '';
          showWhere(false);
          llmConfirm.appendChild(h('p', { text: t('This is not a model on your computer. Saving it means that mapping columns will send the column names and value patterns to that server. Do you want to save it anyway?') }));
          llmConfirm.appendChild(h('div', { class: 'btns' },
            h('button', { type: 'button', class: 'primary', id: 'set-llm-confirm-yes', text: t('Yes, save this remote server'), onclick: function () { saveConnection(true); } }),
            h('button', { type: 'button', class: 'secondary', id: 'set-llm-confirm-no', text: t('Cancel'), onclick: function () { clear(llmConfirm); } })));
          return;
        }
        llmMsg.textContent = t('Connection saved.');
        showSaved(res.connection); showWhere(res.connection.locality === 'local');
      }).catch(function (e) { llmMsg.textContent = e.message; })
        .then(function () { llmBusy(false); });
    }
    llmSave.addEventListener('click', function () { saveConnection(false); });
    llmForget.addEventListener('click', function () {
      llmBusy(true); clear(llmConfirm);
      api('/api/settings/llm/clear', { method: 'POST', body: {} }).then(function (res) { llmMsg.textContent = t('Saved connection removed.'); showSaved(res.connection); })
        .catch(function (e) { llmMsg.textContent = e.message; }).then(function () { llmBusy(false); });
    });
    app.appendChild(h('div', { class: 'card', id: 'set-llm-card' }, h('h3', { text: t('Language model (optional)') }),
      h('p', { class: 'small muted', text: t('Used when you map a new file’s columns with a model instead of the built-in offline matcher (the map command with --provider openai-compat). The Run tab does not use it. Pick the program that serves your model, check that it answers, and choose a model from its list.') }),
      h('div', { class: 'runfields' },
        field(t('Model server'), 'set-llm-preset', preset),
        field(t('Address'), 'set-llm-url', llmUrl, t('Usually ends with /v1. Plain http works only for this computer; anything else must use https.')),
        field(t('Model'), 'set-llm-model', llmModel, t('Check the connection to fill this list.')), modelList),
      llmWhere, llmState, llmConfirm,
      h('div', { class: 'btns' }, llmCheck, llmSave, llmForget), llmMsg, llmSaved));

    // The model key is write-only: the page can save or remove it but the server never sends it back.
    var keyInput = h('input', { id: 'set-llmkey', type: 'password', maxlength: '512', autocomplete: 'off', spellcheck: 'false' });
    var keyState = h('div', { class: 'small', id: 'set-llmkey-state', role: 'status' });
    var keyMsg = h('div', { class: 'small', id: 'set-llmkey-msg', role: 'status' });
    var keySave = h('button', { type: 'button', class: 'primary', id: 'set-llmkey-save', text: t('Save key') });
    var keyClear = h('button', { type: 'button', class: 'secondary', id: 'set-llmkey-clear', text: t('Remove saved key') });
    var keyWhere = h('p', { class: 'small muted', id: 'set-llmkey-where' });
    function showKey(k) {
      var cm = k.store === 'credential-manager';                         // where the server keeps it: Windows' own secret store, or a file
      keyInput.placeholder = k.saved ? t('Saved. Paste a new key to replace it') : t('Paste your key');
      keyClear.disabled = !k.saved;
      keyState.textContent = (k.saved ? t('A key is saved on this computer.') : t('No key is saved.')) +
        (k.environment ? ' ' + t('The environment variable DATAPIPE_LLM_API_KEY is set and takes priority over the saved key.') : '') +
        ' ' + (cm ? t('Stored in Windows Credential Manager.') : t('File: {0}', k.path));
      keyWhere.textContent = (cm
        ? t('Stored in Windows Credential Manager, encrypted for your Windows login: not in the work folder and not in a file. It is sent to the server address above (when you check the connection) and to the address you give the map command, so save one only for a server you trust.')
        : t('Stored as plain text in your own user folder, not in the work folder. It is sent to the server address above (when you check the connection) and to the address you give the map command, so save one only for a server you trust.')) +
        ' ' + t('It is never shown again; to change it, paste a new one.');
    }
    var keyNow = llmKey;                                                // what the server last said: saved or not
    showKey(keyNow);
    function keyCall(path, body, done, clearField) {
      keySave.disabled = true; keyClear.disabled = true; keyMsg.textContent = t('Working…');
      api(path, { method: 'POST', body: body })
        .then(function (res) { keyNow = res.llm_key; keyMsg.textContent = done; if (clearField) { keyInput.value = ''; } })
        .catch(function (e) { keyMsg.textContent = e.message; })           // on a failure the typed text stays, so it can be fixed
        .then(function () { showKey(keyNow); keySave.disabled = false; });
    }
    keySave.addEventListener('click', function () { keyCall('/api/settings/llm-key', { key: keyInput.value }, t('Key saved.'), true); });
    keyClear.addEventListener('click', function () { keyCall('/api/settings/llm-key/clear', {}, t('Saved key removed.'), false); });
    app.appendChild(h('div', { class: 'card', id: 'set-card-key' }, h('h3', { text: t('Key for the model server (optional)') }),
      h('p', { class: 'small muted', text: t('Only needed when the server above asks for a key, such as a hosted service or LM Studio with a key switched on.') + ' ' +
        t('Ollama on this computer needs none.') }),
      keyWhere,
      h('div', { class: 'runfields' }, field(t('API key'), 'set-llmkey', keyInput)),
      keyState, h('div', { class: 'btns' }, keySave, keyClear), keyMsg));

    var audit = h('button', { type: 'button', class: 'secondary small', id: 'set-audit', text: t('Check the audit log'), onclick: function () {
      auditMsg.textContent = t('Checking…');
      api('/api/settings/audit').then(function (r) { auditMsg.textContent = r.ok ? t('OK: {0} records. {1}', r.records, tm(r.message)) : t('PROBLEM: {0} records. {1}', r.records, tm(r.message)); })
        .catch(function (e) { auditMsg.textContent = e.message; });
    } });
    var auditMsg = h('div', { class: 'small', id: 'set-audit-msg', role: 'status' });
    function row(dt, dd) { return [h('dt', { text: dt }), h('dd', { text: dd })]; }
    app.appendChild(h('div', { class: 'card', id: 'set-card-about' }, h('h3', { text: t('About this installation') }), h('dl', { class: 'meta' },
      row(t('Version'), 'datapipe ' + info.version + (info.frozen ? ' ' + t('(standalone program)') : '')),
      row(t('Engine'), 'Python ' + info.python + ', DuckDB ' + info.duckdb),
      row(t('System'), info.system),
      row(t('Work folder (results, audit log, settings)'), info.workdir),
      row(t('Folders it reads data from'), info.folders.length ? info.folders.join('   ') : t('(none)'))),
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
    if (unsaved && unsaved() && !window.confirm(t('You have decisions that are not saved yet. Leave this proposal and discard them?'))) {
      location.hash = currentHash;
      return;
    }
    unsaved = null; currentHash = location.hash;
    route();
  });
  if (fixedReviewer) document.getElementById('whoami').textContent = t('reviewing as {0}', fixedReviewer);
  route();
})();
</script>
</body>
</html>
"""


def render_page(nonce: str, csrf: str, fixed_reviewer_escaped: str, theme: str = "system", language: str = "system") -> str:
    theme = theme if theme in ("light", "dark") else "system"          # only these three words ever reach the HTML
    language = language if language in LANGUAGES else "system"
    # the translation table goes in first, before any other value, so nothing substituted later can be mistaken for its marker
    return (_TEMPLATE.replace("{{I18N}}", embedded(language)).replace("{{LANGPREF}}", language)
            .replace("{{NONCE}}", nonce).replace("{{CSRF}}", csrf)
            .replace("{{FIXED}}", fixed_reviewer_escaped)
            .replace("{{THEME_ATTR}}", "" if theme == "system" else f' data-theme="{theme}"')
            .replace("{{SCHEME}}", "light dark" if theme == "system" else theme))
