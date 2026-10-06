"""Streaming CSV pipeline: files of any size in bounded memory.

The file is read in passes and never held in memory:
  1. scan       - checks the whole file (encoding, NUL bytes, quoting), counts rows, hashes the bytes and, when no schema was
                  given, infers one.
  2. duplicates - only when the schema has "unique" columns: types just those columns into side tables of an on-disk DuckDB
                  database, which finds the values shared by several rows.
  3. validate   - types and checks every row with the same code as the in-memory path (validate.validate_row), loads the valid
                  rows into the database in chunks, spools the rejected rows (with their problems) and writes the cleaned rows.
The in-memory pipeline stays the reference: tests run both on the same files and compare results_sha256, clean.csv and
quarantine.csv. A pass that sees a different file than the scan did (hash or row count) fails the run.
"""
import csv
import hashlib
import io
import json
import os
import secrets
import shutil
from collections import defaultdict
from contextlib import ExitStack
from dataclasses import replace
from decimal import Context, Decimal
from pathlib import Path

import duckdb

from .analyze import MEMORY_LIMIT, PyStats, _sql_literal, analyze_loaded, append_rows, create_table, engine_columns, lock_engine
from .errors import DataPipeError, IngestError
from .ingest import ENCODING_MESSAGE, NUL_MESSAGE, ScanInfo, check_header, sniff_delimiter, trimmed_warning
from .outputs import clean_cell, file_sha256, write_quarantine_files
from .schema import ColumnInferrer, schema_from_inferrers
from .validate import MASK, Issue, Quarantined, Validated, duplicate_issue, validate_row

CHUNK_ROWS = 20_000                      # rows held in memory between loads into the database
MAX_DUPLICATE_ROWS = 1_000_000           # rows sharing a value in a "unique" column that one run will track
FIRST_LINE_MAX = 16 * 1024 * 1024


class _HashingRaw(io.RawIOBase):
    """The file's bytes, hashed on the way through; refuses NUL bytes (binary or UTF-16/32 data)."""

    def __init__(self, path):
        super().__init__()
        self._fh = open(path, "rb", buffering=0)
        self.sha = hashlib.sha256()
        self.bytes = 0

    def readable(self):
        return True

    def readinto(self, buffer):
        n = self._fh.readinto(buffer) or 0
        if n:
            data = bytes(memoryview(buffer)[:n])
            if b"\x00" in data:
                raise IngestError(NUL_MESSAGE)
            self.sha.update(data)
            self.bytes += n
        return n

    def close(self):
        self._fh.close()
        super().close()


def _first_line(path, encoding):
    head = b""
    with open(path, "rb") as fh:
        while b"\n" not in head and len(head) < FIRST_LINE_MAX:
            block = fh.read(1024 * 1024)
            if not block:
                break
            head += block
    head = head.split(b"\n", 1)[0]
    try:
        return head.decode(encoding or "utf-8-sig", errors="replace")
    except LookupError:
        raise IngestError(ENCODING_MESSAGE)


