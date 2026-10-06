"""Streaming CSV pipeline: it must give exactly what the in-memory pipeline gives (same results hash, same cleaned and
quarantine files, same counts, warnings and failure messages), in bounded memory, and leave no work files behind."""
import csv
import io
import json
import random

import pytest

import datapipe.analyze as analyze
import datapipe.pipeline as pipeline
import datapipe.stream as stream_mod
from datapipe.analyze import PyStats, python_stats
from datapipe.audit import AuditLog
from datapipe.errors import IngestError
from datapipe.pipeline import run_pipeline, signoff
from datapipe.policy import get_policy
from datapipe.schema import ColumnInferrer, infer_type, present_values, schema_from_dict
from datapipe.stream import CsvReader, scan_csv
from conftest import EX

COMPARED_FILES = ("clean.csv", "quarantine.csv", "issues.json", "proposed_schema.json")
LEFTOVERS = ("quarantine.spool", "clean.partial")


def _snapshot(res):
    d = res.document
    files = {n: (res.run_dir / n).read_bytes() for n in COMPARED_FILES if (res.run_dir / n).exists()}
    left = [p.name for p in res.run_dir.iterdir() if p.name.startswith("engine-") or p.name in LEFTOVERS or p.name.startswith("load-")]
    assert left == [], f"work files left behind: {left}"
    return {"status": res.status, "reasons": res.reasons, "counts": d["counts"], "results": d["results"],
            "results_sha256": d["results_sha256"], "warnings": d["warnings"], "schema": d["schema"],
            "clean": (d["outputs"] or {}).get("clean_csv"), "files": files}


def both(tmp_path, src, schema=None, analysis=None, policy="low", **kw):
    """Run the same file through the in-memory and the streaming pipeline; assert identical output; return the pair."""
    snaps = {}
    for mode in ("never", "always"):
        res = run_pipeline(src, workdir=tmp_path / f"w-{mode}", policy_name=policy, schema_path=schema, analysis_path=analysis,
                           actor="alice", stream=mode, **kw)
        assert res.document["source"].get("streamed", False) == (mode == "always")
        snaps[mode] = _snapshot(res)
    assert snaps["never"] == snaps["always"]
    return snaps["never"]


# ------------------------------------------------------------------ a deliberately awkward file
NOTES = ["", "plain", "a, comma", 'say "hi"', "two\nlines", "cr\r\nlf", "=1+1", "+x", "-5", "+49 (0) 30-123", "@home", "\tTab",
         "ünï©ode 金额 🙂", "  padded  ", "NA", "NULL", r"\N"]

TORTURE_SCHEMA = {"name": "torture", "version": 1, "null_tokens": ["", "NA"], "columns": [
    {"name": "id", "type": "integer", "required": True, "unique": True, "min": 0},
    {"name": "code", "type": "string", "unique": True, "max_length": 8},
    {"name": "amount", "type": "decimal", "scale": 2, "min": "-100", "max": "10000"},
    {"name": "day", "type": "date"},
    {"name": "flag", "type": "boolean"},
    {"name": "note", "type": "string"},
    {"name": "email", "type": "string", "source": "Email Address", "pii": True, "unique": True},
    {"name": "qty", "type": "integer", "allowed": [1, 2, 3, 4, 5]},
]}
TORTURE_ANALYSIS = {"metrics": [
    {"name": "by_flag", "sql": "SELECT flag, count(*) AS n, sum(amount) AS total, avg(amount) AS mean FROM data GROUP BY flag"},
    {"name": "by_qty", "sql": "SELECT qty, count(*) AS n, min(day) AS first_day, max(day) AS last_day FROM data GROUP BY qty"},
    {"name": "ordered", "sql": "SELECT id, code, amount FROM data ORDER BY id LIMIT 50"},
]}


