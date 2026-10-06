"""Deterministic analysis on DuckDB, with independent Python reconciliation of built-in statistics."""
import csv
import decimal
import json
import os
import re
import secrets
import tempfile
import threading
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb

from .errors import AnalysisError

MAX_RESULT_ROWS = 10_000
QUERY_TIMEOUT_SECONDS = 30
MEMORY_LIMIT = "2GB"
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
    if not isinstance(doc, dict):
        raise AnalysisError("the analysis spec must be a JSON object with a 'metrics' list")
    unknown = set(doc) - {"metrics", "profile"}
    if unknown:
        raise AnalysisError(f"unknown analysis keys: {sorted(unknown)}")
    if not isinstance(doc.get("metrics", []), list):
        raise AnalysisError("'metrics' must be a list")
    if not isinstance(doc.get("profile", True), bool):
        raise AnalysisError("'profile' must be true or false")
    metrics, seen = [], set()
    for m in doc.get("metrics", []):
        if not isinstance(m, dict) or set(m) - {"name", "sql", "description"} or "name" not in m or "sql" not in m:
            raise AnalysisError("each metric needs 'name' and 'sql' (and optionally 'description')")
        if not isinstance(m["name"], str) or not m["name"].strip():
            raise AnalysisError("a metric 'name' must be non-empty text")
        if not isinstance(m["sql"], str) or not m["sql"].strip():
            raise AnalysisError(f"metric {m['name']!r}: 'sql' must be non-empty text")
        if not isinstance(m.get("description", ""), str):
            raise AnalysisError(f"metric {m['name']!r}: 'description' must be text")
        if m["name"] in seen:
            raise AnalysisError(f"duplicate metric name {m['name']!r}")
        seen.add(m["name"])
        metrics.append(Metric(**m))
    return AnalysisSpec(metrics, doc.get("profile", True))


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


def _sql_literal(text):
    return "'" + text.replace("'", "''") + "'"


def _converter(col):
    """Python value -> the text DuckDB's CSV reader turns back into exactly the same value."""
    if col.type == "boolean":
        return lambda v: "true" if v else "false"
    if col.type == "decimal":
        return lambda v: format(v, "f")          # never '1E+2': plain digits, no exponent
    if col.type == "date":
        return lambda v: v.isoformat()
    return str


def _write_load_file(path, cols, valid_rows, null_marker):
    """Write the rows as CSV. Returns "ok", "collision" (a real value equals the NULL marker: retry with another one)
    or "nul" (a text value contains NUL, which a CSV file cannot carry: use the slow path)."""
    convs = [(c.name, _converter(c), c.type == "string") for c in cols]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\r\n")      # the COPY below says so too: a bare \n or \r inside a value is data, not a line end
        for r in valid_rows:
            out = [r["_row"]]
            for name, conv, is_text in convs:
                v = r[name]
                if v is None:
                    out.append(null_marker)
                    continue
                if is_text:
                    if v == null_marker:
                        return "collision"
                    if "\x00" in v:
                        return "nul"
                    out.append(v)
                else:
                    out.append(conv(v))
            writer.writerow(out)
    return "ok"


def _bulk_load(con, cols, valid_rows, tmp_dir, table="data"):
    """Load through a temporary CSV file and DuckDB's COPY: ~380x faster than binding values one by one (measured), with
    identical stored values. The file lives next to the run's outputs (not in /tmp, which can be RAM-backed) and is
    deleted straight away. Returns False when the rows cannot go through a CSV file."""
    for _ in range(5):
        marker = "NULL_" + secrets.token_hex(12)          # random, so it cannot collide with a real value
        fd, path = tempfile.mkstemp(prefix="load-", suffix=".csv", dir=tmp_dir)
        os.close(fd)                                      # Windows cannot share an open handle with DuckDB
        try:
            state = _write_load_file(path, cols, valid_rows, marker)
            if state == "nul":
                return False
            if state == "ok":
                con.execute(f'COPY "{table}" FROM {_sql_literal(Path(path).as_posix())} '
                            f"(FORMAT csv, AUTO_DETECT false, NEW_LINE '\\r\\n', HEADER false, DELIMITER ',', QUOTE '\"', ESCAPE '\"', "
                            f"NULLSTR {_sql_literal(marker)})")
                return True
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    return False


def _slow_load(con, cols, valid_rows, table="data"):
    """Row-by-row binding. Only used when the bulk path cannot (NUL inside a text value) and by tests as a reference."""
    placeholders = ",".join("?" * (len(cols) + 1))
    con.executemany(f'INSERT INTO "{table}" VALUES ({placeholders})',
                    [[r["_row"]] + [_slow_value(c, r[c.name]) for c in cols] for r in valid_rows])


def create_table(con, cols, table="data"):
    ddl = ", ".join(f'"{c.name}" {_duck_type(c)}' for c in cols)
    con.execute(f'CREATE TABLE "{table}" ("_row" BIGINT, {ddl})')


def append_rows(con, cols, rows, tmp_dir, table="data"):
    """Add rows ({"_row": n, <column name>: typed value}) to a table made by create_table."""
    if rows and not _bulk_load(con, cols, rows, tmp_dir, table):
        _slow_load(con, cols, rows, table)


def engine_columns(schema, policy):
    """The columns a query can see. Data minimisation: when the policy masks PII, PII columns are not loaded at all."""
    return [c for c in schema.columns if not (c.pii and policy.mask_pii)]


def lock_engine(con):
    con.execute("SET enable_external_access=false")
    con.execute(f"SET memory_limit='{MEMORY_LIMIT}'")          # a crafted metric (cross joins, huge aggregates) fails instead of eating the machine
    con.execute("SET lock_configuration=true")


