# datapipe (prototype)

Core pipeline for processing CSV / JSON / JSONL / SQL-dump files into verifiable analysis results,
with behaviour controlled by three policy tiers (`low`, `business`, `regulated`).

    ingest -> schema (declared, or inferred+confirmed) -> validate (quarantine) -> analyze (DuckDB)
           -> reconcile (independent Python check) -> report + hash-chained audit log -> sign-off

## Quick start

    pip install duckdb pytest
    python -m pytest                                    # 487 tests; the browser tests need Playwright + Chromium (pip install playwright; playwright install chromium)
    python -m datapipe run examples/sales.csv --policy business \
        --schema examples/schema_sales.json --analysis examples/analysis_sales.json --actor alice
    python -m datapipe run examples/sales_dirty.csv --policy regulated --schema examples/schema_sales.json
    python -m datapipe infer examples/sales.csv         # propose a schema
    python -m datapipe signoff <run_id> --reviewer bob  # regulated runs need a *different* person
    python -m datapipe verify-audit
    python -m datapipe app                              # the same thing in your browser: pick a file, run, read the results

**The app** (`datapipe app`) opens a local page (127.0.0.1 only) with two tabs: *Run a file* (choose a data file, a schema and a metrics file from the folders you started it in, press Run, then read the counts and metrics tables and download `clean.csv`, `quarantine.csv` and `report.md`; it can also draft a schema from a file) and *Review mappings*. It lists files from the current folder (or each `--data-dir`, repeatable), from `<work>/inbox/` (drop files there) and, for schemas and metrics, from `examples/`; *Add your own files* in the page shows exactly which folders, runs one file at a time, and never accepts a path typed into the page. A one-page guide for testers: [docs/TESTER_GUIDE.md](docs/TESTER_GUIDE.md).

**Install on a laptop:** `python -m pip install git+https://github.com/Aleks108-heaven/datapipe-prototype.git`, then double-click a launcher in [launchers/](launchers) or run `python -m datapipe app` - see [docs/INSTALL.md](docs/INSTALL.md). Tested in a clean virtual environment on Windows (install, run from an unrelated folder, serve the app). **No Python?** A standalone single-file program (Windows, Mac, Linux) is built by `.github/workflows/build.yml` (`packaging/build.py` builds it for your own system); the Windows one was tested with an empty PATH.

A guided 5-minute walkthrough with a talk track: `python examples/demo.py --pause` (see [examples/DEMO.md](examples/DEMO.md)).

Exit codes: 0 ok / pending sign-off, 1 failed, 2 blocked by policy, 3 schema needs confirmation, 64 wrong command line (usage error).
Each run writes `work/runs/<id>/` and appends to `work/audit.jsonl`:

| File | What it is |
| --- | --- |
| `result.json`, `report.md` | status, counts, metrics, the effective size limits, and the hash of every output |
| `clean.csv` | **the cleaned data**: the rows that passed every check, in file order, one column per schema column (ISO dates, plain decimals, `true`/`false`, empty = missing). Personal columns are masked in `business`/`regulated`. Text that a spreadsheet would run as a formula (`=...`, `@...`, `+cmd`) gets a leading `'`; numbers and phone numbers are never altered. Written only when every check passed; `signoff` re-checks its hash |
| `quarantine.csv`, `issues.json` | the rows that failed and why. "row" counts data records (the first line after the header is row 1) |

### Big files, and what computer you need

Defaults: 300 MB per file (150 MB in `regulated`) and an **estimated 6 GB of memory** for the parsed rows. The memory estimate (about 400 bytes per row plus 170 bytes per cell) is what really limits you, not the file size: a normal table needs roughly 25x its size, a file of many tiny rows up to 300x. The run is refused early, with a clear message, when the estimate is over the limit. Measured on a 12-column file of 120 MB (1.13 million rows, `examples/make_synthetic_buyers.py`): 88 s and 2.5 GB of RAM, so a laptop with 16 GB is fine. Override per run if you know your machine: `--max-file-mb 600 --max-memory-gb 10` (the limits used are recorded in `result.json`).

    python examples/make_synthetic_buyers.py buyers.csv --mb 120          # fake buyers/preferences data, any size
    python -m datapipe run buyers.csv --policy business --schema examples/schema_buyers.json --analysis examples/analysis_buyers.json --actor me

