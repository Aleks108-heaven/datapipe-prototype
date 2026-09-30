"""Format parsers. Every parser returns a RawTable and fails loudly on structural problems.

'row' numbers are 1-based data-record indexes (header excluded, blank lines skipped).
"""
import csv
import hashlib
import io
import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .errors import IngestError

SQL_TIMEOUT_SECONDS = 10


@dataclass
class RawTable:
    format: str
    columns: list
    rows: list                      # list of dict: source column -> raw value
    row_numbers: list               # parallel to rows
    structural_issues: list = field(default_factory=list)   # [(row_no, message)]
    warnings: list = field(default_factory=list)
    source_sha256: str = ""
    source_bytes: int = 0


# ---------------------------------------------------------------- entry point
def read_source(path, *, max_bytes, fmt=None, encoding=None, delimiter=None,
                table=None, records_path=None) -> RawTable:
    path = Path(path)
    if not path.is_file():
        raise IngestError(f"input is not a regular file: {path.name}")
    size = path.stat().st_size
    if size > max_bytes:
        raise IngestError(f"file is {size} bytes; policy limit is {max_bytes}")
    if size == 0:
        raise IngestError("file is empty")
    data = path.read_bytes()
    if b"\x00" in data:
        raise IngestError("file contains NUL bytes (binary or UTF-16/32 data); convert to UTF-8 first")
    text = _decode(data, encoding)
    fmt = fmt or detect_format(path, text)
    if fmt == "csv":
        tbl = parse_csv(text, delimiter=delimiter or ("\t" if path.suffix.lower() == ".tsv" else None))
    elif fmt == "json":
        tbl = parse_json(text, records_path=records_path)
    elif fmt == "jsonl":
        tbl = parse_jsonl(text)
    elif fmt == "sql":
        tbl = parse_sql_dump(text, table=table)
    else:
        raise IngestError(f"unsupported format {fmt!r}")
    tbl.source_sha256 = hashlib.sha256(data).hexdigest()
    tbl.source_bytes = size
    return tbl


def detect_format(path, text):
    ext = path.suffix.lower()
    by_ext = {".csv": "csv", ".tsv": "csv", ".json": "json", ".jsonl": "jsonl", ".ndjson": "jsonl", ".sql": "sql"}
    if ext in by_ext:
        return by_ext[ext]
    head = text.lstrip()[:1]
    if head in "[{":
        return "json"
    raise IngestError("cannot detect format from extension or content; pass --format")


def _decode(data, encoding):
    try:
        return data.decode(encoding) if encoding else data.decode("utf-8-sig")
    except (UnicodeDecodeError, LookupError):
        raise IngestError("file is not valid in the expected encoding (default UTF-8); pass --encoding explicitly")


# ---------------------------------------------------------------- CSV / TSV
def parse_csv(text, delimiter=None) -> RawTable:
    warnings = []
    if delimiter is None:
        first_line = text.split("\n", 1)[0]
        counts = {d: first_line.count(d) for d in ",;\t|"}
        delimiter = max(counts, key=counts.get) if max(counts.values()) > 0 else ","
        if sum(1 for c in counts.values() if c > 0) > 1:   # only flag genuinely ambiguous headers
            warnings.append(f"delimiter auto-detected as {delimiter!r} but the header contains other "
                            "candidate delimiters; pass --delimiter to make it explicit")
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    try:
        header = next(reader)
    except StopIteration:
        raise IngestError("no header row found")
    except csv.Error as exc:
        raise IngestError(f"malformed CSV header: {exc}")
    header = [h.strip() for h in header]
    if any(h == "" for h in header):
        raise IngestError("header contains an empty column name")
    if len(set(header)) != len(header):
        raise IngestError("header contains duplicate column names")
    rows, numbers, issues = [], [], []
    n = 0
    try:
        for record in reader:
            if not record:          # blank line
                continue
            n += 1
            if len(record) != len(header):
                issues.append((n, f"expected {len(header)} fields, found {len(record)}"))
                continue
            rows.append({h: v.strip() for h, v in zip(header, record)})
            numbers.append(n)
    except csv.Error as exc:
        raise IngestError(f"malformed CSV near record {n + 1}: {exc}")
    if not rows and not issues:
        warnings.append("file has a header but no data rows")
    return RawTable("csv", header, rows, numbers, issues, warnings)


# ---------------------------------------------------------------- JSON / JSONL
def _strict_loads(text):
    def pairs(items):
        keys = [k for k, _ in items]
        if len(keys) != len(set(keys)):
            raise IngestError("JSON object contains duplicate keys")
        return dict(items)

    def bad_constant(name):
        raise IngestError(f"non-standard JSON constant {name} is not allowed")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_float=Decimal, parse_constant=bad_constant)
    except json.JSONDecodeError as exc:
        raise IngestError(f"invalid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}")
    except RecursionError:
        raise IngestError("JSON nesting too deep")


def _flatten(obj, prefix=""):
    out = {}
    for key, value in obj.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, name + "."))
        elif isinstance(value, list):
            out[name] = json.dumps(value, default=str, sort_keys=True)
        else:
            out[name] = value
    return out