def torture_csv(rows=1500, seed=1, code_len=4, email_space=2000, id_dups=0.03, damage=0.04, eol="\n", delimiter=","):
    rnd = random.Random(seed)
    out = io.StringIO()
    w = csv.writer(out, delimiter=delimiter, lineterminator=eol)
    w.writerow(["id", "code", "amount", "day", "flag", "note", "Email Address", "qty"])
    ids = []
    for i in range(rows):
        if ids and rnd.random() < id_dups:
            rid = str(rnd.choice(ids))
        elif rnd.random() < damage:
            rid = rnd.choice(["abc", "", "007", "-3", "1.5"])
        else:
            rid = str(i + 1)
            ids.append(i + 1)
        amount = rnd.choice([f"{rnd.uniform(-120, 11000):.2f}", f"{rnd.uniform(0, 50):.2f}", f"{rnd.uniform(0, 50):.3f}", "", "NA", "1e3"]) \
            if rnd.random() < 0.15 + damage else f"{rnd.uniform(0, 500):.2f}"
        row = [rid, "".join(rnd.choice("abcdefghij") for _ in range(code_len)) + ("-too-long" if rnd.random() < damage else ""),
               amount, rnd.choice(["2026-01-05", "2026-02-30", "2025-12-31", "", "5/1/2026"]) if rnd.random() < 0.3 else f"2026-0{rnd.randint(1, 9)}-1{rnd.randint(0, 9)}",
               rnd.choice(["true", "false", "yes", "maybe", "", "1", "N"]), rnd.choice(NOTES),
               f"user{rnd.randint(0, email_space)}@x.test", rnd.choice(["1", "2", "3", "4", "5", "6", "", "NA"])]
        if rnd.random() < damage:
            row = row[:-1] if rnd.random() < 0.5 else row + ["extra"]
        if rnd.random() < 0.05:
            row = [c if rnd.random() < 0.8 else f"  {c} " for c in row]
        w.writerow(row)
        if rnd.random() < 0.01:
            out.write(eol)                           # a blank line
    return out.getvalue()


@pytest.fixture
def torture_files(tmp_path):
    schema = tmp_path / "torture_schema.json"
    schema.write_text(json.dumps(TORTURE_SCHEMA))
    analysis = tmp_path / "torture_analysis.json"
    analysis.write_text(json.dumps(TORTURE_ANALYSIS))
    return schema, analysis


def test_awkward_file_gives_identical_output_in_both_modes(tmp_path, write, torture_files):
    schema, analysis = torture_files
    src = write("t.csv", torture_csv().encode("utf-8"))
    snap = both(tmp_path, src, schema, analysis)
    c = snap["counts"]
    assert c["quarantined"] > 100 and c["valid"] > 100                                  # the file really exercises both outcomes
    assert {"structure", "type", "required", "unique", "allowed", "min", "max_length"} <= set(c["issues_by_rule"])
    assert snap["status"] == "COMPLETED_WITH_WARNINGS"


@pytest.mark.parametrize("eol", ["\n", "\r\n", "\r"])
def test_line_endings(tmp_path, write, torture_files, eol):
    schema, analysis = torture_files
    both(tmp_path, write("t.csv", torture_csv(rows=400, seed=3, eol=eol).encode("utf-8")), schema, analysis)


def test_bom_and_explicit_encoding(tmp_path, write, torture_files):
    schema, analysis = torture_files
    text = torture_csv(rows=300, seed=4)
    both(tmp_path / "bom", write("bom.csv", b"\xef\xbb\xbf" + text.encode("utf-8")), schema, analysis)
    latin = text.encode("latin-1", errors="replace")
    both(tmp_path / "latin", write("latin.csv", latin), schema, analysis, encoding="latin-1")


def test_semicolon_and_tab_files(tmp_path, write, torture_files):
    schema, analysis = torture_files
    both(tmp_path / "semi", write("s.csv", torture_csv(rows=300, seed=5, delimiter=";").encode()), schema, analysis)
    both(tmp_path / "tab", write("t.tsv", torture_csv(rows=300, seed=5, delimiter="\t").encode()), schema, analysis)
    both(tmp_path / "explicit", write("p.csv", torture_csv(rows=300, seed=5, delimiter="|").encode()), schema, analysis, delimiter="|")


