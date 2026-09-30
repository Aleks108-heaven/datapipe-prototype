import json
from pathlib import Path

import pytest

from conftest import CLEAN_CSV, EX, SCHEMA
from datapipe.audit import AuditLog
from datapipe.cli import main
from datapipe.errors import DataPipeError
from datapipe.pipeline import signoff

DIRTY = (EX / "sales_dirty.csv").read_text()


def all_text(root: Path):
    return "\n".join(p.read_text(errors="ignore") for p in root.rglob("*") if p.is_file())


def test_clean_file_golden_results(write, run):
    r = run(write("s.csv", CLEAN_CSV), "low")
    assert r.status == "COMPLETED"
    m = r.document["results"]["metrics"]
    assert m["total_amount"]["rows"] == [["560.31"]]
    assert m["by_region"]["rows"] == [["APAC", "10.31", 3], ["EU", "200.00", 2], ["US", "350.00", 3]]
    assert m["paid_share"]["rows"] == [[6, 8]]
    assert r.document["counts"]["quarantined"] == 0


def test_same_data_in_csv_json_and_sql_gives_identical_results_hash(run):
    hashes = {f: run(EX / f"sales.{f}", "low").document["results_sha256"] for f in ("csv", "json", "sql")}
    assert len(set(hashes.values())) == 1 and None not in hashes.values()


def test_results_are_reproducible(write, run):
    p = write("s.csv", CLEAN_CSV)
    assert run(p).document["results_sha256"] == run(p).document["results_sha256"]


# ---------------------------------------------------------------- policy tiers on dirty data
def test_low_tier_quarantines_and_continues(write, run):
    r = run(write("d.csv", DIRTY), "low")
    assert r.status == "COMPLETED_WITH_WARNINGS"
    assert r.document["counts"]["valid"] == 2 and r.document["counts"]["quarantined"] == 8
    assert (r.run_dir / "quarantine.csv").exists()
    assert r.document["results"]["metrics"]["total_amount"]["rows"] == [["120.70"]]     # only rows 1001 + 1008 counted


def test_business_tier_blocks_above_error_rate(write, run):
    r = run(write("d.csv", DIRTY), "business")
    assert r.status == "BLOCKED" and r.document["results"] is None and "error rate" in r.reasons[0]


def _mixed(n_good, n_bad):
    head = "order_id,customer_email,region,amount,order_date,paid\n"
    good = "".join(f"{i},u{i}@x.io,EU,1.00,2026-01-01,true\n" for i in range(1, n_good + 1))
    bad = "".join(f"{1000 + i},u@x.io,EU,-1.00,2026-01-01,true\n" for i in range(n_bad))
    return head + good + bad


def test_business_error_rate_boundary_exactly_5_percent_passes_above_blocks(write, run):
    at_limit = run(write("a.csv", _mixed(38, 2)), "business")        # 2/40 = 5.0% -> allowed
    assert at_limit.status == "COMPLETED_WITH_WARNINGS" and at_limit.document["counts"]["quarantined"] == 2
    over = run(write("b.csv", _mixed(37, 3)), "business")            # 3/40 = 7.5% -> blocked
    assert over.status == "BLOCKED" and "error rate" in over.reasons[0]


def test_regulated_tier_blocks_on_any_single_error_and_gives_no_partial_results(write, run):
    head = "order_id,customer_email,region,amount,order_date,paid\n"
    rows = "".join(f"{i},u{i}@x.io,EU,1.00,2026-01-01,true\n" for i in range(1, 100))
    r = run(write("d.csv", head + rows + "100,u@x.io,EU,-1.00,2026-01-01,true\n"), "regulated")
    assert r.status == "BLOCKED" and r.document["results"] is None and r.document["results_sha256"] is None


def test_regulated_clean_run_waits_for_signoff(write, run):
    r = run(write("s.csv", CLEAN_CSV), "regulated")
    assert r.status == "PENDING_SIGNOFF" and r.document["results"] is not None


# ---------------------------------------------------------------- schema policy
def test_regulated_requires_registered_schema(write, run):
    assert run(write("s.csv", CLEAN_CSV), "regulated", schema=None).status == "BLOCKED"


