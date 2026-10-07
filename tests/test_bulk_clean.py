"""Bulk load, size limits, cleaned-data export, four-eyes name matching, strict schema/analysis files, error safety net."""
import csv
import json
import os
import time
from datetime import date
from decimal import Decimal

import pytest

import datapipe.analyze as analyze
import datapipe.pipeline as pipeline
from datapipe.analyze import build_engine, load_analysis
from datapipe.audit import AuditLog
from datapipe.cli import main
from datapipe.errors import AnalysisError, DataPipeError, SchemaError
from datapipe.identity import clean_name, same_person
from datapipe.ingest import parse_csv, read_source
from datapipe.mapping import build_approved_schema
from datapipe.pipeline import run_pipeline, signoff
from datapipe.policy import get_policy
from datapipe.schema import infer_schema, schema_from_dict
from conftest import ANALYSIS, EX, SCHEMA
from helpers import GOOD, make_proposal


# ------------------------------------------------------------------ bulk load == row-by-row load
def _all_types_schema():
    return schema_from_dict({"name": "t", "columns": [
        {"name": "i", "type": "integer"}, {"name": "s", "type": "string"}, {"name": "a", "type": "decimal", "scale": 6},
        {"name": "d", "type": "date"}, {"name": "b", "type": "boolean"}]})


AWKWARD = [
    (1, "plain", Decimal("1.50"), date(2026, 1, 5), True),
    (2, "", Decimal("0.00"), None, False),                       # empty text is not NULL
    (3, None, None, date(2026, 1, 6), None),                      # NULLs
    (4, 'quote " and , comma', Decimal("-7.25"), date(2026, 1, 7), True),
    (5, "line1\nline2\r\nline3", Decimal("1E+2"), date(2026, 1, 8), False),   # exponent form (JSON can produce it)
    (6, r"\N", Decimal("3"), date(2026, 1, 9), True),            # looks like a classic NULL marker
    (7, "NULL", Decimal("4"), date(2026, 1, 9), True),
    (8, "  kept spaces  ", Decimal("0.000001"), date(1, 1, 1), True),
    (9, "ünï©ode 金额 שלום 🙂", Decimal("99999999999999999999999999.999999"), date(9999, 12, 31), False),
    (10, "true", Decimal("-0.5"), date(2026, 2, 28), True),
    (11, "x" * 100_000, Decimal("12345.678901"), date(2026, 3, 1), True),
]


def _clean_rows(run_dir):
    with open(run_dir / "clean.csv", encoding="utf-8", newline="") as fh:          # closed again: Python 3.14 reports a file left to the garbage collector
        return list(csv.reader(fh))


def _rows(data):
    return [{"_row": n, "i": i, "s": s, "a": a, "d": d, "b": b} for n, (i, s, a, d, b) in enumerate(data, 1)]


def _dump(con):
    return con.execute('SELECT * FROM data ORDER BY "_row"').fetchall()


def test_bulk_load_stores_exactly_what_the_row_by_row_load_stores(tmp_path):
    schema, pol = _all_types_schema(), get_policy("low")
    bulk, _ = build_engine(schema, _rows(AWKWARD), pol, tmp_dir=tmp_path, bulk=True)
    slow, _ = build_engine(schema, _rows(AWKWARD), pol, tmp_dir=tmp_path, bulk=False)
    assert _dump(bulk) == _dump(slow)
    got = {r[0]: r for r in _dump(bulk)}
    assert got[2][2] == "" and got[3][2] is None                    # '' and NULL stay different
    assert got[5][3] == Decimal("100.000000")                        # the exponent form is stored as 100, not 1
    assert got[6][2] == r"\N" and got[7][2] == "NULL"


def test_a_real_value_equal_to_the_null_marker_is_handled(tmp_path, monkeypatch):
    schema = _all_types_schema()
    tokens = iter(["aa", "bb"])
    monkeypatch.setattr(analyze.secrets, "token_hex", lambda n: next(tokens))
    data = [(1, "NULL_aa", Decimal("1"), date(2026, 1, 1), True)]      # collides with the first marker
    con, _ = build_engine(schema, _rows(data), get_policy("low"), tmp_dir=tmp_path)
    assert _dump(con) == [(1, 1, "NULL_aa", Decimal("1.000000"), date(2026, 1, 1), True)]