Platforms: pure Python + DuckDB, no OS-specific code in the load path (the temporary load file is created inside the run folder, never in `/tmp`, and deleted at once). CI runs the suite on Linux, macOS and Windows; the 120 MB measurement above was taken on Windows only.

## Policy tiers

| | low | business | regulated |
| --- | --- | --- | --- |
| Schema | inferred automatically (warning) | inferred -> must be confirmed | registered schema required |
| Extra columns (drift) | warn | warn | block |
| Bad rows | quarantine, continue | quarantine, block above 5% | block on any |
| PII | shown | masked in outputs, PII columns never loaded into the engine | same |
| Sign-off | no | no | required, reviewer != runner |
| LLM mapping | cloud: shapes + a few non-sensitive samples | cloud_masked: shapes only | none: offline heuristic only |

Missing required columns, zero data rows, zero valid rows, and reconciliation mismatches block in every tier.

## Accuracy mechanisms

- Strict typing: no silent rounding (decimal scale is enforced), `007` is not an integer, `03/04/2026` is rejected unless the column declares its date format, JSON numbers keep exact decimals, `NaN`/duplicate JSON keys/NUL bytes rejected.
- Duplicate keys quarantine *every* member of the group (the tool cannot know which one is right).
- Bad rows are quarantined with reasons, never repaired or dropped silently.
- Built-in statistics are computed twice (DuckDB and plain Python) and must agree.
- `results_sha256` is reproducible: the same data in CSV, JSON or SQL gives the same hash.

## Security mechanisms

- SQL dumps run in an in-memory SQLite with an authorizer (no ATTACH/PRAGMA/VIEW/TRIGGER/extensions) and a time limit.
- Metrics must be a single SELECT/WITH; DuckDB runs with external file access disabled and configuration locked.
- Error messages, audit records and reports never contain raw data values (tests enforce this, including SQL error text).
- Quarantine CSV neutralises spreadsheet formula injection.
- Audit log is hash-chained (tamper-evident) and written under a file lock.
- LLM calls never follow redirects (a redirect would re-send the API key to another host), require https except for localhost, and a cloud-proxied model name (`...:cloud`) is recorded as cloud egress even behind a localhost URL.
- A tiny SQL dump cannot expand into a huge database or value (database cap 512 MB; single value 32 MB on Python 3.11+).
- Hostile numbers (`1e999999999`, 5000-digit integers) are rejected as bad values, never crash the run.

## LLM-assisted schema mapping

For a file whose columns have different names than your registered schema ("Order No" vs `order_id`):

    python -m datapipe map new_file.csv --schema examples/schema_sales.json --policy business \
        [--provider anthropic --model <model-id>] [--dry-run]     # default provider: offline heuristic
    python -m datapipe approve-mapping <proposal.json> --reviewer bob [--accept-review] --out mapped_schema.json
    python -m datapipe run new_file.csv --schema mapped_schema.json ...

Design rules:

1. **The LLM only suggests.** It sees column names, value *shapes* (`2026-01-05` -> `9{4}-9{2}-9{2}`) and, in the `low` tier only, up to 3 samples from columns that do not look personal. It never sees full rows and never does analysis. `regulated` refuses any external provider; `--dry-run` prints exactly what would be sent.
2. **Every suggestion is verified against the real values**: names must exist, mappings are one-to-one, and the source values must actually parse as the target type (>= 98%). Contradicted mappings are rejected; low confidence, duplicates in a unique target, empty columns, or several equally plausible columns with uncorroborating names become `needs_review`.
3. **A different person approves** (`approve-mapping`, four-eyes). Rejected items can never be included; `needs_review` items only with `--accept-review`; required targets must end up mapped. The proposal is hash-sealed, so edits after the fact are refused.
4. **Traceable:** the approved schema carries provenance (proposal hash, proposer, approver, provider/model) that is copied into every run's `result.json`; audit events record hashes, never data.
5. The Anthropic provider uses explicit model selection (`--model` / `DATAPIPE_LLM_MODEL`) and `ANTHROPIC_API_KEY`; nothing is sent unless you pick it.
6. **Local or free models** use `--provider openai-compat`, which speaks the OpenAI-style `/chat/completions` API (Ollama, LM Studio, llama.cpp, vLLM, or hosted free tiers such as Groq / OpenRouter). The default URL is Ollama's:

        ollama pull llama3.1
        python -m datapipe map new_file.csv --schema examples/schema_sales.json --provider openai-compat --model llama3.1
        # hosted: --base-url https://api.groq.com/openai/v1 --model <id>   with DATAPIPE_LLM_API_KEY set

   A loopback URL (`localhost`, `127.0.0.1`) is recorded as egress `local`: shapes only, payload still stored in the proposal, nothing leaves the machine, no key needed. Any other host counts as `cloud` and follows the tier rules above. `regulated` still refuses every LLM. Verification does not change, so a weak small model can only produce more `needs_review`/rejected items, not wrong accepted ones; expect lower hit rates than a frontier model. Tested against a fake server and, once, against a real Ollama 0.35 on Windows (2026-10-02, `examples/sales_renamed.csv`, 6 target columns): `qwen2.5-coder:14b` mapped 6/6 correctly (one flagged needs_review at 0.75; ~90 s on CPU/GPU of the author's PC), `granite-4.2-3b` mapped 5/6 and left a required column unmapped (blocking approval until a person maps it), `llama3.2` (3B) mapped 1/6. The provider asks the server for schema-constrained JSON (`response_format`), because small models otherwise emit almost-valid JSON that the strict parser rightly refuses; servers that reject the parameter get one retry without it. LM Studio (`--base-url http://127.0.0.1:1234/v1`) is the same protocol but has not been run yet.

Limits of this layer: it was tested against a fake local API server and scripted replies, **not against the live API**;
LLM confidence numbers are uncalibrated; two columns of the same type (e.g. net vs gross amount) cannot be told apart by value checks,
so such pairs are only as trustworthy as the human review; sample values in the `low` tier could still include personal data in a free-text column the detector does not recognise.

## Reviewing mapping proposals in the browser

    python -m datapipe review [--port 8765] [--reviewer NAME]     # then open the printed link

A local web page (no external assets, works offline) lists every proposal in `work/mappings/` and lets a reviewer inspect and decide:

- per mapping: source -> target, provider confidence, the deterministic evidence (fit %, distinctness, name similarity, other columns that would also fit), the verification reasons, and the provider's rationale (labelled as unverified text);
- *exactly what was sent* to the LLM, and whether anything left the machine;
- per-item **Include / Exclude** (verified items start as include; items that need review start undecided - neither option is selected and the column is left out unless you include it; rejected items are locked). Schemas with more than 8 columns also get "Only columns that need me" and "Next to decide"; overrides and rejections need a written note;
- **Approve** writes `work/schemas/<name>-mapped-<id>.json` (with provenance) and records the decision in the audit log; **Reject** records the reason. Each proposal can be decided once.

All rules are enforced on the server (four-eyes, hash seal, required columns, one decision per proposal, atomically under the audit-log lock), not just in the page.

Security model: listens on 127.0.0.1 only; the printed link carries a random secret that is exchanged for an HttpOnly, SameSite=Strict cookie; every POST needs a per-run CSRF header; Host and Origin are checked (DNS rebinding / cross-site); strict CSP with per-response nonce (no inline handlers, no external hosts); all file- and LLM-derived text is inserted with `textContent` only (browser tests use hostile column names such as `<img onerror=...>`). To use it from another machine, tunnel the port with SSH; do not expose it.

Layout: one card per schema column (evidence, decision and "use a different file column" together). Cards that need you come first (needs review, or a required column with no mapping), then those refused by the checks, then optional unmapped columns, then verified ones; the order is fixed from the server's verdicts so cards do not move while you work. A one-line summary above the list counts each group. Keyboard and screen-reader use is supported (real buttons for navigation, arrow keys on Include/Exclude, focus kept across redraws, route changes announced); timestamps are shown in UTC.

Limits: the reviewer's name is self-asserted (or fixed at startup with `--reviewer`); the server is a prototype for one team on a trusted machine, not an internet-facing service.

### Manual remapping

When the provider missed a column or picked the wrong one, the reviewer can map it by hand in the
"Use a different file column" control on each column's card (`datapipe review --data-dir DIR`, default: current directory).

- The server finds the original file in a data dir by base name and **exact sha256**, parses it with the
  read options stored in the proposal, and recomputes the evidence (parse rate, uniqueness, name similarity).
  Anything the browser sends about evidence is ignored.
- The parse-rate gate (>= 98% by default) cannot be overridden; a refused pair shows the reason.
- A note is required for any manual mapping; it supersedes a proposed item for the same target; a source column
  can serve only one target. Manual mappings are recorded in the schema provenance and the audit log.
- If the original file is not available (or its hash differs), remapping is disabled and the UI says why.

## Known limitations (read before relying on it)

- **Not production-ready.** No independent security review. Treat as a design-proving prototype.
- Everything is processed in memory (size limit 150-300 MB and an estimated-RAM limit of 6 GB by default, both overridable per run, see "Big files"); a file of several GB needs the streaming redesign that is not built yet. Rows are loaded into the analysis engine through a temporary CSV file and `COPY` (a row-by-row load ran at ~100-1,000 rows/s and made the old 100 MB limit unreachable).
- Identity is self-asserted (`--actor` / OS user). Four-eyes sign-off needs real authentication in a product.
- The audit log detects edits, deletions and reordering, but not removal of its *tail*; store the printed head hash elsewhere.
- SQL dumps: SQLite-compatible only (MySQL/Postgres dumps fail loudly). SQLite stores decimals as floats, so money read from SQL is converted via shortest repr and flagged with a warning.
- Auto-inference can only guess types; it cannot know meaning, uniqueness, or PII (it flags names that look like PII).
- Semantics (is `amount` gross or net?) must be encoded by a domain owner in the schema/metrics; the tool checks form, not meaning.
- One input per run; no cross-file joins. Dates only (no timestamps/time zones). LLM layer covers schema mapping only (not analysis).
- Schema regexes are still treated as trusted input: nested unbounded repeats such as `(a+)+` are now refused, but overlapping alternations such as `(a|a)*` are not detected, so a hostile schema author can still cause slow matches. DuckDB memory is capped at 2 GB per run (`analyze.MEMORY_LIMIT`).
- Platforms: CI (`.github/workflows/ci.yml`) runs the full suite, browser tests included, on Ubuntu, macOS and Windows with Python 3.10 and 3.13; all tests passed on all six combinations in two consecutive runs at 281 tests, and again on every push since (288 tests at commit 4b09c8c, 2026-10-02). The audit-log lock uses `flock` on POSIX and a byte-range lock on a separate `.lock` file on Windows. On some locked-down Windows machines DuckDB's native library can be blocked by application-control policies (seen earlier on the author's PC; the 2026-10-02 local run was unaffected; CI is unaffected).