def test_business_needs_confirmation_of_inferred_schema(write, run):
    p = write("s.csv", CLEAN_CSV)
    r = run(p, "business", schema=None)
    assert r.status == "NEEDS_SCHEMA_CONFIRMATION" and (r.run_dir / "proposed_schema.json").exists()
    assert r.document["results"] is None
    r2 = run(p, "business", schema=None, accept_inferred=True)
    assert r2.status == "COMPLETED_WITH_WARNINGS" and any("inferred" in w for w in r2.document["warnings"])


def test_low_tier_infers_schema_with_warning(write, run):
    r = run(write("s.csv", CLEAN_CSV), "low", schema=None, analysis=None)
    assert r.status == "COMPLETED_WITH_WARNINGS" and r.document["schema"]["inferred"]


def test_extra_column_drift_warns_in_low_and_blocks_in_regulated(write, run):
    lines = CLEAN_CSV.splitlines()
    text = "\n".join([lines[0] + ",notes"] + [l + ",x" for l in lines[1:]]) + "\n"
    p = write("s.csv", text)
    low = run(p, "low")
    assert low.status == "COMPLETED_WITH_WARNINGS" and any("notes" in w for w in low.document["warnings"])
    reg = run(p, "regulated")
    assert reg.status == "BLOCKED" and "drift" in reg.reasons[0]


@pytest.mark.parametrize("policy", ["low", "business", "regulated"])
def test_missing_required_column_blocks_every_tier(write, run, policy):
    lines = [",".join(c for j, c in enumerate(l.split(",")) if j != 2) for l in CLEAN_CSV.splitlines()]
    r = run(write("s.csv", "\n".join(lines) + "\n"), policy)
    assert r.status == "BLOCKED" and "region" in r.reasons[0]


def test_header_only_file_is_blocked_not_reported_as_zero(write, run):
    r = run(write("s.csv", CLEAN_CSV.splitlines()[0] + "\n"), "low")
    assert r.status == "BLOCKED" and "no data rows" in r.reasons[0]


def test_failures_are_recorded_not_raised(tmp_path, run, wd):
    r = run(tmp_path / "missing.csv")
    assert r.status == "FAILED" and r.exit_code == 1
    events = [x["event"] for x in AuditLog(wd / "audit.jsonl").records()]
    assert events[0] == "run_started" and events[-1] == "run_finished"


def test_bad_schema_and_bad_metric_fail_cleanly(write, run):
    bad_schema = write("bs.json", json.dumps({"columns": [{"name": "x", "type": "nope"}]}))
    assert run(write("s.csv", CLEAN_CSV), schema=bad_schema).status == "FAILED"
    bad_metric = write("bm.json", json.dumps({"metrics": [{"name": "m", "sql": "DROP TABLE data"}]}))
    assert run(write("s2.csv", CLEAN_CSV), analysis=bad_metric).status == "FAILED"


# ---------------------------------------------------------------- privacy & output safety
@pytest.mark.parametrize("policy", ["business", "regulated"])
def test_pii_never_leaks_into_any_output_file(write, run, wd, policy):
    run(write("d.csv", DIRTY), policy)
    run(write("s.csv", CLEAN_CSV), policy)
    blob = all_text(wd)
    for email in ("anna@", "ben@", "ben2@", "cara@", "dan@", "eva@", "finn@", "gia@", "hugo@", "cmd|"):
        assert email not in blob


def test_low_tier_does_not_mask(write, run, wd):
    run(write("d.csv", DIRTY), "low")
    assert "ben@example.com" in all_text(wd)


def test_quarantine_export_neutralises_formula_injection(write, run):
    head = "order_id,customer_email,region,amount,order_date,paid\n"
    r = run(write("d.csv", head + "1,a@x.io,=HYPERLINK(1),1.00,2026-01-01,true\n2,a@x.io,EU,1.00,2026-01-01,true\n"), "low")
    q = (r.run_dir / "quarantine.csv").read_text()
    assert "'=HYPERLINK(1)" in q and ",=HYPERLINK" not in q


SECRET = "SECRET-VALUE-XYZ"


