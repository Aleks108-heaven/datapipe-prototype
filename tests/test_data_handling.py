"""QA-023..028: JSON key collisions, non-ASCII digits, refused attempts in the audit log, damaged audit logs, report wording, exit codes."""
import json

import pytest

from datapipe.audit import AuditLog
from datapipe.cli import main
from datapipe.coerce import parse_typed
from datapipe.errors import AuditError, DataPipeError
from datapipe.ingest import parse_json
from datapipe.mapping import approve_mapping, proposal_state, reject_mapping
from datapipe.pipeline import run_pipeline, signoff
from conftest import ANALYSIS, EX, SCHEMA
from helpers import GOOD, make_proposal


# ------------------------------------------------------------------ QA-023: two fields, one column name
@pytest.mark.parametrize("record", ['{"a": {"b": 1}, "a.b": 2}', '{"a.b": 2, "a": {"b": 1}}', '{"a": {"b": 1, "c": {"d": 1}}, "a.c.d": 5}'])
def test_json_fields_that_flatten_to_one_column_are_refused_not_merged(record):
    tbl = parse_json(f'[{{"a": 1}}, {record}, {{"a": 3}}]'.replace('{"a": 1}', '{"a": {"b": 0}}', 1))
    assert len(tbl.rows) == 2 and tbl.row_numbers == [1, 3]                     # the other records still load
    assert len(tbl.structural_issues) == 1 and tbl.structural_issues[0][0] == 2
    assert "same column name" in tbl.structural_issues[0][1]


def test_json_without_collisions_is_unchanged():
    tbl = parse_json('[{"a": {"b": 1}, "c": [1, 2], "d": null}]')
    assert tbl.rows == [{"a.b": 1, "c": "[1, 2]", "d": None}] and not tbl.structural_issues


# ------------------------------------------------------------------ QA-024: only ASCII digits are digits
@pytest.mark.parametrize("text", ["٠٧", "０７", "１２", "۱۲", "१२"])
def test_non_ascii_digits_are_not_integers(text):
    with pytest.raises(ValueError):
        parse_typed("integer", text)


@pytest.mark.parametrize("text", ["١٢.٥", "１２.５", "12.５"])
def test_non_ascii_digits_are_not_decimals(text):
    with pytest.raises(ValueError):
        parse_typed("decimal", text)


def test_ascii_numbers_still_parse():
    assert parse_typed("integer", "-42") == -42 and str(parse_typed("decimal", "7.50")) == "7.50"


# ------------------------------------------------------------------ QA-025: refusals leave a trace
def _events(wd, name):
    return [r for r in AuditLog(wd / "audit.jsonl").records() if r["event"] == name]


def test_refused_signoff_is_logged_and_the_chain_stays_valid(tmp_path):
    res = run_pipeline(EX / "sales.csv", workdir=tmp_path / "w", policy_name="regulated", schema_path=SCHEMA,
                       analysis_path=ANALYSIS, actor="alice")
    with pytest.raises(DataPipeError, match="four-eyes"):
        signoff(tmp_path / "w", res.run_id, "ALICE")
    refused = _events(tmp_path / "w", "signoff_refused")
    assert len(refused) == 1 and refused[0]["run_id"] == res.run_id and refused[0]["actor"] == "ALICE"
    assert "four-eyes" in refused[0]["data"]["reason"]
    assert AuditLog(tmp_path / "w" / "audit.jsonl").verify()[0]
    signoff(tmp_path / "w", res.run_id, "bob")                                   # the refusal did not block the real sign-off
    with pytest.raises(DataPipeError, match="already"):
        signoff(tmp_path / "w", res.run_id, "carol")
    assert len(_events(tmp_path / "w", "signoff_refused")) == 2


def test_refused_mapping_decisions_are_logged_and_do_not_decide_the_proposal(wd):
    prop, _ = make_proposal(wd, GOOD, actor="alice")
    with pytest.raises(DataPipeError, match="four-eyes"):
        approve_mapping(prop, reviewer="Alice ", workdir=str(wd))
    with pytest.raises(DataPipeError, match="reason"):
        reject_mapping(prop, reviewer="bob", note=" ", workdir=str(wd))
    assert len(_events(wd, "mapping_approval_refused")) == 1 and len(_events(wd, "mapping_rejection_refused")) == 1
    assert proposal_state(AuditLog(wd / "audit.jsonl").records(), prop["proposal_sha256"])["state"] == "pending"
    approve_mapping(prop, reviewer="bob", workdir=str(wd))
    with pytest.raises(DataPipeError, match="already approved"):
        reject_mapping(prop, reviewer="carol", note="too late", workdir=str(wd))
    assert AuditLog(wd / "audit.jsonl").verify()[0]


def test_a_tampered_proposal_attempt_is_logged(wd):
    prop, _ = make_proposal(wd, GOOD, actor="alice")
    prop["items"][0]["confidence"] = 0.1
    with pytest.raises(DataPipeError, match="modified"):
        approve_mapping(prop, reviewer="bob", workdir=str(wd))
    assert "modified" in _events(wd, "mapping_approval_refused")[0]["data"]["reason"]