@pytest.mark.parametrize("chunk", [1, 7, 20_000])
def test_chunk_size_does_not_change_anything(tmp_path, write, torture_files, monkeypatch, chunk):
    monkeypatch.setattr(stream_mod, "CHUNK_ROWS", chunk)
    schema, analysis = torture_files
    both(tmp_path, write("t.csv", torture_csv(rows=500, seed=6).encode()), schema, analysis)


def test_inferred_schema_matches(tmp_path, write):
    src = write("t.csv", torture_csv(rows=600, seed=7, damage=0).encode())
    snap = both(tmp_path, src, policy="low", accept_inferred=True)
    assert snap["schema"]["inferred"] and snap["status"].startswith("COMPLETED")
    # business policy stops for confirmation: the proposed schema must be the same file
    both(tmp_path / "confirm", src, policy="business")


def test_inferred_schema_with_blank_columns_and_booleans(tmp_path, write):
    text = "a,b,c,d,e,f\n1, ,true,2026-01-01,1.5,x\n2,,FALSE,2026-01-02,2,y\n3,,true,,3.25,\n"
    snap = both(tmp_path, write("i.csv", text.encode()), accept_inferred=True)
    assert snap["schema"]["inferred"] and snap["status"].startswith("COMPLETED")


def test_column_inferrer_agrees_with_infer_type():
    rnd = random.Random(2)
    pool = ["1", "2", "007", "-4", "1.5", "2026-01-02", "true", "FALSE", "x", "", " ", "1e3", "٣", "2026-1-2", "+5", "0"]
    for _ in range(400):
        values = [rnd.choice(pool) for _ in range(rnd.randint(0, 6))]
        inf = ColumnInferrer()
        for v in values:
            inf.add(v)
        present = present_values(values)
        assert inf.type == infer_type(present)
        assert inf.required == (len(present) == len(values) and len(values) > 0)


@pytest.mark.parametrize("policy", ["low", "business", "regulated"])
def test_policies_and_masking(tmp_path, write, torture_files, policy):
    schema, analysis = torture_files
    clean = torture_csv(rows=800, seed=8, code_len=6, email_space=10 ** 6, id_dups=0.002, damage=0.002)
    both(tmp_path / "clean", write("c.csv", clean.encode()), schema, analysis, policy=policy)
    both(tmp_path / "dirty", write("d.csv", torture_csv(rows=800, seed=9).encode()), schema, analysis, policy=policy)


def test_regulated_streamed_run_can_be_signed_off(tmp_path, write, torture_files):
    good = "id,code,amount,day,flag,note,Email Address,qty\n" + "".join(
        f"{i},c{i},{i}.50,2026-01-0{i % 9 + 1},true,n,u{i}@x.test,{i % 5 + 1}\n" for i in range(1, 40))
    schema, analysis = torture_files
    res = run_pipeline(write("g.csv", good.encode()), workdir=tmp_path / "w", policy_name="regulated", schema_path=schema,
                       analysis_path=analysis, actor="alice", stream="always")
    assert res.status == "PENDING_SIGNOFF" and res.document["source"]["streamed"] is True
    out = signoff(tmp_path / "w", res.run_id, "bob")
    assert out["results_sha256"] == res.document["results_sha256"]
    ok, _, _ = AuditLog(tmp_path / "w" / "audit.jsonl").verify()
    assert ok


def test_examples_from_the_repository(tmp_path):
    both(tmp_path / "sales", EX / "sales.csv", EX / "schema_sales.json", EX / "analysis_sales.json")
    both(tmp_path / "dirty", EX / "sales_dirty.csv", EX / "schema_sales.json", EX / "analysis_sales.json")