class CsvReader:
    """One streaming read of a CSV file. Same parsing rules and error messages as ingest.parse_csv."""

    def __init__(self, path, encoding=None, delimiter=None):
        self.path, self.encoding, self._given = Path(path), encoding, delimiter
        self.warning = None
        self._text = self._raw = None

    def __enter__(self):
        try:
            self._open()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc):
        self.close()

    def _open(self):
        delimiter = self._given
        if delimiter is None:
            delimiter, self.warning = sniff_delimiter(_first_line(self.path, self.encoding))
        self.delimiter = delimiter
        self._raw = _HashingRaw(self.path)
        try:
            self._text = io.TextIOWrapper(io.BufferedReader(self._raw, 1 << 20), encoding=self.encoding or "utf-8-sig", newline="")
        except LookupError:
            raise IngestError(ENCODING_MESSAGE)
        self._reader = csv.reader(self._text, delimiter=delimiter, strict=True)
        try:
            header = next(self._reader)
        except StopIteration:
            raise IngestError("no header row found")
        except csv.Error as exc:
            raise IngestError(f"malformed CSV header: {exc}")
        except UnicodeDecodeError:
            raise IngestError(ENCODING_MESSAGE)
        self.header, self.trimmed = check_header(header)

    def records(self):
        """(n, record) for every non-blank record; n counts from 1 after the header."""
        n = 0
        try:
            for record in self._reader:
                if not record:
                    continue
                n += 1
                yield n, record
        except csv.Error as exc:
            raise IngestError(f"malformed CSV near record {n + 1}: {exc}")
        except UnicodeDecodeError:
            raise IngestError(ENCODING_MESSAGE)

    @property
    def sha256(self):
        return self._raw.sha.hexdigest()

    @property
    def bytes_read(self):
        return self._raw.bytes

    def close(self):
        if self._text is not None:
            self._text.close()
        elif self._raw is not None:
            self._raw.close()


def scan_csv(path, *, encoding=None, delimiter=None, infer=False) -> ScanInfo:
    """Pass 1. Reads the whole file once, keeping only counters (and, when asked, one inferrer per column)."""
    with CsvReader(path, encoding, delimiter) as rd:
        header = rd.header
        ncols = len(header)
        trimmed, rows, structural = rd.trimmed, 0, 0
        inferrers = [ColumnInferrer() for _ in header] if infer else None
        for _, record in rd.records():
            if len(record) != ncols:
                structural += 1
                continue
            stripped = [v.strip() for v in record]
            if stripped != record:
                trimmed += 1
            rows += 1
            if inferrers:
                for inferrer, value in zip(inferrers, stripped):
                    inferrer.add(value)
        warnings = [rd.warning] if rd.warning else []
        if trimmed:
            warnings.append(trimmed_warning(trimmed))
        if not rows and not structural:
            warnings.append("file has a header but no data rows")
        return ScanInfo("csv", header, rows, structural, warnings, rd.sha256, rd.bytes_read, rd.delimiter, inferrers)


_WIDE = Context(prec=100)                  # normalize() would round a 38-digit decimal at the default 28


def _canonical_key(value):
    """SHA-256 of a typed value's canonical text: equal values (1.5 and 1.50, 100 and 1E+2) give the same key."""
    text = format(value.normalize(_WIDE), "f") if isinstance(value, Decimal) else str(value)
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _file_changed():
    return DataPipeError("the file changed while it was being read; run it again once it is finished being written")


