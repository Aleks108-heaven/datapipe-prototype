"""report.md says what its numbers rest on: how many rows they cover, what was cross-checked and what was not, under which limits."""
import json

import pytest

from conftest import CLEAN_CSV, EX
from datapipe.pipeline import _scope_lines

DIRTY = (EX / "sales_dirty.csv").read_text()


def report(res):
    return (res.run_dir / "report.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("stream", ["never", "always"])
def test_a_run_that_set_most_rows_aside_says_so_next_to_its_numbers(stream, write, run):
    res = run(write("d.csv", DIRTY), "low", stream=stream)
    text = report(res)
    assert "## Checks and limits" in text
    assert "Coverage: the metrics use 2 of 10 rows (20.0%); the 8 quarantined rows are not in them." in text
    assert "Look at quarantine.csv before relying on these numbers" in text
    assert "comparisons, 0 disagreements" in text
    assert "Policy low:" in text and "set aside in quarantine.csv" in text and "personal data is not masked" in text
    assert f"datapipe {res.document['engine']['datapipe']} with DuckDB" in text and res.document["audit_head"][:12] in text


@pytest.mark.parametrize("stream", ["never", "always"])
def test_a_clean_run_says_every_row_is_in_the_numbers_and_gives_no_warning(stream, write, run):
    text = report(run(write("s.csv", CLEAN_CSV), "low", stream=stream))
    assert "Coverage: all 8 rows passed the checks and are in the metrics." in text
    assert "Look at quarantine.csv" not in text


def test_a_few_set_aside_rows_are_counted_without_the_warning(write, run):
    head = "order_id,customer_email,region,amount,order_date,paid\n"
    rows = "".join(f"{i},u{i}@x.io,EU,1.00,2026-01-01,true\n" for i in range(1, 100)) + "100,u@x.io,EU,-1.00,2026-01-01,true\n"
    text = report(run(write("f.csv", head + rows), "low"))
    assert "Coverage: the metrics use 99 of 100 rows (99.0%); the 1 quarantined rows are not in them." in text
    assert "Look at quarantine.csv" not in text


def test_a_stricter_policy_is_described_as_it_is(write, run):
    text = report(run(write("s.csv", CLEAN_CSV), "business", accept_inferred=True))
    assert "Policy business:" in text and "more than 5% of the rows fail" in text and "personal data is masked" in text
    text = report(run(write("t.csv", DIRTY), "regulated"))
    assert "Policy regulated:" in text and "is refused" in text


def test_a_blocked_run_still_states_its_policy_and_makes_no_claim_about_coverage(write, run):
    res = run(write("e.csv", "a,b\n"), "low")
    text = report(res)
    assert "## Checks and limits" in text and "Coverage" not in text and "Policy low:" in text


def test_statistics_that_could_not_be_cross_checked_are_named_not_hidden():
    doc = {"counts": {"rows_total": 5, "valid": 5, "quarantined": 0}, "policy": {"name": "low", "row_errors": "quarantine", "max_error_rate": 1.0,
                                                                           "mask_pii": False, "max_file_bytes": 300 * 1024 ** 2, "max_memory_bytes": 6 * 1024 ** 3},
           "results": {"profile": {}, "reconciliation": {"checks": 7, "mismatches": [], "not_checked": [{"column": "email", "statistic": "distinct"},
                                                                                                          {"column": "id", "statistic": "distinct"}]}},
           "engine": {"datapipe": "9.9.9", "duckdb": "1.2.3"}}
    text = "\n".join(_scope_lines(doc))
    assert "Not cross-checked (too many different values to track): distinct of email, distinct of id. The other statistics were." in text
    assert "7 comparisons, 0 disagreements" in text and "datapipe 9.9.9 with DuckDB 1.2.3." in text and "audit record" not in text
    doc["results"] = {"profile": None, "reconciliation": {"checks": 0, "mismatches": []}}
    assert "were not requested by the analysis file" in "\n".join(_scope_lines(doc))


def test_the_report_is_still_plain_text_the_result_file_does_not_change_shape(write, run):
    res = run(write("s.csv", CLEAN_CSV), "low")
    doc = json.loads((res.run_dir / "result.json").read_text(encoding="utf-8"))
    assert "Checks and limits" not in json.dumps(doc) and doc["results_sha256"] == res.document["results_sha256"]