def _records_to_table(numbered_records, fmt, warnings, issues=None):
    columns, rows, numbers = [], [], []
    issues = list(issues or [])
    seen = set()
    for i, rec in numbered_records:
        if not isinstance(rec, dict):
            issues.append((i, "record is not a JSON object"))
            continue
        flat = _flatten(rec)
        for k in flat:
            if k not in seen:
                seen.add(k)
                columns.append(k)
        rows.append(flat)
        numbers.append(i)
    if not rows and not issues:
        warnings.append("no records found")
    return RawTable(fmt, columns, rows, numbers, sorted(issues), warnings)


def parse_json(text, records_path=None) -> RawTable:
    doc = _strict_loads(text)
    warnings = []
    if records_path:
        for key in records_path.split("."):
            if not isinstance(doc, dict) or key not in doc:
                raise IngestError("records path not found in document")
            doc = doc[key]
    elif isinstance(doc, dict):
        candidates = [k for k, v in doc.items() if isinstance(v, list) and v and all(isinstance(x, dict) for x in v)]
        if len(candidates) != 1:
            raise IngestError("top-level JSON object: pass --records-path to say where the record list is")
        warnings.append(f"records taken from key {candidates[0]!r} (auto-detected)")
        doc = doc[candidates[0]]
    if not isinstance(doc, list):
        raise IngestError("expected a JSON array of objects")
    return _records_to_table(list(enumerate(doc, start=1)), "json", warnings)


def parse_jsonl(text) -> RawTable:
    records, issues, n = [], [], 0
    for line in text.splitlines():
        if not line.strip():
            continue
        n += 1
        try:
            records.append((n, _strict_loads(line)))
        except IngestError as exc:
            issues.append((n, str(exc)))
    return _records_to_table(records, "jsonl", [], issues)


# ---------------------------------------------------------------- SQL dumps
_S = sqlite3
_ALLOWED_ACTIONS = {
    _S.SQLITE_CREATE_TABLE, _S.SQLITE_CREATE_INDEX, _S.SQLITE_DROP_TABLE, _S.SQLITE_DROP_INDEX,
    _S.SQLITE_INSERT, _S.SQLITE_UPDATE, _S.SQLITE_DELETE, _S.SQLITE_TRANSACTION, _S.SQLITE_SAVEPOINT,
    _S.SQLITE_SELECT, _S.SQLITE_READ, _S.SQLITE_RECURSIVE,
}
_DENIED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}


def _authorizer(action, arg1, arg2, dbname, source):
    if action == _S.SQLITE_FUNCTION:
        return _S.SQLITE_DENY if (arg2 or "").lower() in _DENIED_FUNCTIONS else _S.SQLITE_OK
    return _S.SQLITE_OK if action in _ALLOWED_ACTIONS else _S.SQLITE_DENY


def parse_sql_dump(text, table=None) -> RawTable:
    """Run a SQLite-compatible dump inside a locked-down in-memory database, then read a table.

    Denied: ATTACH/DETACH/PRAGMA/VIEW/TRIGGER/virtual tables/extension loading. A time limit stops runaway queries.
    """
    conn = sqlite3.connect(":memory:")
    deadline = time.monotonic() + SQL_TIMEOUT_SECONDS
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    conn.set_authorizer(_authorizer)
    try:
        conn.executescript(text)
    except sqlite3.Error as exc:
        conn.close()
        # SQLite messages can quote tokens from the dump (which may be personal data), so only the
        # error class name is reported.
        raise IngestError(
            "SQL dump could not be executed in the sandbox (SQLite-compatible dumps only; forbidden, "
            f"unsupported or malformed statement; sqlite error: {getattr(exc, 'sqlite_errorname', type(exc).__name__)})"
        )
    conn.set_authorizer(None)
    conn.set_progress_handler(None, 0)
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        if not names:
            raise IngestError("SQL dump defines no tables")
        if table is None:
            if len(names) > 1:
                raise IngestError(f"dump contains several tables {names}; pass --table")
            table = names[0]
        elif table not in names:
            raise IngestError(f"table not found in dump; available: {names}")
        quoted = '"' + table.replace('"', '""') + '"'
        cur = conn.execute(f"SELECT * FROM {quoted}")
        columns = [d[0] for d in cur.description]
        rows, numbers, warnings = [], [], []
        for i, values in enumerate(cur, start=1):
            converted = []
            for v in values:
                if isinstance(v, float):
                    v = Decimal(repr(v))      # shortest round-trip repr, avoids binary noise
                elif isinstance(v, bytes):
                    v = v.hex()
                converted.append(v)
            rows.append(dict(zip(columns, converted)))
            numbers.append(i)
        warnings.append("SQLite stores decimals as binary floats: values were converted via their shortest "
                        "decimal representation; verify money columns against the source system")
        return RawTable("sql", columns, rows, numbers, [], warnings)
    finally:
        conn.close()


_ = re  # re kept for future format sniffing