class StreamBackend:
    """The streaming implementation of the steps run_pipeline needs (see pipeline.MemoryBackend for the reference)."""
    streaming = True

    def __init__(self, path, run_dir, policy, *, encoding=None, delimiter=None, infer=False):
        self.path, self.run_dir, self.policy = Path(path), Path(run_dir), policy
        self.encoding, self.delimiter, self.infer = encoding, delimiter, infer
        self.scan = self.schema = self._stats = None
        self.n_valid = 0
        self._con = None
        token = secrets.token_hex(4)
        self._db = self.run_dir / f"engine-{token}.duckdb"
        self._db_tmp = self.run_dir / f"engine-{token}.tmp"
        self._spool = self.run_dir / "quarantine.spool"
        self._partial = self.run_dir / "clean.partial"

    # ------------------------------------------------------------ 1. scan
    def ingest(self):
        path, policy = self.path, self.policy
        if not path.is_file():
            raise IngestError(f"input is not a regular file: {path.name}")
        size = path.stat().st_size
        if size > policy.max_stream_bytes:
            raise IngestError(f"file is {size} bytes; policy limit is {policy.max_stream_bytes} (raise it with --max-file-mb)")
        if size == 0:
            raise IngestError("file is empty")
        delimiter = self.delimiter or ("\t" if path.suffix.lower() == ".tsv" else None)
        self.scan = scan_csv(path, encoding=self.encoding, delimiter=delimiter, infer=self.infer)
        return self.scan

    def infer_schema(self, name):
        return schema_from_inferrers(self.scan.columns, self.scan.inferrers, name)

    # ------------------------------------------------------------ 2. duplicates in "unique" columns
    def _find_duplicates(self, con, schema, uniq):
        """({row: [(column, rows sharing the value, first row with it)]}, {first row: [columns]}).
        Only the unique columns are typed, into side tables of the database, which finds the shared values. Every member of
        such a group is rejected (we cannot know which is right), exactly as in the in-memory run."""
        if not uniq:
            return {}, {}
        scan, policy = self.scan, self.policy
        header = scan.columns
        mini = replace(schema, columns=uniq)               # validate_row on just these columns
        present, null_tokens = set(header), set(schema.null_tokens)
        where = [(c.src, header.index(c.src)) for c in uniq if c.src in present]
        # a column that is masked in the output is never written to a work file in the clear: only the SHA-256 of its
        # canonical text is stored, which finds the same duplicates (a run that is killed leaves no personal data behind)
        hashed = [c.pii and policy.mask_pii for c in uniq]
        stored = [replace(c, type="string", scale=None, format=None) if h else c for c, h in zip(uniq, hashed)]
        for k, c in enumerate(stored):
            create_table(con, [c], f"_uq{k}")
        chunks, rows = [[] for _ in uniq], 0

        def flush():
            for k, c in enumerate(stored):
                append_rows(con, [c], chunks[k], self.run_dir, f"_uq{k}")
                chunks[k].clear()

        with CsvReader(self.path, self.encoding, scan.delimiter) as rd:
            for n, record in rd.records():
                if len(record) != len(header):
                    continue
                rows += 1
                typed, _ = validate_row(n, {src: record[i].strip() for src, i in where}, mini, present, null_tokens, policy)
                for k, c in enumerate(uniq):
                    if typed[c.name] is not None:              # rows with other problems still count towards duplicates
                        chunks[k].append({"_row": n, c.name: _canonical_key(typed[c.name]) if hashed[k] else typed[c.name]})
                if rows % CHUNK_ROWS == 0:
                    flush()
            flush()
            if rd.sha256 != scan.source_sha256 or rows != scan.n_rows:
                raise _file_changed()

        dups, firsts = {}, defaultdict(list)
        for k, c in enumerate(uniq):
            q = f'"{c.name}"'
            cur = con.execute(f'SELECT _row, n, first_row FROM (SELECT _row, count(*) OVER (PARTITION BY {q}) AS n, '
                              f'min(_row) OVER (PARTITION BY {q}) AS first_row FROM "_uq{k}") WHERE n > 1 ORDER BY _row')
            while True:
                batch = cur.fetchmany(100_000)
                if not batch:
                    break
                for row, members, first in batch:
                    dups.setdefault(row, []).append((c, members, first))
                    if c not in firsts[first]:
                        firsts[first].append(c)
                if len(dups) > MAX_DUPLICATE_ROWS:
                    raise DataPipeError(f"more than {MAX_DUPLICATE_ROWS:,} rows share a value in a column the schema marks as unique; "
                                        "check that the column really is unique")
            con.execute(f'DROP TABLE "_uq{k}"')
        return dups, firsts

    # ------------------------------------------------------------ 3. validate and load
    def validate(self, schema):
        scan, policy, run_dir = self.scan, self.policy, self.run_dir
        self.schema = schema
        header, ncols = scan.columns, len(scan.columns)
        present, null_tokens = set(header), set(schema.null_tokens)
        engine_cols = engine_columns(schema, policy)

        con = self._con = duckdb.connect(str(self._db))
        con.execute(f"SET memory_limit='{MEMORY_LIMIT}'")
        con.execute(f"SET temp_directory={_sql_literal(self._db_tmp.as_posix())}")
        create_table(con, engine_cols)
        dups, firsts = self._find_duplicates(con, schema, [c for c in schema.columns if c.unique])

        stats = self._stats = PyStats(schema, policy)
        pii_src = {c.src for c in schema.columns if c.pii}
        masked = [i for i, name in enumerate(header) if policy.mask_pii and name in pii_src]     # not kept in the clear in the spool
        by_rule = defaultdict(int)
        valid = quarantined = structural = rows = 0
        valid_chunk, first_values = [], {}

        with ExitStack() as stack:
            rd = stack.enter_context(CsvReader(self.path, self.encoding, scan.delimiter))
            spool = stack.enter_context(open(self._spool, "w", encoding="utf-8"))
            partial = stack.enter_context(open(self._partial, "w", newline="", encoding="utf-8"))
            writer = csv.writer(partial)
            writer.writerow([c.name for c in schema.columns])
            for n, record in rd.records():
                if len(record) != ncols:
                    structural += 1
                    quarantined += 1
                    by_rule["structure"] += 1
                    spool.write(json.dumps({"r": n, "raw": None, "i": [[None, "structure", f"expected {ncols} fields, found {len(record)}", None]]}) + "\n")
                    continue
                rows += 1
                cells = [v.strip() for v in record]
                typed, issues = validate_row(n, dict(zip(header, cells)), schema, present, null_tokens, policy)
                for col in firsts.get(n, ()):                       # a duplicate group is reported with its first row's value
                    first_values[(n, col.name)] = typed[col.name]
                if n in dups:
                    issues += [duplicate_issue(n, col, members, first_values[(first, col.name)], policy) for col, members, first in dups[n]]
                if issues:
                    quarantined += 1
                    for issue in issues:
                        by_rule[issue.rule] += 1
                    for i in masked:                           # quarantine.csv masks these anyway; do it before anything is written
                        if cells[i] != "":
                            cells[i] = MASK
                    spool.write(json.dumps({"r": n, "raw": cells, "i": [[i.column, i.rule, i.message, i.value] for i in issues]},
                                           ensure_ascii=False) + "\n")
                    continue
                valid += 1
                stats.add(typed)
                valid_chunk.append(typed)
                writer.writerow([clean_cell(c, typed[c.name], policy) for c in schema.columns])
                if len(valid_chunk) >= CHUNK_ROWS:
                    append_rows(con, engine_cols, valid_chunk, run_dir)
                    valid_chunk.clear()
            append_rows(con, engine_cols, valid_chunk, run_dir)
            if rd.sha256 != scan.source_sha256 or rows != scan.n_rows or structural != scan.n_structural:
                raise _file_changed()
        self.n_valid = valid
        lock_engine(con)
        return Validated(rows + structural, valid, quarantined, dict(by_rule))

    # ------------------------------------------------------------ 4. outputs
    def write_quarantine(self):
        header = self.scan.columns

        def rejected():
            with open(self._spool, encoding="utf-8") as fh:
                for line in fh:
                    e = json.loads(line)
                    yield Quarantined(e["r"], dict(zip(header, e["raw"])) if e["raw"] is not None else None,
                                      [Issue(e["r"], *i) for i in e["i"]])
        write_quarantine_files(self.run_dir, header, self.schema, self.policy, rejected())

    def analyze(self, schema, spec):
        return analyze_loaded(self._con, schema, spec, self.policy, self._stats.results)

    def write_clean(self, schema):
        path = self.run_dir / "clean.csv"
        os.replace(self._partial, path)
        return {"file": path.name, "rows": self.n_valid, "sha256": file_sha256(path)}

    def cleanup(self):
        if self._con is not None:
            try:
                self._con.close()
            except duckdb.Error:
                pass
        for p in (self._db, Path(str(self._db) + ".wal"), self._spool, self._partial):
            try:
                p.unlink()
            except OSError:
                pass
        shutil.rmtree(self._db_tmp, ignore_errors=True)