## Security audit (2026-10-02)

Scope: the whole code base, by reading the code and by running working attacks against it (not a substitute for an independent review). Dependencies (`duckdb`, `pytest`, `playwright`, latest versions) were checked with `pip-audit`: no known vulnerabilities. There is no `eval`/`exec`/`pickle`/`subprocess`/shell use anywhere in the package.

**Attacks that were tried and failed (the defences held):** DuckDB metric SQL reading files (`read_text`, `read_csv`, `glob`), environment variables (`getenv` does not exist), `COPY`, `INSTALL`, `SET enable_external_access`, `PRAGMA` via a comment prefix, stacked and `WITH ... DELETE` statements; SQL dumps using `ATTACH`, `PRAGMA`, `load_extension`, `VIEW`, `TRIGGER`, virtual tables and a CPU bomb (stopped at the 10 s limit); a 10,000-digit integer; 100,000-level JSON nesting; the review server's secret-link, cookie, CSRF, Host and Origin checks.

**Found, fixed and covered by `tests/test_security.py` (each was reproduced as a working attack first):**

| # | Finding | Impact | Fix |
| --- | --- | --- | --- |
| 1 | A 301/302/303 answer to an LLM request was followed and the `x-api-key` / `Authorization` header was re-sent to the new host | API key leak to anyone who controls or compromises the endpoint | Redirects are never followed |
| 2 | `http://` base URL accepted with an API key | Key sent in clear text | https required; plain http only for localhost / 127.0.0.1 / ::1 |
| 3 | An Ollama cloud model (`gpt-oss:120b-cloud`) behind `http://127.0.0.1:11434` was recorded as `local` ("nothing left this machine") | False privacy claim in the audit trail | Model names ending in `cloud` are recorded as `cloud` |
| 4 | JSON `1e999999999` in a decimal column raised `decimal.Overflow` and aborted the run with a raw traceback | One crafted file kills a run | Magnitude checked with `adjusted()` first; clean "decimal too large" |
| 5 | Schema pattern `(a+)+$` takes exponential time (0.2 s, 1.1 s, 4.2 s at n = 22, 24, 26) | CPU denial of service by a hostile schema | Nested unbounded repeats refused at schema load (partial: see limitations) |
| 6 | A 66-byte SQL dump built a 100 MB value (scales to GBs; the size limit only covered the file) | Memory exhaustion | Sandbox database capped at 512 MB, single value at 32 MB (3.11+) |
| 7 | DuckDB had no memory cap | A crafted metric exhausts memory | `memory_limit` 2 GB; the query fails cleanly |
| 8 | CI workflow token had default permissions | Wider blast radius if a step were compromised | `permissions: contents: read` |