def test_nul_inside_text_falls_back_to_the_slow_path(tmp_path):
    schema = _all_types_schema()
    data = [(1, "a\x00b", Decimal("1"), date(2026, 1, 1), True)]
    con, _ = build_engine(schema, _rows(data), get_policy("low"), tmp_dir=tmp_path)
    assert _dump(con)[0][2] == "a\x00b"


@pytest.mark.parametrize("text", ["a\nb", "a\rb", "a\n\nb", "x\n", "x\r", "\n", "\r", "a\nb\rc\r\nd"])
def test_a_lone_row_whose_text_has_a_bare_line_break_loads_exactly(tmp_path, text):
    """DuckDB's own newline detection used to misread a file of one row with a bare \\n or \\r inside a quoted value."""
    schema = _all_types_schema()
    con, _ = build_engine(schema, _rows([(1, text, Decimal("1"), date(2026, 1, 1), True)]), get_policy("low"), tmp_dir=tmp_path)
    assert _dump(con)[0][2] == text


def test_temp_folder_with_an_apostrophe_and_no_leftover_file(tmp_path):
    odd = tmp_path / "O'Brien's data"
    odd.mkdir()
    build_engine(_all_types_schema(), _rows(AWKWARD), get_policy("low"), tmp_dir=odd)
    assert list(odd.iterdir()) == []


def test_bulk_and_row_by_row_loads_give_the_same_results_hash(tmp_path, monkeypatch):
    rows = ["order_id,customer_email,region,amount,order_date,paid"]
    for i in range(1, 401):
        rows.append(f"{i},u{i}@example.com,{['EU', 'US', 'APAC'][i % 3]},{i}.{i % 100:02d},2026-01-{1 + i % 28:02d},{'true' if i % 2 else 'false'}")
    p = tmp_path / "s.csv"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    fast = run_pipeline(p, workdir=tmp_path / "w1", policy_name="low", schema_path=SCHEMA, analysis_path=ANALYSIS, actor="a")
    real = analyze.build_engine
    monkeypatch.setattr(analyze, "build_engine", lambda s, v, pol, tmp_dir=None: real(s, v, pol, tmp_dir, bulk=False))
    slow = run_pipeline(p, workdir=tmp_path / "w2", policy_name="low", schema_path=SCHEMA, analysis_path=ANALYSIS, actor="a")
    assert fast.status == slow.status == "COMPLETED"
    assert fast.document["results_sha256"] == slow.document["results_sha256"]


def test_fifty_thousand_rows_load_in_seconds_not_minutes(tmp_path):
    n = 50_000
    p = tmp_path / "big.csv"
    with open(p, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["order_id", "customer_email", "region", "amount", "order_date", "paid"])
        for i in range(1, n + 1):
            w.writerow([i, f"u{i}@example.com", "EU", "1.25", "2026-01-05", "true"])
    t0 = time.time()
    res = run_pipeline(p, workdir=tmp_path / "w", policy_name="low", schema_path=SCHEMA, analysis_path=ANALYSIS, actor="a")
    assert res.status == "COMPLETED" and res.document["counts"]["valid"] == n
    assert res.document["results"]["metrics"]["total_amount"]["rows"] == [[str(Decimal("1.25") * n)]]
    assert time.time() - t0 < 60          # the row-by-row load needed ~8 minutes for this on a clean install


def test_sum_is_exact_beyond_28_digits(tmp_path, write):
    sch = tmp_path / "s.json"
    sch.write_text(json.dumps({"name": "t", "columns": [{"name": "v", "type": "decimal", "scale": 6}]}))
    spec = tmp_path / "a.json"
    spec.write_text(json.dumps({"metrics": [{"name": "t", "sql": "SELECT SUM(v) FROM data"}]}))
    res = run_pipeline(write("d.csv", "v\n999999999999999999999999.999999\n1\n"), workdir=tmp_path / "w", policy_name="low",
                       schema_path=sch, analysis_path=spec, actor="a")
    assert res.status == "COMPLETED"
    assert res.document["results"]["metrics"]["t"]["rows"] == [["1000000000000000000000000.999999"]]