# ------------------------------------------------------------------ QA-026: a damaged audit log is reported, not crashed on
def _log_with_two_records(tmp_path):
    wd = tmp_path / "w"
    log = AuditLog(wd / "audit.jsonl")
    log.append("a", "r1", {"x": 1}, actor="u")
    log.append("b", "r1", {"x": 2}, actor="u")
    return wd, log


def test_torn_last_line_is_refused_with_a_clear_message_and_nothing_is_written(tmp_path):
    wd, log = _log_with_two_records(tmp_path)
    with open(log.path, "a", encoding="utf-8") as fh:
        fh.write('{"seq": 2, "event": "c", "pr')                                # a write that was cut off
    before = log.path.read_bytes()
    with pytest.raises(AuditError, match="damaged.*line 3"):
        log.append("d", "r1", {})
    assert log.path.read_bytes() == before
    ok, n, msg = log.verify()
    assert not ok and n == 2 and "line 3" in msg                                  # two intact records, then the problem
    with pytest.raises(AuditError):
        log.records()


def test_a_line_that_is_not_an_object_does_not_crash_verification(tmp_path):
    wd, log = _log_with_two_records(tmp_path)
    for junk in ("[1, 2]", "42", '"text"', "null"):
        log.path.write_text(log.path.read_text(encoding="utf-8") + junk + "\n", encoding="utf-8")
        ok, n, msg = log.verify()
        assert not ok and "not a log record" in msg, (junk, msg)
        assert main(["--workdir", str(wd), "verify-audit"]) == 1
        log.path.write_text("\n".join(log.path.read_text(encoding="utf-8").splitlines()[:2]) + "\n", encoding="utf-8")


def test_a_valid_last_record_without_a_newline_is_not_glued_to_the_next_one(tmp_path):
    wd, log = _log_with_two_records(tmp_path)
    log.path.write_bytes(log.path.read_bytes().rstrip(b"\r\n"))
    log.append("c", "r1", {})
    ok, n, _ = log.verify()
    assert ok and n == 3


def test_log_that_is_not_text_is_reported(tmp_path):
    wd, log = _log_with_two_records(tmp_path)
    log.path.write_bytes(b"\xff\xfe\x00 not text")
    assert log.verify()[0] is False
    with pytest.raises(AuditError, match="UTF-8"):
        log.append("c", None, {})


def test_unicode_line_separators_inside_a_record_are_not_line_breaks(tmp_path):
    wd, log = _log_with_two_records(tmp_path)
    log.append("note", "r1", {"text": "a\u2028b\u0085c\x0bd"}, actor="u")        # json keeps these raw; they must not split the record
    assert log.verify()[0] and len(log.records()) == 3


def test_a_run_against_a_damaged_log_fails_cleanly_and_leaves_no_run_folder(tmp_path, capsys):
    wd = tmp_path / "w"
    wd.mkdir()
    (wd / "audit.jsonl").write_text('{"seq": 0, "tor', encoding="utf-8")
    code = main(["--workdir", str(wd), "run", str(EX / "sales.csv"), "--policy", "low", "--schema", str(SCHEMA)])
    err = capsys.readouterr().err
    assert code == 1 and "audit log is damaged" in err and "Traceback" not in err
    assert not (wd / "runs").exists() or not list((wd / "runs").iterdir())
    assert main(["--workdir", str(wd), "verify-audit"]) == 1
    assert "0 records" in capsys.readouterr().out


# ------------------------------------------------------------------ QA-027: the report says what "row" means
def test_report_explains_row_numbers_and_prints_no_python_dict(tmp_path):
    dirty = tmp_path / "d.csv"
    dirty.write_text((EX / "sales.csv").read_text().rstrip("\n") + "\nnot-an-id,x,y,z,1,2,3\n", encoding="utf-8")
    res = run_pipeline(dirty, workdir=tmp_path / "w", policy_name="low", schema_path=SCHEMA, analysis_path=ANALYSIS, actor="a")
    report = (res.run_dir / "report.md").read_text(encoding="utf-8")
    assert "N-th data record after the header" in report and "sheet row N+1" in report
    assert "{'" not in report and "problems found, by kind:" in report


# ------------------------------------------------------------------ QA-028: usage errors are not "blocked by policy"
def test_usage_errors_exit_64_and_policy_blocks_still_exit_2(tmp_path, capsys):
    for argv in ([], ["run"], ["bogus"], ["run", "x.csv", "--policy", "nonsense"]):
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code == 64, argv
    assert "error:" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    dirty = tmp_path / "d.csv"
    dirty.write_text("order_id,x\nbad,1\n", encoding="utf-8")
    assert main(["--workdir", str(tmp_path / "w"), "run", str(dirty), "--policy", "regulated", "--schema", str(SCHEMA)]) == 2


# ------------------------------------------------------------------ trimmed spaces are reported, not silent
def test_csv_values_with_edge_spaces_are_trimmed_and_the_file_says_so():
    from datapipe.ingest import parse_csv
    tbl = parse_csv("a, b\n 1 ,2\n3,4\n")
    assert tbl.columns == ["a", "b"] and tbl.rows[0] == {"a": "1", "b": "2"}
    assert any("2 record(s) or header(s)" in w and "' 5 ' is read as '5'" in w for w in tbl.warnings)
    assert parse_csv("a,b\n1,2\n").warnings == []