**Still open (not fixed):**

- Identity is self-asserted (`--actor`, `--reviewer`): anyone who can run the tool or write the audit log can claim to be anyone, so four-eyes is a process control, not a security boundary. Real authentication is the biggest gap for the `regulated` tier.
- The audit log is tamper-evident, not tamper-proof: removing its tail is invisible unless the head hash is stored elsewhere.
- The review UI's secret link doubles as the session cookie value and lasts until the server stops; anyone who sees the link (terminal scrollback, shared screen) can act as the reviewer on that machine. Use `--reviewer` and keep the server short-lived.
- A proposal file placed in the mappings folder is trusted if its hash seal is internally consistent (the seal proves it was not edited, not who wrote it).
- Metric authors can read the DuckDB version and settings (`version()`, `duckdb_settings()`), which includes the working-folder path. Treat analysis files as trusted code.
- Report tables print data values verbatim; a hostile value cannot execute anything but can distort the Markdown layout.
- Dependencies are not pinned (`duckdb>=1.0`), there is no lock file, and `pip-audit` is not in CI; GitHub Actions are referenced by tag, not by commit SHA.
- No independent penetration test or code review has been done.
## Project status

**Stage:** working prototype, demo-ready. Core pipeline, three policy tiers, LLM-assisted mapping (offline heuristic, Anthropic, and any OpenAI-compatible local/hosted model), browser review UI and CI are built and tested (487 tests; CI runs Linux/macOS/Windows x Python 3.10/3.13; the new code was run locally on Windows with Python 3.14). Not production-ready: see "Known limitations".

**Roadmap**

| Goal | State |
| --- | --- |
| Demo prototype (5 min, `python examples/demo.py`) | Ready; talk track in `examples/DEMO.md` |
| Local / free model path | Done and run for real against Ollama; LM Studio not run yet |
| Anthropic API path | Implemented, tested against a fake server only; first live run waits for the author's API credit |
| Real team use | Needs reviewer authentication, off-log storage of the audit head hash, a live-API run, an independent security review |

