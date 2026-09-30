import pytest

from datapipe import ingest
from datapipe.errors import IngestError
from datapipe.ingest import parse_csv, parse_json, parse_jsonl, parse_sql_dump, read_source

BIG = 10 ** 8


def test_csv_row_numbers_skip_blank_lines():
    t = parse_csv("id,name\n1,a\n\n2,b\n")
    assert t.columns == ["id", "name"]
    assert t.row_numbers == [1, 2]


def test_read_source_strips_utf8_bom(write):
    p = write("a.csv", b"\xef\xbb\xbfid,name\n1,a\n")
    assert read_source(p, max_bytes=BIG).columns == ["id", "name"]


def test_csv_ragged_rows_become_structural_issues_not_crashes():
    t = parse_csv("a,b\n1,2\n3\n4,5,6\n7,8\n")
    assert t.row_numbers == [1, 4]
    assert [n for n, _ in t.structural_issues] == [2, 3]


def test_csv_quoted_field_with_delimiter_and_newline():
    t = parse_csv('a,b\n"x,1","line1\nline2"\n')
    assert t.rows[0] == {"a": "x,1", "b": "line1\nline2"}


@pytest.mark.parametrize("text", ["a,a\n1,2\n", "a,\n1,2\n", ""])
def test_csv_bad_headers(text):
    with pytest.raises(IngestError):
        parse_csv(text)


def test_csv_unclosed_quote_fails_loudly():
    with pytest.raises(IngestError):
        parse_csv('a,b\n1,"oops\n')


def test_csv_header_only_warns():
    assert "no data rows" in parse_csv("a,b\n").warnings[0]


def test_csv_delimiter_autodetect_and_ambiguity_warning():
    assert parse_csv("a;b\n1;2\n").columns == ["a", "b"]
    assert not parse_csv("a;b\n1;2\n").warnings
    assert parse_csv("a;b,c\n1;2,3\n").warnings      # ambiguous header -> flagged


def test_tsv_by_extension(write):
    p = write("x.tsv", "a\tb\n1\t2\n")
    assert read_source(p, max_bytes=BIG).columns == ["a", "b"]


def test_binary_and_bad_encoding_rejected(write):
    with pytest.raises(IngestError):
        read_source(write("b.csv", b"a,b\n1,\x00\n"), max_bytes=BIG)
    with pytest.raises(IngestError):
        read_source(write("l.csv", "a\nzé\n".encode("latin-1")), max_bytes=BIG)
    assert read_source(write("l2.csv", "a\nzé\n".encode("latin-1")), max_bytes=BIG, encoding="latin-1").rows


def test_empty_missing_and_oversized_files(write, tmp_path):
    with pytest.raises(IngestError):
        read_source(write("e.csv", ""), max_bytes=BIG)
    with pytest.raises(IngestError):
        read_source(tmp_path / "nope.csv", max_bytes=BIG)
    with pytest.raises(IngestError):
        read_source(write("big.csv", "a\n" + "1\n" * 50), max_bytes=10)


def test_unknown_extension_needs_format(write):
    with pytest.raises(IngestError):
        read_source(write("x.dat", "hello"), max_bytes=BIG)


# ---------------------------------------------------------------- JSON
def test_json_keeps_exact_decimals_and_flattens_nested():
    t = parse_json('[{"a": 0.10, "b": {"c": 1, "d": [1, 2]}}]')
    from decimal import Decimal
    assert t.rows[0]["a"] == Decimal("0.10") and str(t.rows[0]["a"]) == "0.10"
    assert t.columns == ["a", "b.c", "b.d"]
    assert t.rows[0]["b.d"] == "[1, 2]"


def test_json_duplicate_keys_and_nan_rejected():
    with pytest.raises(IngestError):
        parse_json('[{"a": 1, "a": 2}]')
    with pytest.raises(IngestError):
        parse_json('[{"a": NaN}]')
    with pytest.raises(IngestError):
        parse_json("[{")


def test_json_records_path_and_autodetect():
    assert parse_json('{"data": {"rows": [{"a": 1}]}}', records_path="data.rows").rows == [{"a": 1}]
    t = parse_json('{"meta": 1, "items": [{"a": 1}]}')
    assert t.rows == [{"a": 1}] and "auto-detected" in t.warnings[0]
    with pytest.raises(IngestError):
        parse_json('{"a": [{"x": 1}], "b": [{"y": 2}]}')          # ambiguous
    with pytest.raises(IngestError):
        parse_json('{"a": 1}', records_path="zzz")


def test_json_non_object_records_are_structural_issues_and_missing_keys_are_null():
    t = parse_json('[{"a": 1}, 5, {"b": 2}]')
    assert t.structural_issues == [(2, "record is not a JSON object")]
    assert t.columns == ["a", "b"] and "b" not in t.rows[0]


def test_json_deep_nesting_does_not_crash():
    with pytest.raises(IngestError):
        parse_json("[" * 100000 + "]" * 100000)


def test_jsonl_bad_line_is_row_level_issue():
    t = parse_jsonl('{"a": 1}\n\nnot json\n{"a": 2}\n')
    assert t.row_numbers == [1, 3] and [n for n, _ in t.structural_issues] == [2]


# ---------------------------------------------------------------- SQL
DUMP = "BEGIN TRANSACTION;CREATE TABLE t(id INTEGER, price DECIMAL(10,2));INSERT INTO t VALUES(1,0.10);COMMIT;"


def test_sql_dump_loads_and_converts_floats_via_repr():
    from decimal import Decimal
    t = parse_sql_dump(DUMP)
    assert t.rows == [{"id": 1, "price": Decimal("0.1")}]


def test_sql_multi_table_requires_choice():
    two = DUMP + "CREATE TABLE u(x);INSERT INTO u VALUES(1);"
    with pytest.raises(IngestError):
        parse_sql_dump(two)
    assert parse_sql_dump(two, table="u").rows == [{"x": 1}]
    with pytest.raises(IngestError):
        parse_sql_dump(two, table="missing")


@pytest.mark.parametrize("evil", [
    "ATTACH DATABASE '/tmp/datapipe_evil.db' AS x;",
    "PRAGMA writable_schema=1;",
    "CREATE VIEW v AS SELECT 1;",
    "CREATE TRIGGER g AFTER INSERT ON t BEGIN SELECT 1; END;",
    "SELECT load_extension('x');",
    "CREATE VIRTUAL TABLE v USING fts5(a);",
])
def test_sql_dump_forbidden_statements_are_denied(evil):
    with pytest.raises(IngestError):
        parse_sql_dump("CREATE TABLE t(a);" + evil)
    import os
    assert not os.path.exists("/tmp/datapipe_evil.db")


def test_sql_dump_runaway_query_times_out(monkeypatch):
    monkeypatch.setattr(ingest, "SQL_TIMEOUT_SECONDS", 0.3)
    with pytest.raises(IngestError):
        parse_sql_dump("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) SELECT count(*) FROM c;")


def test_sql_dump_unsupported_dialect_fails_loudly():
    with pytest.raises(IngestError):
        parse_sql_dump("CREATE TABLE t (id int) ENGINE=InnoDB;")
    with pytest.raises(IngestError):
        parse_sql_dump("COPY t FROM stdin;")


def test_sql_dump_may_drop_its_own_tables_harmlessly():
    t = parse_sql_dump("DROP TABLE IF EXISTS t;" + DUMP)
    assert len(t.rows) == 1