def test_json_exponent_decimal_is_no_longer_blocked(tmp_path, write):
    sch = tmp_path / "s.json"
    sch.write_text(json.dumps({"name": "t", "columns": [{"name": "v", "type": "decimal", "scale": 2}]}))
    spec = tmp_path / "a.json"
    spec.write_text(json.dumps({"metrics": [{"name": "t", "sql": "SELECT SUM(v) FROM data"}]}))
    res = run_pipeline(write("d.json", '[{"v": 1E+2}, {"v": 2.5}]'), workdir=tmp_path / "w", policy_name="low",
                       schema_path=sch, analysis_path=spec, actor="a")
    assert res.status == "COMPLETED" and res.document["results"]["metrics"]["t"]["rows"] == [["102.50"]]


# ------------------------------------------------------------------ size limits
def test_memory_estimate_stops_a_file_that_would_not_fit(tmp_path, write):
    p = write("m.csv", "n\n" + "1\n" * 50_000)
    res = run_pipeline(p, workdir=tmp_path / "w", policy_name="low", schema_path=None, accept_inferred=True,
                       max_memory_gb=0.01, actor="a")
    assert res.status == "FAILED" and "would need about" in res.reasons[0] and "--max-memory-gb" in res.reasons[0]
    assert "more than" in res.reasons[0]                       # stopped while reading, not after
    ok = run_pipeline(p, workdir=tmp_path / "w2", policy_name="low", schema_path=None, accept_inferred=True, actor="a")
    assert ok.status.startswith("COMPLETED")


def test_file_size_override_is_applied_and_recorded(tmp_path, write):
    p = write("f.csv", "n\n" + "1\n" * 1000)
    refused = run_pipeline(p, workdir=tmp_path / "w", policy_name="low", accept_inferred=True, max_file_mb=0.001, actor="a")
    assert refused.status == "FAILED" and "--max-file-mb" in refused.reasons[0]
    assert refused.document["policy"]["max_file_bytes"] == int(0.001 * 1024 * 1024)


def test_default_limits_accept_a_file_over_100_mb():
    assert get_policy("business").max_file_bytes > 112.6 * 1024 * 1024
    assert get_policy("regulated").max_file_bytes > 100 * 1024 * 1024


def test_cli_flags_reach_the_pipeline(tmp_path, write):
    p = write("c.csv", "n\n" + "1\n" * 1000)
    assert main(["--workdir", str(tmp_path / "w"), "run", str(p), "--policy", "low", "--accept-inferred", "--max-memory-gb", "0.0000001"]) == 1


# ------------------------------------------------------------------ cleaned data
def test_clean_csv_has_exactly_the_valid_rows_and_its_hash_is_recorded(tmp_path):
    res = run_pipeline(EX / "sales_dirty.csv", workdir=tmp_path / "w", policy_name="low", schema_path=SCHEMA,
                       analysis_path=ANALYSIS, actor="alice")
    assert res.status == "COMPLETED_WITH_WARNINGS"
    out = res.document["outputs"]["clean_csv"]
    rows = _clean_rows(res.run_dir)
    assert rows[0] == ["order_id", "customer_email", "region", "amount", "order_date", "paid"]
    assert [r[0] for r in rows[1:]] == ["1001", "1008"] and out["rows"] == 2
    assert rows[1] == ["1001", "anna@example.com", "EU", "120.50", "2026-01-05", "true"]
    assert out["sha256"] == pipeline._file_sha256(res.run_dir / "clean.csv")
    assert any(r["event"] == "export_done" for r in AuditLog(tmp_path / "w" / "audit.jsonl").records())
    assert "Cleaned data" in (res.run_dir / "report.md").read_text(encoding="utf-8")
    assert not list(res.run_dir.glob("load-*"))                # no temporary load file is left behind