def test_generated_sample_with_duplicates(tmp_path):
    from datapipe.sample import generate
    path = tmp_path / "b.csv"
    generate(path, rows=6000, bad_percent=4, seed=11)
    snap = both(tmp_path, path, EX / "schema_buyers.json", EX / "analysis_buyers.json", policy="business")
    assert snap["counts"]["issues_by_rule"]["unique"] > 0


# ------------------------------------------------------------------ duplicates in unique columns
def _schema(columns):
    return json.dumps({"name": "u", "version": 1, "columns": columns})


def test_duplicates_of_every_type_and_a_row_duplicated_in_two_columns(tmp_path, write):
    schema = write("s.json", _schema([
        {"name": "i", "type": "integer", "unique": True}, {"name": "d", "type": "decimal", "scale": 2, "unique": True},
        {"name": "t", "type": "date", "unique": True}, {"name": "b", "type": "boolean", "unique": True},
        {"name": "s", "type": "string", "unique": True, "pattern": "[a-c]+"}]).encode())
    text = ("i,d,t,b,s\n1,1.5,2026-01-01,true,a\n2,1.50,2026-01-02,false,b\n1,2.5,2026-01-01,true,zzz\n"
            "3,1.500,2026-01-03,,c\n4,9,2026-01-04,,A\n5,9.00,2026-01-04,,a\n6,0.1,2026-01-06,,\n")
    snap = both(tmp_path, write("u.csv", text.encode()), schema)
    assert snap["counts"]["issues_by_rule"]["unique"] >= 8
    q = snap["files"]["quarantine.csv"].decode()
    assert "duplicate value shared by 2 rows" in q


def test_first_row_of_a_group_has_another_problem_and_values_are_shown_identically(tmp_path, write):
    schema = write("s.json", _schema([{"name": "k", "type": "decimal", "scale": 3, "unique": True},
                                       {"name": "v", "type": "integer", "min": 10}]).encode())
    rows = "".join(f"{x},{y}\n" for x, y in [("1.50", 5), ("1.5", 20), ("2", 30), ("2.000", 40), ("3", 50), ("1.500", 60)])
    snap = both(tmp_path, write("f.csv", ("k,v\n" + rows).encode()), schema)
    assert snap["counts"]["quarantined"] == 5


def test_unique_column_that_is_pii_is_masked_in_the_duplicate_report(tmp_path, write):
    schema = write("s.json", _schema([{"name": "email", "type": "string", "unique": True, "pii": True},
                                       {"name": "n", "type": "integer"}]).encode())
    text = "email,n\na@x.test,1\nb@x.test,2\na@x.test,3\n" + "".join(f"u{i}@x.test,{i}\n" for i in range(60))
    snap = both(tmp_path, write("p.csv", text.encode()), schema, policy="business")
    assert b"a@x.test" not in snap["files"]["issues.json"] and b"<masked>" in snap["files"]["issues.json"]


def test_nothing_unique_means_no_second_pass_needed(tmp_path, write):
    schema = write("s.json", _schema([{"name": "n", "type": "integer"}]).encode())
    snap = both(tmp_path, write("n.csv", b"n\n1\n2\nx\n3\n"), schema)
    assert snap["counts"]["quarantined"] == 1


def test_too_many_duplicates_is_a_clear_failure(tmp_path, write, monkeypatch):
    monkeypatch.setattr(stream_mod, "MAX_DUPLICATE_ROWS", 5)
    schema = write("s.json", _schema([{"name": "k", "type": "integer", "unique": True}]).encode())
    res = run_pipeline(write("d.csv", ("k\n" + "1\n" * 20).encode()), workdir=tmp_path / "w", policy_name="low",
                       schema_path=schema, actor="a", stream="always")
    assert res.status == "FAILED" and "unique" in res.reasons[0]
    assert not [p for p in res.run_dir.iterdir() if p.name.startswith("engine-") or p.name in LEFTOVERS]


