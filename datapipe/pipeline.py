"""Orchestration: ingest -> schema -> validate -> analyze -> report, with policy gates and audit at every step."""
import csv
import hashlib
import json
import os
import re
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

from . import __version__
from .analyze import AnalysisSpec, load_analysis, run_analysis
from .audit import AuditLog, default_actor
from .errors import DataPipeError
from .identity import clean_name, same_person
from .ingest import read_source
from .policy import get_policy
from .schema import compare_columns, infer_schema, load_schema
from .validate import MASK, validate

RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{6}$")
EXIT_CODES = {"COMPLETED": 0, "COMPLETED_WITH_WARNINGS": 0, "PENDING_SIGNOFF": 0,
              "FAILED": 1, "BLOCKED": 2, "NEEDS_SCHEMA_CONFIRMATION": 3}


@dataclass
class RunResult:
    status: str
    run_id: str
    run_dir: Path
    reasons: list = field(default_factory=list)
    document: dict = field(default_factory=dict)

    @property
    def exit_code(self):
        return EXIT_CODES[self.status]


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_of(obj):
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def _csv_safe(value):
    """Neutralise spreadsheet formula injection in exported cells."""
    text = "" if value is None else str(value)
    return "'" + text if _formula_risk(text) else text          # plain numbers such as -10.00 are shown exactly as they were in the file