def test_clean_csv_masks_personal_data_in_business_and_not_in_low(tmp_path):
    biz = run_pipeline(EX / "sales.csv", workdir=tmp_path / "b", policy_name="business", schema_path=SCHEMA, actor="a")
    low = run_pipeline(EX / "sales.csv", workdir=tmp_path / "l", policy_name="low", schema_path=SCHEMA, actor="a")
    assert "example.com" not in (biz.run_dir / "clean.csv").read_text(encoding="utf-8")
    assert "anna@example.com" in (low.run_dir / "clean.csv").read_text(encoding="utf-8")


def test_blocked_runs_write_no_cleaned_data(tmp_path):
    res = run_pipeline(EX / "sales_dirty.csv", workdir=tmp_path / "w", policy_name="regulated", schema_path=SCHEMA, actor="a")
    assert res.status == "BLOCKED" and not (res.run_dir / "clean.csv").exists() and not res.document["outputs"]


def test_spreadsheet_formulas_are_neutralised_but_numbers_and_phones_are_not(tmp_path, write):
    sch = tmp_path / "s.json"
    sch.write_text(json.dumps({"name": "t", "columns": [{"name": "t", "type": "string"}, {"name": "n", "type": "integer"}]}))
    values = ["=1+1", "@SUM(A1)", "+cmd|x", "-1+2", "+49 (0) 30-1234", "-12", "plain"]
    body = "t,n\n" + "\n".join(f'"{v}",-5' for v in values) + "\n"
    res = run_pipeline(write("f.csv", body), workdir=tmp_path / "w", policy_name="low", schema_path=sch, actor="a")
    got = [r[0] for r in _clean_rows(res.run_dir)[1:]]
    assert got == ["'=1+1", "'@SUM(A1)", "'+cmd|x", "'-1+2", "+49 (0) 30-1234", "-12", "plain"]
    assert all(r[1] == "-5" for r in _clean_rows(res.run_dir)[1:])


def test_signoff_refuses_a_cleaned_file_that_was_edited(tmp_path):
    res = run_pipeline(EX / "sales.csv", workdir=tmp_path / "w", policy_name="regulated", schema_path=SCHEMA,
                       analysis_path=ANALYSIS, actor="alice")
    assert res.status == "PENDING_SIGNOFF"
    (res.run_dir / "clean.csv").write_text("order_id\n1\n", encoding="utf-8")
    with pytest.raises(DataPipeError, match="cleaned data file"):
        signoff(tmp_path / "w", res.run_id, "bob")


# ------------------------------------------------------------------ four-eyes names
@pytest.mark.parametrize("variant", ["alice", "Alice", "ALICE", " alice ", "alice\t", "Al​ice", "ａｌｉｃｅ", "ALICE "])
def test_same_person_in_any_spelling_cannot_sign_off(tmp_path, variant):
    res = run_pipeline(EX / "sales.csv", workdir=tmp_path / "w", policy_name="regulated", schema_path=SCHEMA, actor="alice")
    with pytest.raises(DataPipeError, match="four-eyes"):
        signoff(tmp_path / "w", res.run_id, variant)
    assert signoff(tmp_path / "w", res.run_id, "  Bob   Jones ")["reviewer"] == "Bob Jones"


@pytest.mark.parametrize("variant", ["ALICE", "alice ", "Al​ice"])
def test_same_person_cannot_approve_their_own_mapping(wd, variant):
    prop, _ = make_proposal(wd, GOOD, actor="alice")
    with pytest.raises(DataPipeError, match="four-eyes"):
        build_approved_schema(prop, reviewer=variant, accept_review=True)
    assert build_approved_schema(prop, reviewer="bob", accept_review=True).provenance["approved_by"] == "bob"


def test_name_helpers():
    assert clean_name("  A  b​ ") == "A b"
    assert same_person("Straße", "STRASSE") and not same_person("", "") and not same_person("alice", "alicia")


