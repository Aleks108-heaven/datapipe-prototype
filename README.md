# datapipe (prototype)

Core pipeline for processing CSV / JSON / JSONL / SQL-dump files into verifiable analysis results,
with behaviour controlled by three policy tiers (`low`, `business`, `regulated`).

    ingest -> schema (declared, or inferred+confirmed) -> validate (quarantine) -> analyze (DuckDB)
           -> reconcile (independent Python check) -> report + hash-chained audit log -> sign-off

## Quick start

    pip install duckdb pytest
    python -m pytest                                    # 287 tests; the browser tests need Playwright + Chromium (pip install playwright; playwright install chromium)
    python -m datapipe run examples/sales.csv --policy business \
        --schema examples/schema_sales.json --analysis examples/analysis_sales.json --actor alice
    python -m datapipe run examples/sales_dirty.csv --policy regulated --schema examples/schema_sales.json
    python -m datapipe infer examples/sales.csv         # propose a schema
    python -m datapipe signoff <run_id> --reviewer bob  # regulated runs need a *different* person
    python -m datapipe verify-audit

A guided 5-minute walkthrough with a talk track: `python examples/demo.py --pause` (see [examples/DEMO.md](examples/DEMO.md)).

Exit codes: 0 ok / pending sign-off, 1 failed, 2 blocked by policy, 3 schema needs confirmation.
Each run writes `work/runs/<id>/` (`result.json`, `report.md`, `quarantine.csv`, `issues.json`) and appends to `work/audit.jsonl`.

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

   A loopback URL (`localhost`, `127.0.0.1`) is recorded as egress `local`: shapes only, payload still stored in the proposal, nothing leaves the machine, no key needed. Any other host counts as `cloud` and follows the tier rules above. `regulated` still refuses every LLM. Verification does not change, so a weak small model can only produce more `needs_review`/rejected items, not wrong accepted ones; expect lower hit rates than a frontier model. Tested against a fake local server only, not against Ollama itself.

Limits of this layer: it was tested against a fake local API server and scripted replies, **not against the live API**;
LLM confidence numbers are uncalibrated; two columns of the same type (e.g. net vs gross amount) cannot be told apart by value checks,
so such pairs are only as trustworthy as the human review; sample values in the `low` tier could still include personal data in a free-text column the detector does not recognise.

## Reviewing mapping proposals in the browser

    python -m datapipe review [--port 8765] [--reviewer NAME]     # then open the printed link

A local web page (no external assets, works offline) lists every proposal in `work/mappings/` and lets a reviewer inspect and decide:

- per mapping: source -> target, provider confidence, the deterministic evidence (fit %, distinctness, name similarity, other columns that would also fit), the verification reasons, and the provider's rationale (labelled as unverified text);
- *exactly what was sent* to the LLM, and whether anything left the machine;
- per-item **Include / Exclude** (verified items default to include, needs-review items default to exclude, rejected items are locked); overrides and rejections need a written note;
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
- Everything is processed in memory (file size limit 50-100 MB by tier); not built for big data.
- Identity is self-asserted (`--actor` / OS user). Four-eyes sign-off needs real authentication in a product.
- The audit log detects edits, deletions and reordering, but not removal of its *tail*; store the printed head hash elsewhere.
- SQL dumps: SQLite-compatible only (MySQL/Postgres dumps fail loudly). SQLite stores decimals as floats, so money read from SQL is converted via shortest repr and flagged with a warning.
- Auto-inference can only guess types; it cannot know meaning, uniqueness, or PII (it flags names that look like PII).
- Semantics (is `amount` gross or net?) must be encoded by a domain owner in the schema/metrics; the tool checks form, not meaning.
- One input per run; no cross-file joins. Dates only (no timestamps/time zones). LLM layer covers schema mapping only (not analysis).
- Schema regexes are trusted input (ReDoS possible from a hostile schema author). DuckDB memory is not capped.
- Platforms: CI (`.github/workflows/ci.yml`) runs the full suite, browser tests included, on Ubuntu, macOS and Windows with Python 3.10 and 3.13; all 281 tests passed on all six combinations in two consecutive runs. The audit-log lock uses `flock` on POSIX and a byte-range lock on a separate `.lock` file on Windows. On some locked-down Windows machines DuckDB's native library can be blocked by application-control policies (seen on the author's PC; CI is unaffected).

## Project status

### Done (latest work session)

- **Windows support for the audit log.** `audit.py` picks `flock` (POSIX) or an `msvcrt` byte-range lock (Windows); schema paths returned by the review service always use `/`.
- **Review UI accessibility** (`datapipe/webui/page.py`): real buttons for navigation (Enter/Space work), focus is kept across redraws, Include/Exclude is a proper radio group (one tab stop, arrow keys), route changes are announced through a small status region, the page title and focus follow the route, and timestamps are shown in UTC.
- **Review UI structure:** the duplicated "Mappings" list and "Map columns yourself" table were merged into one card per schema column (evidence, decision, "use a different file column"). Cards are ordered by risk from the server's verdicts and stay put while the reviewer works; a summary line counts each group; a proposed target the schema does not list is still shown. Reject now says what it is missing.
- **Bugs found and fixed by running the browser tests:** focus was pulled away from a field the reviewer was typing in; the action bar was rebuilt on every redraw and could swallow keystrokes; the HTTP server could reset connections on Windows when it refused a POST before reading its body (it now reads the bounded body first and has a 15 s socket timeout).
- **Design tokens:** radius, tap-target, spacing and type-size values are tokens in the page CSS.
- **Tests:** symlink cases skip their symlink part where the OS forbids symlinks (Windows without Developer Mode); browser tests updated for the merged layout. Last run on Windows: 236 passed, 45 failed - all 45 because the DuckDB stand-in cannot run queries, none for another reason.
- **README corrections:** removed the contradiction about manual remapping.
- **Continuous integration:** GitHub Actions runs the suite on Linux, macOS and Windows (Python 3.10 and 3.13). It found two real bugs that local runs had missed:
  - SQL-dump ingestion failed on Python 3.10, because `set_authorizer(None)` only clears the authorizer from 3.11 (on 3.10 it denies everything). It now installs an allow-all callback.
  - The first Windows audit lock blocked concurrent readers of the log (Windows byte-range locks are mandatory) and made a concurrency test fail intermittently. The lock now lives on a separate `.lock` file.

### Should do next

1. ~~Explain the evidence chips~~ (done: "How to read the evidence" legend, percent everywhere).
2. ~~Warn before losing decisions~~ (done: leave/reload prompt while decisions, manual mappings or a note are unsubmitted; the decisions themselves are still not persisted across a reload).
2b. ~~Control borders~~ (done: `--line-strong` token, >= 3:1, for inputs, selects, secondary buttons and Include/Exclude).
3. ~~Phone layout~~ (done: the to-do count is a sticky strip at the top of the proposal, with a "Go to approve / reject" jump on phones; provenance details are folded so the first decision card is on the first screen).
4. **Design-token follow-through:** spacing still has many literal values; button heights and focus styles could come from tokens too. Check dark mode visually on the merged cards.
5. **Accessibility verification with a real screen reader** (NVDA/VoiceOver); so far only automated browser tests and code review.
6. **Real authentication for reviewers** (today the name is self-asserted) and storing the audit head hash outside the log.
7. **CI follow-ups:** pin action versions to commit SHAs, add a dependency-audit step, and run the suite against the minimum supported DuckDB as well as the latest.
8. Items from "Known limitations" above (big files, SQL dump dialects, timestamps/time zones, analysis by LLM out of scope).