@pytest.mark.parametrize("name,content", [
    ("d.json", '[{"order_id": 1, "region": "%s"}, {bad json' % SECRET),
    ("d.sql", "CREATE TABLE t(a); INSERT INTO t VALUES(%s;" % SECRET),
    ("d.sql", "CREATE TABLE t(a); INSERT INTO t VALUES(%s);" % SECRET),      # 'no such column: SECRET'
    ("d.csv", 'order_id,region\n1,"%s\n' % SECRET),
])
def test_failure_messages_never_echo_data_values(write, run, wd, name, content):
    r = run(write(name, content), "regulated")
    assert r.status == "FAILED"
    assert SECRET.split("-")[0] not in all_text(wd)


# ---------------------------------------------------------------- sign-off
def test_signoff_flow_four_eyes_and_single_use(write, run, wd):
    r = run(write("s.csv", CLEAN_CSV), "regulated", actor="alice")
    with pytest.raises(DataPipeError):
        signoff(wd, r.run_id, "alice")                    # cannot approve own run
    out = signoff(wd, r.run_id, "bob", "looks right")
    assert out["results_sha256"] == r.document["results_sha256"]
    with pytest.raises(DataPipeError):
        signoff(wd, r.run_id, "carol")                    # already signed
    recs = AuditLog(wd / "audit.jsonl").records()
    assert recs[-1]["event"] == "signoff" and recs[-1]["actor"] == "bob"


def test_signoff_refuses_tampered_results_and_wrong_status_and_bad_ids(write, run, wd):
    r = run(write("s.csv", CLEAN_CSV), "regulated", actor="alice")
    f = r.run_dir / "result.json"
    doc = json.loads(f.read_text())
    doc["results"]["metrics"]["total_amount"]["rows"] = [["999999.00"]]
    f.write_text(json.dumps(doc))
    with pytest.raises(DataPipeError, match="hash mismatch"):
        signoff(wd, r.run_id, "bob")
    low = run(write("s2.csv", CLEAN_CSV), "low")
    with pytest.raises(DataPipeError):
        signoff(wd, low.run_id, "bob")
    for bad in ("../../etc", "nope", ""):
        with pytest.raises(DataPipeError):
            signoff(wd, bad, "bob")


def test_full_audit_trail_is_chained_and_free_of_data_values(write, run, wd):
    run(write("d.csv", DIRTY), "low")
    ok, n, _ = AuditLog(wd / "audit.jsonl").verify()
    assert ok and n >= 5
    assert "example.com" not in (wd / "audit.jsonl").read_text()


# ---------------------------------------------------------------- CLI
def test_cli_exit_codes_and_commands(write, wd, capsys):
    base = ["--workdir", str(wd)]
    clean, dirty = write("s.csv", CLEAN_CSV), write("d.csv", DIRTY)
    common = ["--schema", str(SCHEMA), "--actor", "alice"]
    assert main(base + ["run", str(clean), "--policy", "low"] + common) == 0
    assert main(base + ["run", str(dirty), "--policy", "regulated"] + common) == 2
    assert main(base + ["run", str(clean), "--policy", "business", "--actor", "alice"]) == 3
    assert main(base + ["run", str(write("x.dat", "zzz")), "--policy", "low"]) == 1
    assert main(base + ["verify-audit"]) == 0
    capsys.readouterr()
    assert main(base + ["infer", str(clean)]) == 0
    assert json.loads(capsys.readouterr().out)["columns"][0]["name"] == "order_id"
    assert main(base + ["policies"]) == 0


def test_cli_multiple_files_report_worst_exit_code(write, wd):
    code = main(["--workdir", str(wd), "run", str(write("a.csv", CLEAN_CSV)), str(write("b.csv", DIRTY)),
                 "--policy", "regulated", "--schema", str(SCHEMA), "--actor", "alice"])
    assert code == 2


def test_cli_signoff_command(write, wd, capsys):
    base = ["--workdir", str(wd)]
    main(base + ["run", str(write("s.csv", CLEAN_CSV)), "--policy", "regulated", "--schema", str(SCHEMA), "--actor", "alice"])
    run_id = next((wd / "runs").iterdir()).name
    assert main(base + ["signoff", run_id, "--reviewer", "alice"]) == 1
    assert main(base + ["signoff", run_id, "--reviewer", "bob"]) == 0