def run_pipeline(input_path, *, workdir, policy_name, schema_path=None, analysis_path=None, fmt=None,
                 encoding=None, delimiter=None, table=None, records_path=None,
                 accept_inferred=False, actor=None, max_file_mb=None, max_memory_gb=None) -> RunResult:
    policy = get_policy(policy_name).with_limits(max_file_mb, max_memory_gb)
    actor = clean_name(actor) or default_actor()
    workdir = Path(workdir)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    run_dir = workdir / "runs" / run_id
    audit = AuditLog(workdir / "audit.jsonl")
    input_path = Path(input_path)

    doc = {"run_id": run_id, "status": None, "reasons": [], "actor": actor, "policy": policy.as_dict(),
           "engine": {"datapipe": __version__, "duckdb": duckdb.__version__},
           "source": {"name": input_path.name}, "schema": None, "counts": None, "warnings": [],
           "results": None, "results_sha256": None, "outputs": None}

    def finish(status, reasons=()):
        doc["status"] = status
        doc["reasons"] = list(reasons)
        audit_rec = audit.append("run_finished", run_id, {"status": status, "reasons": doc["reasons"],
                                                          "results_sha256": doc["results_sha256"]}, actor)
        doc["audit_head"] = audit_rec["hash"]
        (run_dir / "result.json").write_text(json.dumps(doc, indent=2, default=str) + "\n", encoding="utf-8")
        _write_report(run_dir, doc)
        return RunResult(status, run_id, run_dir, doc["reasons"], doc)

    audit.append("run_started", run_id, {"input": input_path.name, "policy": policy.name,
                                          "max_file_bytes": policy.max_file_bytes, "max_memory_bytes": policy.max_memory_bytes,
                                          "schema_file": Path(schema_path).name if schema_path else None,
                                          "analysis_file": Path(analysis_path).name if analysis_path else None}, actor)
    run_dir.mkdir(parents=True)                       # only after the audit log accepted the run: a refused start leaves no empty folder
    try:
        # ---- ingest
        tbl = read_source(input_path, max_bytes=policy.max_file_bytes, fmt=fmt, encoding=encoding,
                          delimiter=delimiter, table=table, records_path=records_path,
                          max_memory_bytes=policy.max_memory_bytes)
        doc["source"].update({"format": tbl.format, "bytes": tbl.source_bytes, "sha256": tbl.source_sha256})
        doc["warnings"].extend(tbl.warnings)
        audit.append("ingest_done", run_id, {"format": tbl.format, "bytes": tbl.source_bytes,
                                              "sha256": tbl.source_sha256, "rows": len(tbl.rows),
                                              "structural_issues": len(tbl.structural_issues)}, actor)

        # ---- schema
        if schema_path:
            schema = load_schema(schema_path)
        else:
            if policy.schema_mode == "registered":
                return finish("BLOCKED", ["policy requires a registered, versioned schema (--schema)"])
            schema = infer_schema(tbl, name=input_path.stem)
            (run_dir / "proposed_schema.json").write_text(
                json.dumps(schema.to_dict(), indent=2) + "\n", encoding="utf-8")
            if policy.schema_mode == "confirm" and not accept_inferred:
                doc["schema"] = {"name": schema.name, "inferred": True, "fingerprint": schema.fingerprint()}
                return finish("NEEDS_SCHEMA_CONFIRMATION",
                              ["schema was inferred; review proposed_schema.json, then re-run with --schema "
                               "(or --accept-inferred to accept it as is)"])
            doc["warnings"].append("schema was inferred automatically; PII flags and constraints are guesses")
        doc["schema"] = {"name": schema.name, "version": schema.version, "inferred": schema.inferred,
                         "fingerprint": schema.fingerprint(), "provenance": schema.provenance}

        missing, extra = compare_columns(schema, tbl.columns)
        audit.append("schema_resolved", run_id, {**doc["schema"], "missing": [c.name for c in missing],
                                                  "extra": extra}, actor)
        missing_required = [c.name for c in missing if c.required]
        if missing_required:
            return finish("BLOCKED", [f"required columns missing from file: {missing_required}"])
        for c in missing:
            doc["warnings"].append(f"optional column {c.name!r} not present in file (treated as empty)")
        if extra:
            if policy.extra_columns == "block":
                return finish("BLOCKED", [f"unexpected columns not in schema (schema drift): {extra}"])
            doc["warnings"].append(f"columns not in schema were ignored: {extra}")

        # ---- validate
        vr = validate(tbl, schema, policy)
        counts = {"rows_total": vr.rows_total, "valid": len(vr.valid_rows),
                  "quarantined": len(vr.quarantined), "issues_by_rule": vr.counts_by_rule()}
        doc["counts"] = counts
        audit.append("validation_done", run_id, counts, actor)
        if vr.quarantined:
            _write_quarantine(run_dir, tbl, schema, vr, policy)

        reasons = []
        if vr.rows_total == 0:
            reasons.append("file contains no data rows")
        elif not vr.valid_rows:
            reasons.append("no valid rows")
        elif vr.quarantined and policy.row_errors == "block":
            reasons.append(f"{len(vr.quarantined)} row(s) failed validation and this policy allows none")
        elif vr.rows_total and len(vr.quarantined) / vr.rows_total > policy.max_error_rate:
            reasons.append(f"error rate {len(vr.quarantined) / vr.rows_total:.1%} exceeds policy limit "
                           f"{policy.max_error_rate:.1%}")
        if reasons:
            return finish("BLOCKED", reasons)
        if vr.quarantined:
            doc["warnings"].append(f"{len(vr.quarantined)} row(s) quarantined and excluded from results")

        # ---- analyze
        spec = load_analysis(analysis_path) if analysis_path else AnalysisSpec(metrics=[])
        results, recon = run_analysis(schema, vr.valid_rows, spec, policy, tmp_dir=run_dir)
        results["reconciliation"] = recon
        doc["results"] = results
        doc["results_sha256"] = sha256_of(results)
        audit.append("analysis_done", run_id, {"metrics": [m.name for m in spec.metrics],
                                               "reconciliation_checks": recon["checks"],
                                               "reconciliation_mismatches": len(recon["mismatches"]),
                                               "results_sha256": doc["results_sha256"]}, actor)
        if recon["mismatches"]:
            return finish("BLOCKED", ["independent reconciliation of built-in statistics failed: "
                                      f"{recon['mismatches']}"])

        # ---- cleaned dataset: the valid rows, in file order, typed and normalised (only reached when every check passed)
        clean = _write_clean(run_dir, schema, vr, policy)
        doc["outputs"] = {"clean_csv": clean}
        audit.append("export_done", run_id, {"file": clean["file"], "rows": clean["rows"], "sha256": clean["sha256"]}, actor)

        if policy.require_signoff:
            return finish("PENDING_SIGNOFF", ["policy requires sign-off by a second person"])
        return finish("COMPLETED_WITH_WARNINGS" if doc["warnings"] else "COMPLETED")

    except DataPipeError as exc:
        return finish("FAILED", [str(exc)[:300]])
    except MemoryError:
        return finish("FAILED", ["ran out of memory; raise the RAM available, split the file, or lower --max-memory-gb "
                                 "so the run is refused earlier"])
    except Exception as exc:        # a bug or an input nobody predicted: still leave a result and a closed audit record
        if os.environ.get("DATAPIPE_DEBUG"):
            traceback.print_exc()   # off by default: a traceback can quote data values
        return finish("FAILED", [f"unexpected internal error ({type(exc).__name__}); set DATAPIPE_DEBUG=1 and run "
                                 "again to see the details on screen"])