# ------------------------------------------------------------------ strict schema and analysis files
@pytest.mark.parametrize("col_patch,top_patch", [
    ({"required": "false"}, {}), ({"unique": "no"}, {}), ({"pii": 1}, {}), ({"max_length": "x"}, {}), ({"max_length": 0}, {}),
    ({"max_length": True}, {}), ({"allowed": "abc"}, {}), ({"allowed": []}, {}), ({"pattern": 5}, {}), ({"source": 5}, {}),
    ({"source": ""}, {}), ({"description": 5}, {}), ({"format": 5}, {}), ({"scale": True, "type": "decimal"}, {}),
    ({"min": {}, "type": "integer"}, {}), ({"min": [1], "type": "integer"}, {}), ({"min": "1e999999999", "type": "decimal"}, {}),
    ({}, {"version": "abc"}), ({}, {"version": None}), ({}, {"version": True}), ({}, {"version": -1}), ({}, {"version": 1.5}),
    ({}, {"null_tokens": "NA"}), ({}, {"null_tokens": [1]}), ({}, {"name": ["x"]}), ({}, {"name": ""}),
])
def test_wrongly_typed_schema_values_are_errors_not_silent_changes(col_patch, top_patch):
    doc = {"name": "t", "version": 1, "columns": [{"name": "a", "type": "string", **col_patch}], **top_patch}
    with pytest.raises(SchemaError):
        schema_from_dict(doc)


def test_a_good_schema_still_loads():
    s = schema_from_dict({"name": "t", "version": 3, "null_tokens": ["", "NA"], "columns": [
        {"name": "a", "type": "string", "required": True, "unique": False, "max_length": 5, "allowed": ["x"], "source": "A col"}]})
    assert s.version == 3 and s.null_tokens == ("", "NA")


@pytest.mark.parametrize("doc", [[], None, 5, "x", {"metrics": 5}, {"metrics": [1]}, {"metrics": ["abc"]}, {"metrics": [{"name": ["x"], "sql": "select 1"}]},
                                 {"metrics": [{"name": "m", "sql": 5}]}, {"metrics": [{"name": "m", "sql": " "}]},
                                 {"metrics": [{"name": "", "sql": "select 1"}]}, {"metrics": [{"name": "m", "sql": "select 1", "description": 1}]},
                                 {"profile": "yes"}, {"metrics": None}])
def test_wrongly_shaped_analysis_files_are_errors(tmp_path, doc):
    p = tmp_path / "a.json"
    p.write_text(json.dumps(doc))
    with pytest.raises(AnalysisError):
        load_analysis(p)


def test_bad_config_files_end_as_a_clean_failed_run_with_a_closed_audit_record(tmp_path, write):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"name": "t", "version": "abc", "columns": [{"name": "a", "type": "string"}]}))
    res = run_pipeline(write("d.csv", "a\nx\n"), workdir=tmp_path / "w", policy_name="low", schema_path=bad, actor="a")
    assert res.status == "FAILED" and "version" in res.reasons[0]
    events = [r["event"] for r in AuditLog(tmp_path / "w" / "audit.jsonl").records()]
    assert events[-1] == "run_finished" and (res.run_dir / "result.json").exists()


def test_an_unexpected_bug_still_leaves_a_failed_result_and_no_data_in_the_message(tmp_path, write, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret-value-123 exploded")
    monkeypatch.setattr(pipeline, "validate", boom)
    res = run_pipeline(write("d.csv", "a\nx\n"), workdir=tmp_path / "w", policy_name="low", accept_inferred=True, actor="a")
    assert res.status == "FAILED" and res.exit_code == 1
    assert "RuntimeError" in res.reasons[0] and "secret-value-123" not in json.dumps(res.document)
    assert [r["event"] for r in AuditLog(tmp_path / "w" / "audit.jsonl").records()][-1] == "run_finished"


# ------------------------------------------------------------------ non-English headers
def test_inferred_schema_for_non_ascii_headers_can_be_loaded_again(tmp_path, write):
    tbl = read_source(write("n.csv", "größe,金额,сумма,Größe\n1,2,3,4\n"), max_bytes=10 ** 6)
    doc = infer_schema(tbl, "n").to_dict()
    assert all(c["name"].isascii() for c in doc["columns"]) and len({c["name"] for c in doc["columns"]}) == 4
    again = schema_from_dict(doc)
    assert [c.src for c in again.columns] == ["größe", "金额", "сумма", "Größe"]
    assert parse_csv("a\n1\n").columns == ["a"]
