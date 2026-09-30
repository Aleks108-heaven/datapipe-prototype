"""Deterministic analysis on DuckDB, with independent Python reconciliation of built-in statistics."""
import json
import re
import threading
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb

from .errors import AnalysisError

MAX_RESULT_ROWS = 10_000
QUERY_TIMEOUT_SECONDS = 30
_ORDER_BY = re.compile(r"\border\s+by\b", re.I)


@dataclass
class Metric:
    name: str
    sql: str
    description: str = ""


@dataclass
class AnalysisSpec:
    metrics: list
    profile: bool = True


def load_analysis(path) -> AnalysisSpec:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AnalysisError(f"cannot read analysis spec: {exc}")
    unknown = set(doc) - {"metrics", "profile"}
    if unknown:
        raise AnalysisError(f"unknown analysis keys: {sorted(unknown)}")
    metrics, seen = [], set()
    for m in doc.get("metrics", []):
        if set(m) - {"name", "sql", "description"} or "name" not in m or "sql" not in m:
            raise AnalysisError("each metric needs 'name' and 'sql' (and optionally 'description')")
        if m["name"] in seen:
            raise AnalysisError(f"duplicate metric name {m['name']!r}")
        seen.add(m["name"])
        metrics.append(Metric(**m))
    return AnalysisSpec(metrics, bool(doc.get("profile", True)))


def _duck_type(col):
    return {"string": "VARCHAR", "integer": "BIGINT", "boolean": "BOOLEAN", "date": "DATE",
            "decimal": f"DECIMAL(38,{col.effective_scale})"}[col.type]


def _jsonable(v):
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    return str(v)


def build_engine(schema, valid_rows, policy):
    """Load valid rows into an in-memory DuckDB with external access disabled.

    Data minimisation: when the policy masks PII, PII columns are NOT loaded, so no query can read them.
    """
    cols = [c for c in schema.columns if not (c.pii and policy.mask_pii)]
    con = duckdb.connect(":memory:")
    ddl = ", ".join(f'"{c.name}" {_duck_type(c)}' for c in cols)
    con.execute(f'CREATE TABLE data ("_row" BIGINT, {ddl})')
    if valid_rows:
        placeholders = ",".join("?" * (len(cols) + 1))
        con.executemany(f"INSERT INTO data VALUES ({placeholders})",
                        [[r["_row"]] + [r[c.name] for c in cols] for r in valid_rows])
    con.execute("SET enable_external_access=false")
    con.execute("SET lock_configuration=true")
    return con, cols


def _check_single_select(con, sql):
    try:
        stmts = con.extract_statements(sql)
    except duckdb.Error as exc:
        raise AnalysisError(f"metric SQL does not parse: {str(exc)[:160]}")
    if len(stmts) != 1:
        raise AnalysisError("a metric must be exactly one statement")
    # DuckDB classifies some non-query statements (e.g. PRAGMA database_list) as SELECT, so the type check
    # alone is not enough: also require the text to start with SELECT or WITH.
    stripped = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.S)
    if stmts[0].type != duckdb.StatementType.SELECT or not re.match(r"\s*(select|with)\b", stripped, re.I):
        raise AnalysisError("only SELECT/WITH queries are allowed in metrics")


def _run_query(con, sql):
    timer = threading.Timer(QUERY_TIMEOUT_SECONDS, con.interrupt)
    timer.start()
    try:
        cur = con.execute(sql)
        rows = cur.fetchmany(MAX_RESULT_ROWS + 1)
        names = [d[0] for d in cur.description]
    except duckdb.Error as exc:
        raise AnalysisError(f"metric query failed: {str(exc)[:160]}")
    finally:
        timer.cancel()
    if len(rows) > MAX_RESULT_ROWS:
        raise AnalysisError(f"metric returned more than {MAX_RESULT_ROWS} rows")
    return names, rows


def run_metrics(con, spec):
    out = {}
    for m in spec.metrics:
        _check_single_select(con, m.sql)
        names, rows = _run_query(con, m.sql)
        table = [[_jsonable(v) for v in r] for r in rows]
        if not _ORDER_BY.search(m.sql):           # make output order (and its hash) deterministic
            table.sort(key=lambda r: json.dumps(r, sort_keys=True, default=str))
        out[m.name] = {"description": m.description, "sql": m.sql, "columns": names, "rows": table}
    return out


def profile_and_reconcile(con, schema, valid_rows, policy):
    """Built-in statistics computed twice (DuckDB and plain Python); any disagreement is a hard failure."""
    profile, mismatches, checks = {}, [], 0
    for col in schema.columns:
        values = [r[col.name] for r in valid_rows]
        non_null = [v for v in values if v is not None]
        py = {"rows": len(values), "nulls": len(values) - len(non_null), "distinct": len(set(non_null))}
        hidden = col.pii and policy.mask_pii
        if not hidden and non_null:
            if col.type in ("integer", "decimal", "date"):
                py["min"], py["max"] = min(non_null), max(non_null)
            if col.type in ("integer", "decimal"):
                py["sum"] = sum(non_null)
        if hidden:                       # not in the engine at all: Python-only, counts only
            profile[col.name] = {k: _jsonable(v) for k, v in py.items()} | {"note": "PII column: statistics limited"}
            continue
        q = f'"{col.name}"'
        sel = [f"count(*) - count({q})", f"count(DISTINCT {q})"]
        keys = ["nulls", "distinct"]
        if col.type in ("integer", "decimal", "date"):
            sel += [f"min({q})", f"max({q})"]
            keys += ["min", "max"]
        if col.type in ("integer", "decimal"):
            sel.append(f"sum({q})")
            keys.append("sum")
        row = con.execute(f"SELECT {', '.join(sel)} FROM data").fetchone()
        eng = dict(zip(keys, row))
        eng["rows"] = len(values)
        for key in keys:
            checks += 1
            if key in py:
                same = py[key] == eng[key]
            else:                          # engine reports NULL for an all-null column
                same = eng[key] is None
            if not same:
                mismatches.append({"column": col.name, "statistic": key})
        profile[col.name] = {k: _jsonable(eng[k]) for k in ["rows"] + keys}
    return profile, {"checks": checks, "mismatches": mismatches}


def run_analysis(schema, valid_rows, spec, policy):
    con, _ = build_engine(schema, valid_rows, policy)
    try:
        result = {"profile": None, "metrics": run_metrics(con, spec)}
        recon = {"checks": 0, "mismatches": []}
        if spec.profile:
            result["profile"], recon = profile_and_reconcile(con, schema, valid_rows, policy)
        return result, recon
    finally:
        con.close()