# ------------------------------------------------------------------ outputs
def _write_quarantine(run_dir, tbl, schema, vr, policy):
    pii_src = {c.src for c in schema.columns if c.pii}
    with open(run_dir / "quarantine.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["row", "rules", "messages"] + list(tbl.columns))
        for q in vr.quarantined:
            raw = q.raw or {}
            cells = []
            for c in tbl.columns:
                v = raw.get(c)
                cells.append(MASK if (policy.mask_pii and c in pii_src and v not in (None, "")) else _csv_safe(v))
            w.writerow([q.row, ";".join(sorted({i.rule for i in q.issues})),
                        _csv_safe(" | ".join(f"{i.column or 'row'}: {i.message}" for i in q.issues))] + cells)
    issues = [i.as_dict() for i in vr.issues][:1000]
    (run_dir / "issues.json").write_text(json.dumps(issues, indent=2) + "\n", encoding="utf-8")


_PLAIN_SIGNED = re.compile(r"[+-][\d\s().\-]*")     # "+49 (0) 30-1234", "-12": a number or phone, not a formula


def _formula_risk(text):
    """True for text a spreadsheet would execute when the CSV is opened (= @ tab CR, or +/- followed by more than a number)."""
    if not text:
        return False
    if text[0] in "=@\t\r":
        return True
    return text[0] in "+-" and not _PLAIN_SIGNED.fullmatch(text)


def _clean_cell(col, value, policy):
    if value is None:
        return ""
    if col.pii and policy.mask_pii:
        return MASK
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    if col.type == "string":
        return "'" + value if _formula_risk(value) else value       # numbers are never altered, only risky text
    return str(value)


def _write_clean(run_dir, schema, vr, policy):
    """clean.csv: the rows that passed every check, in file order, one column per schema column (schema names,
    canonical values: ISO dates, plain decimals, true/false, empty = missing). PII follows the policy (masked in
    business/regulated). Its SHA-256 goes into result.json and the audit log, and sign-off re-checks it."""
    cols = schema.columns
    path = run_dir / "clean.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([c.name for c in cols])
        for row in vr.valid_rows:
            writer.writerow([_clean_cell(c, row[c.name], policy) for c in cols])
    return {"file": path.name, "rows": len(vr.valid_rows), "sha256": _file_sha256(path)}


def _file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _write_report(run_dir, doc):
    lines = [f"# Run {doc['run_id']}", "", f"**Status:** {doc['status']}", ""]
    for r in doc["reasons"]:
        lines.append(f"- {r}")
    src = doc["source"]
    lines += ["", "## Source", f"- file: {src.get('name')}  format: {src.get('format', '?')}  sha256: {src.get('sha256', '?')}"]
    if doc["schema"]:
        s = doc["schema"]
        lines.append(f"- schema: {s.get('name')} v{s.get('version', '?')} fingerprint {s.get('fingerprint', '')[:12]}"
                     f"{' (INFERRED)' if s.get('inferred') else ''}")
    if doc["counts"]:
        c = doc["counts"]
        lines += ["", "## Rows", f"- total {c['rows_total']}, valid {c['valid']}, quarantined {c['quarantined']}"]
        if c["issues_by_rule"]:
            lines.append("- problems found, by kind:")
            lines += [f"  - {rule}: {n}" for rule, n in sorted(c["issues_by_rule"].items())]
    if doc["warnings"]:
        lines += ["", "## Warnings"] + [f"- {w}" for w in doc["warnings"]]
    if doc["counts"] and doc["counts"]["quarantined"]:
        lines += ["", "## Bad rows", "- see quarantine.csv (the rows and why) and issues.json.",
                  "- \"row N\" is the N-th data record after the header (blank lines are skipped, and a value that spans several "
                  "lines counts once), so it is not a line number in a text editor. In a spreadsheet with a header row, row N is sheet row N+1."]
    if doc.get("outputs"):
        c = doc["outputs"]["clean_csv"]
        lines += ["", "## Cleaned data", f"- {c['file']}: {c['rows']} rows that passed every check, sha256 {c['sha256']}"]
    if doc["results"]:
        lines += ["", "## Metrics"]
        for name, m in doc["results"]["metrics"].items():
            lines += ["", f"### {name}", "| " + " | ".join(m["columns"]) + " |",
                      "|" + "---|" * len(m["columns"])]
            lines += ["| " + " | ".join(str(v) for v in row) + " |" for row in m["rows"]]
        lines += ["", f"results sha256: {doc['results_sha256']}"]
    (run_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ sign-off
def _refuse_signoff(workdir, run_id, reviewer, message):
    """Leave a trace of a refused sign-off (who tried, which run, why), then refuse. Never lets logging hide the refusal."""
    try:
        AuditLog(Path(workdir) / "audit.jsonl").append("signoff_refused", run_id, {"reason": message[:300]}, actor=reviewer or None)
    except Exception:
        pass
    raise DataPipeError(message)


def signoff(workdir, run_id, reviewer, note=""):
    if not RUN_ID_RE.match(run_id):
        raise DataPipeError("invalid run id")
    run_dir = Path(workdir) / "runs" / run_id
    result_file = run_dir / "result.json"
    if not result_file.is_file():
        raise DataPipeError("run not found")
    reviewer = clean_name(reviewer)
    doc = json.loads(result_file.read_text(encoding="utf-8"))
    if doc["status"] != "PENDING_SIGNOFF":
        _refuse_signoff(workdir, run_id, reviewer, f"run status is {doc['status']}; only PENDING_SIGNOFF runs can be signed off")
    if (run_dir / "signoff.json").exists():
        _refuse_signoff(workdir, run_id, reviewer, "run has already been signed off")
    if not reviewer or same_person(reviewer, doc["actor"]):
        _refuse_signoff(workdir, run_id, reviewer, "four-eyes rule: the reviewer must be a different person than the one who ran it")
    if sha256_of(doc["results"]) != doc["results_sha256"]:
        _refuse_signoff(workdir, run_id, reviewer, "results were modified after the run (hash mismatch); refusing to sign off")
    clean = (doc.get("outputs") or {}).get("clean_csv")
    if clean and (not (run_dir / clean["file"]).is_file() or _file_sha256(run_dir / clean["file"]) != clean["sha256"]):
        _refuse_signoff(workdir, run_id, reviewer, "the cleaned data file is missing or was modified after the run; refusing to sign off")
    rec = AuditLog(Path(workdir) / "audit.jsonl").append(
        "signoff", run_id, {"results_sha256": doc["results_sha256"], "note": note[:500]}, actor=reviewer)
    out = {"run_id": run_id, "reviewer": reviewer, "ts": rec["ts"], "results_sha256": doc["results_sha256"],
           "audit_hash": rec["hash"], "note": note[:500]}
    (run_dir / "signoff.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return out