### Done (2026-10-02 session)

- **Design review against a design-quality checklist** (hierarchy, function, experience, system, accessibility, feasibility; risk-ranked as impact x probability x detectability). Findings and fixes:
  - *Hierarchy:* the reviewer's task (decide on cards that need a decision) was below provenance details; on a phone the first decision was over two screens down. Now a sticky status strip ("N need your decision, ...") sits at the top, proposer / policy / what-left-the-machine stay visible, and the rest is folded under "More about this proposal".
  - *Phone:* a "Go to approve / reject" button (under 900 px) jumps to the action bar and focuses the reviewer field; scroll padding keeps focus out from under the sticky strip and the fixed bar.
  - *Accessibility:* control borders were 1.4:1 against the surface; a `--line-strong` token (about 4:1 or better, light and dark) is now used for inputs, selects, secondary buttons and Include/Exclude (WCAG 1.4.11). Text contrast already passed AA in both themes.
  - *Ambiguity:* a "How to read the evidence" legend; "Distinct values" and "Name similarity" use percent like the other chips.
  - *Error recovery:* leaving, reloading or closing the page with unsubmitted decisions, manual mappings or a note asks first.
  - Two stale screenshots in the repository root were replaced with current renders (light, phone).
- **Local / free LLM provider** (`--provider openai-compat`): any OpenAI-compatible `/chat/completions` server (Ollama, LM Studio, llama.cpp, vLLM, hosted free tiers). A loopback URL is egress `local` (shapes only, payload still stored and shown in the UI, no key needed); other hosts are `cloud` and need `DATAPIPE_LLM_API_KEY`. `regulated` still refuses every LLM.
- **First real-model run** (Ollama 0.35, Windows) found a real bug: a 3B model returned almost-valid JSON (a stray `]` per item) and the strict parser refused it. The parser stays strict; the provider now asks for schema-constrained output (`response_format`), with one retry without it for servers that reject the parameter. Results are in the LLM section above.
- **Demo:** `examples/demo.py` runs map -> four-eyes approve -> run -> regulated block on dirty data -> sign-off (self-sign refused) -> audit verify -> tamper detected, in a temporary folder, and exits 1 if any step differs from the script. `--pause` for presenting, `--review` to end in the browser UI. `examples/DEMO.md` has the talk track and the honest caveats.
- **Tests:** 281 -> 288 (provider cases, schema-constrained output and fallback, unsaved-work guard, status strip and jump, legend). The browser-test fixture now accepts `beforeunload` prompts and answers `confirm()` per test.
- **Environment note:** a broken pytest plugin in some global Python installs (`langsmith` with a pydantic version mismatch) crashes pytest at start-up; `pyproject.toml` now switches that plugin off (`-p no:langsmith_plugin`), so plain `python -m pytest` works; if another global plugin breaks pytest, run with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` or use a virtual environment. Unrelated to this project.

### How to try the model paths

    # Ollama (default URL http://127.0.0.1:11434/v1)
    python -m datapipe map examples/sales_renamed.csv --schema examples/schema_sales.json --provider openai-compat --model qwen2.5-coder:14b-instruct-q4_K_M
    # LM Studio: open the app, load a model, Developer tab -> start the local server (port 1234), then
    python -m datapipe map examples/sales_renamed.csv --schema examples/schema_sales.json --provider openai-compat --base-url http://127.0.0.1:1234/v1 --model <id shown in LM Studio>
    # Anthropic (when you have credit): set ANTHROPIC_API_KEY
    python -m datapipe map examples/sales_renamed.csv --schema examples/schema_sales.json --provider anthropic --model <model-id>
    # add --dry-run to any of them to see exactly what would be sent

### Done (earlier sessions)
- **Windows support for the audit log.** `audit.py` picks `flock` (POSIX) or an `msvcrt` byte-range lock (Windows); schema paths returned by the review service always use `/`.
- **Review UI fixes, 2026-10-05** (`datapipe/webui/page.py`, `server.py`, `service.py`): arrow keys now move focus together with the selection and the focus ring is visible on the filled option (inset, flips colour); undecided items show no selection; the folded sections share one row and the action bar is one row, so the first decision is on the first screen at 390x844 and 1366x650; each column card is a labelled section with a heading; errors say what to do next and keep the reviewer's decisions; the command shown after approval uses absolute paths and `--workdir`; focus moves to the outcome after Approve; long file names wrap (no horizontal scroll at 320 px); text sizes use rem; placeholder contrast >= 4.5:1; hidden/direction-changing characters in names are shown as `[U+XXXX]`; a tampered proposal explains why it cannot be approved or rejected; forced-colours mode keeps control edges; on Windows a second `datapipe review` on a busy port now fails with a clear message instead of starting silently. Also fixed: a slow proposal-list answer could paint over the proposal you had just opened (the cause of the occasional browser-test failure); answers for a page you have already left are now ignored.
- **Data-handling fixes, 2026-10-05:** (QA-023) JSON records in which two fields flatten to the same column name (`{"a":{"b":1},"a.b":2}`) are refused as bad rows instead of silently losing a value; (QA-024) only ASCII digits count as numbers, so `٠٧` can no longer pass as 7 and dodge the leading-zero rule; (QA-025) refused approvals, rejections and sign-offs are written to the audit log (`mapping_approval_refused`, `mapping_rejection_refused`, `signoff_refused`: who, which run or proposal, why); (QA-026) a damaged audit log (torn last line, non-record line, non-UTF-8) gives a clear message and is never appended to, `verify-audit` reports how many records were intact before the problem, and a run that cannot start no longer leaves an empty run folder; (QA-027) the report explains that "row N" is the N-th data record after the header (sheet row N+1), and lists problems one per line; (QA-028) a wrong command line exits with 64, so 2 always means "blocked by policy".
- **Worth knowing:** leading and trailing spaces are trimmed from CSV headers and values before validation (a value of `" 5 "` is read as `5`). The run now reports how many records were affected as a warning.
- **Small UI items:** spacing now uses a token scale (`--s1`..`--s6`, only hairlines and one bar clearance stay literal); the page has an inline icon (CSP `img-src data:`), which removes Firefox's favicon console message; forced-colours styling and the focus ring were checked in Chromium, Firefox and WebKit (not on real Windows High Contrast, Safari or a screen reader).
- **Products x buyers x preferences example (2026-10-05):** `examples/schema_products_buyers.json` (60 columns: order line, product, buyer, stated preferences) and `examples/analysis_products_buyers.json` (stated vs actual product group and channel, revenue per currency, plus built-in data checks for the line-total formula, orders before sign-up, cancelled orders with a review, return flag). Run on a 7,000-row and a 328,022-row (119 MB) file: all rows valid, 0 reconciliation mismatches, 126 s for the large one.
- **The app, 2026-10-05:** `python -m datapipe app` and the *Run a file* tab (`datapipe/webui/runner.py`, endpoints under `/api/run/`). Same protections as the review page (secret link, CSRF, Host/Origin checks, strict CSP). Picks are ids from fresh scans of the named folders, never paths; symlinks are not listed; downloads are limited to five named files inside run folders and are streamed (a 120 MB `clean.csv` is never held in memory); one run at a time; earlier runs can be reopened. Metrics numbers are right-aligned in tables. The products example now has 20 metrics (revenue by loyalty tier and month, churn risk by income band, lifetime value by buyer type, top products, delivery days, review scores, returns by category, organic stated vs bought, plus 4 data checks); revenue, churn and monthly figures were recomputed independently in plain Python and match exactly on the 7,000-row file (and on the 328,022-row file).
- **Level 2 + settings, 2026-10-05:** metrics download as CSV (UTF-8 with a BOM so Excel reads non-ASCII text; formula-like text is neutralised; one table or all as a zip); the ⚙ Settings page (`datapipe/webui/settings.py`, stored in `<work>/settings.json`, strictly validated, damaged files fall back to defaults): name, default policy, file-size and memory limits that really apply to runs, light/dark theme, audit-log check, version info; `datapipe sample` and a *Create a fake sample file* button (the generator moved into `datapipe/sample.py`, byte-identical output); a standalone program built with PyInstaller (23 MB on Windows) that opens the app when started with no arguments; the build runs for Windows/macOS/Linux in GitHub Actions.
- **Review UI accessibility** (`datapipe/webui/page.py`): real buttons for navigation (Enter/Space work), focus is kept across redraws, Include/Exclude is a proper radio group (one tab stop, arrow keys), route changes are announced through a small status region, the page title and focus follow the route, and timestamps are shown in UTC.
- **Review UI structure:** the duplicated "Mappings" list and "Map columns yourself" table were merged into one card per schema column (evidence, decision, "use a different file column"). Cards are ordered by risk from the server's verdicts and stay put while the reviewer works; a summary line counts each group; a proposed target the schema does not list is still shown. Reject now says what it is missing.
- **Bugs found and fixed by running the browser tests:** focus was pulled away from a field the reviewer was typing in; the action bar was rebuilt on every redraw and could swallow keystrokes; the HTTP server could reset connections on Windows when it refused a POST before reading its body (it now reads the bounded body first and has a 15 s socket timeout).
- **Design tokens:** radius, tap-target, spacing and type-size values are tokens in the page CSS.
- **Tests:** symlink cases skip their symlink part where the OS forbids symlinks (Windows without Developer Mode); browser tests updated for the merged layout. At that time a local Windows run had 236 passed / 45 failed, all because DuckDB could not run on that machine; on 2026-10-02 the same PC ran all tests green.
- **README corrections:** removed the contradiction about manual remapping.
- **Continuous integration:** GitHub Actions runs the suite on Linux, macOS and Windows (Python 3.10 and 3.13). It found two real bugs that local runs had missed:
  - SQL-dump ingestion failed on Python 3.10, because `set_authorizer(None)` only clears the authorizer from 3.11 (on 3.10 it denies everything). It now installs an allow-all callback.
  - The first Windows audit lock blocked concurrent readers of the log (Windows byte-range locks are mandatory) and made a concurrency test fail intermittently. The lock now lives on a separate `.lock` file.

### Should do next

1. ~~Explain the evidence chips~~ (done: "How to read the evidence" legend, percent everywhere).
2. ~~Warn before losing decisions~~ (done: leave/reload prompt while decisions, manual mappings or a note are unsubmitted; the decisions themselves are still not persisted across a reload).
2b. ~~Control borders~~ (done: `--line-strong` token, >= 3:1, for inputs, selects, secondary buttons and Include/Exclude).
3. ~~Phone layout~~ (done: the to-do count is a sticky strip at the top of the proposal, with a "Go to approve / reject" jump on phones; provenance details are folded so the first decision card is on the first screen).
4. **Design-token follow-through:** spacing still has many literal values; button heights and focus styles could come from tokens too. (Dark mode was checked visually on 2026-10-02 and holds together.) The explainer paragraph above the cards is still long; the proposals list and the approved-result screen got less design attention than the detail view.
5. **Accessibility verification with a real screen reader** (NVDA/VoiceOver); so far only automated browser tests and code review.
6. **Real authentication for reviewers** (today the name is self-asserted) and storing the audit head hash outside the log.
7. **CI follow-ups:** pin action versions to commit SHAs, add a dependency-audit step, and run the suite against the minimum supported DuckDB as well as the latest.
7b. **Run LM Studio and the live Anthropic API once** and record the results next to the Ollama numbers; persist reviewer decisions across a reload (today only a warning).
8. Items from "Known limitations" above (big files, SQL dump dialects, timestamps/time zones, analysis by LLM out of scope).
