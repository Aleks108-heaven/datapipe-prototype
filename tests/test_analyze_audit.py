import json
import random
import threading
from decimal import Decimal

import pytest

from conftest import SCHEMA, ANALYSIS
from datapipe import analyze
from datapipe.analyze import AnalysisSpec, Metric, load_analysis, run_analysis, build_engine, profile_and_reconcile
from datapipe.audit import AuditLog
from datapipe.errors import AnalysisError
from datapipe.ingest import parse_csv
from datapipe.policy import get_policy
from datapipe.schema import load_schema
from datapipe.validate import validate

HEAD = "order_id,customer_email,region,amount,order_date,paid\n"


def prepared(rows_text, policy="low"):
    schema, pol = load_schema(SCHEMA), get_policy(policy)
    vr = validate(parse_csv(HEAD + rows_text), schema, pol)
    assert not vr.quarantined
    return schema, vr.valid_rows, pol


def metric(sql):
    return AnalysisSpec([Metric("m", sql)], profile=False)


def test_golden_totals_are_exact_decimals():
    schema, rows, pol = prepared("1,a@x.io,EU,0.10,2026-01-01,true\n2,b@x.io,EU,0.20,2026-01-02,true\n")
    res, recon = run_analysis(schema, rows, metric("SELECT SUM(amount) FROM data"), pol)
    assert Decimal(res["metrics"]["m"]["rows"][0][0]) == Decimal("0.30")     # float would give 0.30000000000000004
    assert recon["checks"] == 0


def test_profile_reconciles_with_independent_python_calculation():
    schema, rows, pol = prepared("1,a@x.io,EU,5.00,2026-01-01,true\n2,b@x.io,US,7.25,2026-02-01,false\n")
    res, recon = run_analysis(schema, rows, load_analysis(ANALYSIS), pol)
    assert recon["checks"] > 10 and recon["mismatches"] == []
    assert res["profile"]["amount"]["sum"] == "12.25"
    assert res["profile"]["order_date"]["max"] == "2026-02-01"


def test_reconciliation_detects_engine_disagreement():
    schema, rows, pol = prepared("1,a@x.io,EU,5.00,2026-01-01,true\n2,b@x.io,US,7.25,2026-02-01,false\n")
    con, _ = build_engine(schema, rows, pol)
    con.execute("DELETE FROM data WHERE _row = 1")            # simulate a silent engine/data-load fault
    _, recon = profile_and_reconcile(con, schema, rows, pol)
    assert recon["mismatches"]


@pytest.mark.parametrize("sql", [
    "DROP TABLE data",
    "DELETE FROM data",
    "SELECT 1; SELECT 2",
    "INSERT INTO data VALUES (1)",
    "CREATE TABLE x AS SELECT 1",
    "COPY data TO '/tmp/datapipe_out.csv'",
    "ATTACH '/tmp/datapipe_x.db'",
    "PRAGMA database_list",
    "SELEC oops",
])
def test_metric_sql_must_be_a_single_select(sql):
    schema, rows, pol = prepared("1,a@x.io,EU,5.00,2026-01-01,true\n")
    with pytest.raises(AnalysisError):
        run_analysis(schema, rows, metric(sql), pol)
    import os
    assert not os.path.exists("/tmp/datapipe_out.csv") and not os.path.exists("/tmp/datapipe_x.db")


@pytest.mark.parametrize("sql", [
    "SELECT * FROM read_csv('/etc/passwd')",
    "SELECT * FROM read_text('/etc/hostname')",
    "SELECT * FROM glob('/*')",
])
def test_metric_cannot_read_the_filesystem(sql):
    schema, rows, pol = prepared("1,a@x.io,EU,5.00,2026-01-01,true\n")
    with pytest.raises(AnalysisError):
        run_analysis(schema, rows, metric(sql), pol)


def test_pii_columns_are_not_loaded_when_policy_masks_pii():
    schema, rows, _ = prepared("1,a@x.io,EU,5.00,2026-01-01,true\n")
    q = metric("SELECT customer_email FROM data")
    res, _ = run_analysis(schema, rows, q, get_policy("low"))
    assert res["metrics"]["m"]["rows"] == [["a@x.io"]]
    with pytest.raises(AnalysisError):
        run_analysis(schema, rows, q, get_policy("business"))
    with pytest.raises(AnalysisError):
        run_analysis(schema, rows, q, get_policy("regulated"))