# ------------------------------------------------------------------ the same refusals, in the same words
def _failures(tmp_path, src, **kw):
    out = {}
    for mode in ("never", "always"):
        res = run_pipeline(src, workdir=tmp_path / f"w-{mode}", policy_name="low", actor="a", stream=mode, accept_inferred=True, **kw)
        out[mode] = (res.status, res.reasons)
    assert out["never"] == out["always"], out
    return out["never"]


@pytest.mark.parametrize("name,content", [
    ("empty.csv", b""),
    ("header_only.csv", b"a,b\n"),
    ("nul.csv", b"a,b\n1,\x002\n"),
    ("bad_utf8.csv", b"a,b\n1,\xff\xfe\n"),
    ("bad_quote.csv", b'a,b\n1,"2\n"x\n'),
    ("dup_header.csv", b"a,a\n1,2\n"),
    ("empty_header.csv", b"a,\n1,2\n"),
    ("only_blank.csv", b"\n\n\n"),
    ("wrong_fields.csv", b"a,b\n1\n2,3,4\n"),
])
def test_refusals_match(tmp_path, name, content):
    (tmp_path / name).write_bytes(content)
    status, reasons = _failures(tmp_path, tmp_path / name)
    assert status in ("FAILED", "BLOCKED") or name == "header_only.csv"


def test_missing_required_and_extra_columns_block_identically(tmp_path, write, torture_files):
    schema, analysis = torture_files
    src = write("m.csv", b"id,code\n1,a\n")
    for policy in ("low", "regulated"):
        both(tmp_path / policy, src, schema, analysis, policy=policy)
    extra = write("e.csv", b"id,code,amount,day,flag,note,Email Address,qty,surprise\n1,a,1.00,2026-01-01,true,n,e@x.test,1,s\n")
    both(tmp_path / "extra", extra, schema, analysis, policy="regulated")
    both(tmp_path / "extra_low", extra, schema, analysis, policy="low")


def test_a_missing_file_and_a_directory_fail_the_same_way(tmp_path):
    assert _failures(tmp_path, tmp_path / "nope.csv")[0] == "FAILED"
    assert _failures(tmp_path, tmp_path)[0] == "FAILED"


def test_the_size_limit_applies_to_streaming_too(tmp_path, write):
    src = write("big.csv", b"a\n" + b"1\n" * 600_000)               # about 1.2 MB
    res = run_pipeline(src, workdir=tmp_path / "w", policy_name="low", actor="a", stream="always", accept_inferred=True, max_file_mb=1)
    assert res.status == "FAILED" and "policy limit" in res.reasons[0]


def test_streaming_is_not_limited_by_the_memory_estimate(tmp_path, write):
    src = write("rows.csv", b"a\n" + b"1\n" * 50_000)
    never = run_pipeline(src, workdir=tmp_path / "n", policy_name="low", actor="a", stream="never", accept_inferred=True, max_memory_gb=0.001)
    streamed = run_pipeline(src, workdir=tmp_path / "s", policy_name="low", actor="a", stream="always", accept_inferred=True, max_memory_gb=0.001)
    assert never.status == "FAILED" and "memory" in never.reasons[0]
    assert streamed.status.startswith("COMPLETED") and streamed.document["counts"]["valid"] == 50_000


# ------------------------------------------------------------------ choosing the mode
def test_auto_streams_only_big_csv_files(tmp_path, write, monkeypatch):
    monkeypatch.setattr(pipeline, "STREAM_AUTO_BYTES", 100)
    small = write("s.csv", b"a\n1\n")
    big = write("b.csv", b"a\n" + b"1\n" * 100)
    jsn = write("j.json", json.dumps([{"a": i} for i in range(100)]).encode())
    got = {}
    for name, p in (("small", small), ("big", big), ("json", jsn)):
        res = run_pipeline(p, workdir=tmp_path / name, policy_name="low", actor="a", accept_inferred=True)
        assert res.status.startswith("COMPLETED"), res.reasons
        got[name] = res.document["source"].get("streamed", False)
    assert got == {"small": False, "big": True, "json": False}