def _slow_value(col, v):
    # DuckDB's Python binding stores Decimal('1E+2') as 1.00 (wrong); hand it a plain-digit Decimal instead
    return Decimal(format(v, "f")) if col.type == "decimal" and v is not None else v


def build_engine(schema, valid_rows, policy, tmp_dir=None, bulk=True):
    """Load valid rows into an in-memory DuckDB with external access disabled.

    Data minimisation: when the policy masks PII, PII columns are NOT loaded, so no query can read them.
    """
    cols = engine_columns(schema, policy)
    con = duckdb.connect(":memory:")
    create_table(con, cols)
    if valid_rows:
        if not (bulk and _bulk_load(con, cols, valid_rows, tmp_dir)):
            con.execute("DELETE FROM data")
            _slow_load(con, cols, valid_rows)
    lock_engine(con)
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


DISTINCT_BUDGET = 4_000_000       # values the plain-Python distinct check may hold in memory, summed over all columns


class PyStats:
    """The plain-Python side of the built-in statistics, one row at a time, so it works on a file of any size.
    For the same rows it gives exactly what profile_and_reconcile used to compute from a list.

    The distinct count needs every distinct value in memory. Past DISTINCT_BUDGET values (summed over the columns) it stops
    for the remaining columns and says so in the result ("skipped"); every other statistic is still checked."""

    def __init__(self, schema, policy, distinct_budget=DISTINCT_BUDGET):
        self._ctx = decimal.Context(prec=120)      # the default context rounds at 28 digits; DuckDB's DECIMAL(38) sum is exact
        self._cols = []
        for col in schema.columns:
            hidden = col.pii and policy.mask_pii
            self._cols.append({"col": col, "rows": 0, "nulls": 0, "seen": set(), "skipped": False,
                               "min": None, "max": None, "sum": None,
                               "ranged": not hidden and col.type in ("integer", "decimal", "date"),
                               "summed": not hidden and col.type in ("integer", "decimal")})
        self._budget = distinct_budget

    def add(self, row):
        for s in self._cols:
            s["rows"] += 1
            v = row[s["col"].name]
            if v is None:
                s["nulls"] += 1
                continue
            if not s["skipped"]:
                seen = s["seen"]
                before = len(seen)
                seen.add(v)
                if len(seen) != before:
                    self._budget -= 1
                    if self._budget < 0:
                        s["skipped"], s["seen"] = True, set()
            if s["ranged"]:
                if s["min"] is None:
                    s["min"] = s["max"] = v
                elif v < s["min"]:
                    s["min"] = v
                elif v > s["max"]:
                    s["max"] = v
            if s["summed"]:
                s["sum"] = v if s["sum"] is None else (s["sum"] + v if s["col"].type == "integer" else self._ctx.add(s["sum"], v))

    def results(self):
        """({column name: {statistic: value}}, [(column name, statistic) that could not be checked])"""
        out, skipped = {}, []
        for s in self._cols:
            py = {"rows": s["rows"], "nulls": s["nulls"]}
            if s["skipped"]:
                skipped.append((s["col"].name, "distinct"))
            else:
                py["distinct"] = len(s["seen"])
            if s["min"] is not None:
                py["min"], py["max"] = s["min"], s["max"]
            if s["sum"] is not None:
                py["sum"] = s["sum"]
            out[s["col"].name] = py
        return out, skipped


def python_stats(schema, valid_rows, policy):
    stats = PyStats(schema, policy, distinct_budget=float("inf"))      # the rows are in memory already: no cap on the distinct check
    for r in valid_rows:
        stats.add(r)
    return stats.results()


def profile_and_reconcile(con, schema, valid_rows, policy):
    """Built-in statistics computed twice (DuckDB and plain Python); any disagreement is a hard failure."""
    return reconcile(con, schema, *python_stats(schema, valid_rows, policy), policy)


def reconcile(con, schema, py_stats, py_skipped, policy):
    profile, mismatches, checks = {}, [], 0
    for col in schema.columns:
        py = py_stats[col.name]
        hidden = col.pii and policy.mask_pii
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
        try:
            row = con.execute(f"SELECT {', '.join(sel)} FROM data").fetchone()
        except duckdb.Error as exc:
            raise AnalysisError(f"built-in statistics for column {col.name!r} failed in the engine ({type(exc).__name__}); "
                                "the values may be too large to add exactly")
        eng = dict(zip(keys, row))
        eng["rows"] = py["rows"]
        for key in keys:
            if (col.name, key) in py_skipped:
                continue
            checks += 1
            if key in py:
                same = py[key] == eng[key]
            else:                          # engine reports NULL for an all-null column
                same = eng[key] is None
            if not same:
                mismatches.append({"column": col.name, "statistic": key})
        profile[col.name] = {k: _jsonable(eng[k]) for k in ["rows"] + keys}
    recon = {"checks": checks, "mismatches": mismatches}
    if py_skipped:
        recon["not_checked"] = [{"column": c, "statistic": s} for c, s in py_skipped]
    return profile, recon


def analyze_loaded(con, schema, spec, policy, py_stats_fn):
    """Metrics and built-in statistics on an engine that already holds the valid rows. py_stats_fn() gives the plain-Python
    statistics (only called when the profile is wanted)."""
    result = {"profile": None, "metrics": run_metrics(con, spec)}
    recon = {"checks": 0, "mismatches": []}
    if spec.profile:
        result["profile"], recon = reconcile(con, schema, *py_stats_fn(), policy)
    return result, recon


def run_analysis(schema, valid_rows, spec, policy, tmp_dir=None):
    con, _ = build_engine(schema, valid_rows, policy, tmp_dir=tmp_dir)
    try:
        return analyze_loaded(con, schema, spec, policy, lambda: python_stats(schema, valid_rows, policy))
    finally:
        con.close()