def test_pii_profile_is_limited_under_masking():
    schema, rows, _ = prepared("1,a@x.io,EU,5.00,2026-01-01,true\n")
    res, recon = run_analysis(schema, rows, AnalysisSpec([]), get_policy("regulated"))
    assert set(res["profile"]["customer_email"]) <= {"rows", "nulls", "distinct", "note"}
    assert recon["mismatches"] == []


def test_result_row_cap(monkeypatch):
    monkeypatch.setattr(analyze, "MAX_RESULT_ROWS", 3)
    schema, rows, pol = prepared("".join(f"{i},a@x.io,EU,1,2026-01-01,true\n" for i in range(1, 6)))
    with pytest.raises(AnalysisError):
        run_analysis(schema, rows, metric("SELECT * FROM data"), pol)


def test_unordered_metric_output_is_canonically_sorted():
    schema, rows, pol = prepared("2,a@x.io,US,1,2026-01-01,true\n1,b@x.io,EU,1,2026-01-01,true\n")
    res, _ = run_analysis(schema, rows, metric("SELECT region FROM data"), pol)
    assert res["metrics"]["m"]["rows"] == [["EU"], ["US"]]


def test_property_group_totals_equal_grand_total_equal_python_sum():
    rnd = random.Random(1234)
    lines, expected = [], Decimal(0)
    for i in range(1, 501):
        cents = rnd.randint(0, 10_000_00)
        expected += Decimal(cents) / 100
        lines.append(f"{i},u{i}@x.io,{rnd.choice(['EU', 'US', 'APAC'])},{cents // 100}.{cents % 100:02d},2026-01-01,true\n")
    schema, rows, pol = prepared("".join(lines))
    res, recon = run_analysis(schema, rows, load_analysis(ANALYSIS), pol)
    total = Decimal(res["metrics"]["total_amount"]["rows"][0][0])
    groups = sum(Decimal(r[1]) for r in res["metrics"]["by_region"]["rows"])
    assert total == groups == expected and recon["mismatches"] == []


def test_analysis_spec_validation(tmp_path):
    for doc in [{"metricz": []}, {"metrics": [{"name": "a"}]},
                {"metrics": [{"name": "a", "sql": "SELECT 1"}, {"name": "a", "sql": "SELECT 2"}]}]:
        p = tmp_path / "a.json"
        p.write_text(json.dumps(doc))
        with pytest.raises(AnalysisError):
            load_analysis(p)


# ---------------------------------------------------------------- audit log
def make_log(tmp_path, n=5):
    log = AuditLog(tmp_path / "a" / "audit.jsonl")
    for i in range(n):
        log.append("evt", f"r{i}", {"i": i}, actor="t")
    return log


def test_audit_chain_verifies(tmp_path):
    ok, n, _ = make_log(tmp_path).verify()
    assert ok and n == 5


def test_audit_detects_modification_deletion_reorder_and_garbage(tmp_path):
    log = make_log(tmp_path)
    lines = log.path.read_text().splitlines()

    log.path.write_text("\n".join(lines[:2] + [lines[2].replace('"i":2', '"i":9')] + lines[3:]) + "\n")
    assert not log.verify()[0]
    log.path.write_text("\n".join(lines[:2] + lines[3:]) + "\n")                # middle record deleted
    assert not log.verify()[0]
    log.path.write_text("\n".join([lines[1], lines[0]] + lines[2:]) + "\n")     # reordered
    assert not log.verify()[0]
    log.path.write_text("\n".join(lines) + "\nnot json\n")
    assert not log.verify()[0]
    log.path.write_text("\n".join(lines) + "\n")
    assert log.verify()[0]


def test_audit_tail_truncation_is_not_detectable_from_file_alone(tmp_path):
    """Documented limitation: this is why the head hash must be anchored outside the file."""
    log = make_log(tmp_path)
    head = log.head()
    log.path.write_text("\n".join(log.path.read_text().splitlines()[:-1]) + "\n")
    assert log.verify()[0] and log.head() != head


def test_audit_concurrent_appends_keep_chain_valid(tmp_path):
    path = tmp_path / "audit.jsonl"

    def worker(k):
        log = AuditLog(path)
        for i in range(10):
            log.append("evt", f"w{k}", {"i": i}, actor="t")

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    ok, n, _ = AuditLog(path).verify()
    assert ok and n == 80