def test_streaming_a_json_file_is_refused_with_a_clear_message(tmp_path, write):
    res = run_pipeline(write("j.json", b'[{"a": 1}]'), workdir=tmp_path / "w", policy_name="low", actor="a", stream="always")
    assert res.status == "FAILED" and "CSV" in res.reasons[0]


def test_unknown_stream_mode_is_refused(tmp_path, write):
    res = run_pipeline(write("a.csv", b"a\n1\n"), workdir=tmp_path / "w", policy_name="low", actor="a", stream="sometimes")
    assert res.status == "FAILED" and "stream" in res.reasons[0]


def test_a_file_that_changes_between_passes_fails_the_run(tmp_path, write, torture_files, monkeypatch):
    schema, analysis = torture_files
    src = write("t.csv", torture_csv(rows=200, seed=2).encode())
    real = stream_mod.StreamBackend.validate

    def tamper(self, schema_):
        with open(self.path, "ab") as fh:
            fh.write(b"9999,zz,1.00,2026-01-01,true,n,e@x.test,1\n")
        return real(self, schema_)
    monkeypatch.setattr(stream_mod.StreamBackend, "validate", tamper)
    res = run_pipeline(src, workdir=tmp_path / "w", policy_name="low", schema_path=schema, analysis_path=analysis, actor="a", stream="always")
    assert res.status == "FAILED" and "changed" in res.reasons[0]
    assert not (res.run_dir / "clean.csv").exists()


def test_blocked_and_failed_runs_leave_no_cleaned_file_or_work_files(tmp_path, write, torture_files):
    schema, analysis = torture_files
    res = run_pipeline(write("t.csv", torture_csv(rows=300, seed=12).encode()), workdir=tmp_path / "w", policy_name="business",
                       schema_path=schema, analysis_path=analysis, actor="a", stream="always")
    assert res.status == "BLOCKED" and not (res.run_dir / "clean.csv").exists()
    assert not [p.name for p in res.run_dir.iterdir() if p.name.startswith("engine-") or p.name in LEFTOVERS]


# ------------------------------------------------------------------ the reader and the Python-side statistics
def test_scan_reports_what_parse_csv_reports(tmp_path, write):
    from datapipe.ingest import parse_csv
    text = " a ,b\n1, 2 \n\n3\n4,5,6\n 7 ,8\n"
    p = write("s.csv", text.encode())
    info = scan_csv(p)
    tbl = parse_csv(text)
    assert (info.columns, info.n_rows, info.n_structural, info.warnings) == (tbl.columns, len(tbl.rows), len(tbl.structural_issues), tbl.warnings)


def test_reader_hashes_every_byte_and_refuses_nul(tmp_path, write):
    import hashlib
    data = b"a,b\n" + b"1,2\n" * 100_000
    p = write("h.csv", data)
    with CsvReader(p) as rd:
        assert sum(1 for _ in rd.records()) == 100_000
        assert rd.sha256 == hashlib.sha256(data).hexdigest() and rd.bytes_read == len(data)
    p2 = write("n.csv", b"a\n1\n" + b"x" * 100_000 + b"\x00\n")
    with pytest.raises(IngestError, match="NUL"):
        with CsvReader(p2) as rd:
            list(rd.records())


def test_python_stats_budget_skips_the_distinct_check_and_says_so():
    schema = schema_from_dict({"name": "t", "columns": [{"name": "a", "type": "integer"}, {"name": "b", "type": "integer"}]})
    rows = [{"_row": i, "a": i, "b": i % 3} for i in range(100)]
    stats = PyStats(schema, get_policy("low"), distinct_budget=50)
    for r in rows:
        stats.add(r)
    got, skipped = stats.results()
    assert ("a", "distinct") in skipped and "distinct" not in got["a"]
    assert got["a"]["sum"] == sum(range(100)) and got["a"]["min"] == 0 and got["a"]["max"] == 99
    assert got["b"]["distinct"] == 3 and ("b", "distinct") not in skipped
    full, none = python_stats(schema, rows, get_policy("low"))
    assert none == [] and full["a"]["distinct"] == 100


def test_unchecked_statistics_are_reported_in_the_reconciliation(tmp_path, write, monkeypatch):
    monkeypatch.setattr(analyze, "DISTINCT_BUDGET", 5)
    orig = analyze.PyStats.__init__
    monkeypatch.setattr(analyze.PyStats, "__init__", lambda self, schema, policy, distinct_budget=5: orig(self, schema, policy, distinct_budget))
    res = run_pipeline(write("a.csv", b"a,b\n" + b"".join(f"{i},{i % 2}\n".encode() for i in range(50))), workdir=tmp_path / "w",
                       policy_name="low", actor="a", stream="always", accept_inferred=True)
    rec = res.document["results"]["reconciliation"]
    assert res.status.startswith("COMPLETED") and rec["mismatches"] == [] and rec["not_checked"]


def test_command_line_stream_option(tmp_path, write, capsys):
    from datapipe.cli import main
    src = write("a.csv", b"a,b\n1,x\n2,y\n")
    schema = write("s.json", _schema([{"name": "a", "type": "integer"}, {"name": "b", "type": "string"}]).encode())
    rc = main(["--workdir", str(tmp_path / "w"), "run", str(src), "--policy", "low", "--schema", str(schema), "--stream", "always"])
    assert rc == 0 and "COMPLETED" in capsys.readouterr().out
    last = sorted((tmp_path / "w" / "runs").iterdir())[-1]
    assert json.loads((last / "result.json").read_text())["source"]["streamed"] is True
    with pytest.raises(SystemExit) as exc:
        main(["--workdir", str(tmp_path / "w"), "run", str(src), "--stream", "sometimes"])
    assert exc.value.code == 64


def test_masked_personal_columns_never_reach_a_work_file_even_if_the_run_dies(tmp_path, write, monkeypatch):
    schema = write("s.json", _schema([
        {"name": "email", "type": "string", "unique": True, "pii": True}, {"name": "pay", "type": "decimal", "scale": 2, "unique": True, "pii": True},
        {"name": "age", "type": "integer", "min": 18}]).encode())
    text = ("email,pay,age\njane.secret@x.test,1000.5,30\nbob.hidden@x.test,2000,31\njane.secret@x.test,3000,32\n"
            "carl.private@x.test,1000.50,12\n" + "".join(f"u{i}@x.test,{i + 5000},40\n" for i in range(150)))
    snap = both(tmp_path / "same", write("p.csv", text.encode()), schema, policy="business")
    assert snap["counts"]["issues_by_rule"]["unique"] == 4                    # two e-mail rows, two pay rows (1000.5 and 1000.50)
    monkeypatch.setattr(stream_mod.StreamBackend, "cleanup", lambda self: None)       # as if the process were killed: nothing is cleaned up
    monkeypatch.setattr(stream_mod.StreamBackend, "analyze", lambda self, *a: (_ for _ in ()).throw(RuntimeError("boom")))
    res = run_pipeline(write("p2.csv", text.encode()), workdir=tmp_path / "w", policy_name="business", schema_path=schema, actor="a", stream="always")
    assert res.status == "FAILED"
    leftovers = [p for p in res.run_dir.iterdir() if p.name.startswith("engine-") or p.name in LEFTOVERS]
    assert leftovers                                                           # the work files are really still there
    for p in res.run_dir.rglob("*"):
        if p.is_file():
            blob = p.read_bytes()
            assert b"jane.secret" not in blob and b"bob.hidden" not in blob and b"carl.private" not in blob, p.name
