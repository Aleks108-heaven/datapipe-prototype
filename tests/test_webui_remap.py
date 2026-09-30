"""Manual remapping: service, HTTP and pipeline-level tests."""
import json
import os
import shutil

import pytest

from conftest import ANALYSIS, EX, SCHEMA
from datapipe import ingest
from datapipe.audit import AuditLog
from datapipe.ingest import read_source
from datapipe.pipeline import run_pipeline
from datapipe.schema import schema_from_dict
from datapipe.webui import make_server
from datapipe.webui.service import ApiError, ReviewService
from helpers import GOOD, Scripted, m, make_proposal, try_symlink

NO_PAID = GOOD[:5]                                   # 'paid' left unmapped by the provider
CSV = "sales_renamed.csv"


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    shutil.copy(EX / CSV, d / CSV)
    return d


def svc(wd, data, **kw):
    return ReviewService(wd, data_dirs=[data], **kw)


def fail(status, fn, *a, **kw):
    with pytest.raises(ApiError) as e:
        fn(*a, **kw)
    assert e.value.status == status, e.value.message
    return e.value.message


# ---------------------------------------------------------------- finding the source file
def test_availability_and_columns(wd, data):
    prop, _ = make_proposal(wd, NO_PAID)
    info = svc(wd, data).get(prop["proposal_sha256"])["manual_remap"]
    assert info["available"] is True and info["columns"][0] == "Order No" and len(info["columns"]) == 6


def test_unavailable_without_data_dir_or_file(wd, data, tmp_path):
    prop, _ = make_proposal(wd, NO_PAID)
    pid = prop["proposal_sha256"]
    none = ReviewService(wd).get(pid)["manual_remap"]
    assert none["available"] is False and "--data-dir" in none["reason"]
    empty = tmp_path / "empty"
    empty.mkdir()
    gone = ReviewService(wd, data_dirs=[empty]).get(pid)["manual_remap"]
    assert gone["available"] is False and "not found" in gone["reason"]


def test_a_file_with_the_same_name_but_different_content_is_never_used(wd, data):
    prop, _ = make_proposal(wd, NO_PAID)
    (data / CSV).write_text((data / CSV).read_text().replace("120.50", "999.99"))
    s = svc(wd, data)
    assert "differs" in s.get(prop["proposal_sha256"])["manual_remap"]["reason"]
    assert "differs" in fail(409, s.check_manual, prop["proposal_sha256"], {"target": "paid", "source": "Paid?"})


def test_symlinks_and_traversal_names_are_refused(wd, data, tmp_path):
    prop, _ = make_proposal(wd, NO_PAID)
    real = tmp_path / "elsewhere.csv"
    shutil.move(str(data / CSV), real)
    if try_symlink(real, data / CSV):
        assert svc(wd, data).get(prop["proposal_sha256"])["manual_remap"]["available"] is False
    else:                                             # Windows without symlink privilege: keep testing the traversal half
        shutil.move(str(real), data / CSV)
    # a proposal whose recorded source name tries to leave the data folder
    shutil.copy(EX / CSV, tmp_path / "outside.csv")
    evil, _ = make_proposal(wd, GOOD[:4], name="../outside.csv")
    info = svc(wd, data).get(evil["proposal_sha256"])["manual_remap"]
    assert info["available"] is False and "usable source file" in info["reason"]


def test_unreadable_source_reports_a_reason(wd, data):
    dump = "CREATE TABLE a(x); CREATE TABLE b(y); INSERT INTO a VALUES(1); INSERT INTO b VALUES(2);"
    (data / "two.sql").write_text(dump)
    tbl = read_source(data / "two.sql", max_bytes=10 ** 8, table="a")
    good, _ = make_proposal(wd, [m("x", "order_id")], tbl=tbl, name="two.sql", read_options={"fmt": "sql", "table": "a"})
    assert svc(wd, data).get(good["proposal_sha256"])["manual_remap"]["available"] is True     # read options honoured
    bad, _ = make_proposal(wd, [m("x", "order_id")], tbl=tbl, name="two.sql")                   # no table recorded
    assert "could not be read" in svc(wd, data).get(bad["proposal_sha256"])["manual_remap"]["reason"]


def test_parsed_table_is_cached_until_the_file_changes(wd, data, monkeypatch):
    prop, _ = make_proposal(wd, NO_PAID)
    pid, calls = prop["proposal_sha256"], []
    real = ingest.read_source

    def counting(*a, **kw):
        calls.append(1)
        return real(*a, **kw)
    monkeypatch.setattr("datapipe.webui.service.read_source", counting)
    s = svc(wd, data)
    for _ in range(3):
        s.check_manual(pid, {"target": "paid", "source": "Paid?"})
    assert len(calls) == 1


# ---------------------------------------------------------------- checking a candidate
def test_check_returns_statistics_not_values(wd, data):
    prop, _ = make_proposal(wd, NO_PAID)
    res = svc(wd, data).check_manual(prop["proposal_sha256"], {"target": "paid", "source": "Paid?"})
    ev = res["evidence"]
    assert ev["parse_rate"] == 1.0 and ev["non_null"] == 8 and ev["name_score"] == 1.0 and ev["warnings"] == []
    assert "true" not in json.dumps(res).lower().replace('"parse_rate"', "")     # no raw cell values in the response


@pytest.mark.parametrize("payload,needle", [
    ({"target": "paid", "source": "Area"}, "only 0%"),                    # values do not fit a boolean
    ({"target": "amount", "source": "Buyer Email"}, "only 0%"),
    ({"target": "nope", "source": "Paid?"}, "target column"),
    ({"target": "paid", "source": "Nope"}, "does not exist"),
])
def test_check_refuses_what_the_data_contradicts(wd, data, payload, needle):
    prop, _ = make_proposal(wd, NO_PAID)
    assert needle in fail(400, svc(wd, data).check_manual, prop["proposal_sha256"], payload)


@pytest.mark.parametrize("payload", [None, {}, {"target": "paid"}, {"source": "Paid?"}, {"target": 1, "source": "x"}, []])
def test_check_validates_its_payload(wd, data, payload):
    prop, _ = make_proposal(wd, NO_PAID)
    fail(400, svc(wd, data).check_manual, prop["proposal_sha256"], payload)


def custom(wd, data, csv_text, mappings, name="c.csv"):
    (data / name).write_text(csv_text)
    tbl = read_source(data / name, max_bytes=10 ** 8)
    return make_proposal(wd, mappings, tbl=tbl, name=name)[0]["proposal_sha256"]


def test_empty_column_cannot_be_verified(wd, data):
    pid = custom(wd, data, "Order No,Empty\n1,\n2,\n", [m("Order No", "order_id")])
    assert "no values" in fail(400, svc(wd, data).check_manual, pid, {"target": "amount", "source": "Empty"})


def test_warnings_for_duplicates_in_a_unique_target_and_unrelated_names(wd, data):
    pid = custom(wd, data, "Thing,Area\n1,EU\n1,US\n", [m("Area", "region")])
    ev = svc(wd, data).check_manual(pid, {"target": "order_id", "source": "Thing"})["evidence"]
    assert any("unique" in w for w in ev["warnings"]) and any("names do not look alike" in w for w in ev["warnings"])


def test_parse_rate_boundary_for_manual_mappings(wd, data):
    body = lambda bad: "Thing\n" + "1.00\n" * (50 - bad) + "oops\n" * bad
    ok = custom(wd, data, body(1), [], "a.csv")                                  # 49/50 = 98% -> allowed
    no = custom(wd, data, body(2), [], "b.csv")                                  # 48/50 = 96% -> refused
    assert svc(wd, data).check_manual(ok, {"target": "amount", "source": "Thing"})["evidence"]["parse_rate"] == 0.98
    fail(400, svc(wd, data).check_manual, no, {"target": "amount", "source": "Thing"})


def test_check_is_refused_once_the_proposal_is_decided(wd, data):
    prop, _ = make_proposal(wd, NO_PAID)
    pid, s = prop["proposal_sha256"], svc(wd, data)
    s.reject(pid, {"reviewer": "bob", "note": "no"})
    assert "already rejected" in fail(409, s.check_manual, pid, {"target": "paid", "source": "Paid?"})


# ---------------------------------------------------------------- approving with manual mappings
def approve(s, pid, manual, **kw):
    return s.approve(pid, {"reviewer": kw.pop("reviewer", "bob"), "note": kw.pop("note", "mapped by hand"),
                           "manual": manual, **kw})


def test_manual_mapping_is_recorded_and_produces_identical_results_to_the_original_file(wd, data):
    prop, _ = make_proposal(wd, NO_PAID)
    pid, s = prop["proposal_sha256"], svc(wd, data)
    assert "required target columns are not mapped" in fail(400, s.approve, pid, {"reviewer": "bob"})
    res = approve(s, pid, [{"target": "paid", "source": "Paid?"}])
    prov = res["schema"]["provenance"]
    assert {c["name"]: c.get("source") for c in res["schema"]["columns"]}["paid"] == "Paid?"
    mm = prov["manual_mappings"][0]
    assert mm["target"] == "paid" and mm["source"] == "Paid?" and mm["superseded_source"] is None
    assert mm["evidence"]["parse_rate"] == 1.0 and "paid" in prov["mapped_targets"]
    ev = AuditLog(wd / "audit.jsonl").records()[-1]
    assert ev["event"] == "mapping_approved" and ev["data"]["manual_mappings"][0]["target"] == "paid"

    run = run_pipeline(data / CSV, workdir=wd, policy_name="regulated", schema_path=wd / res["schema_file"],
                       analysis_path=ANALYSIS, actor="alice")
    base = run_pipeline(EX / "sales.csv", workdir=wd, policy_name="regulated", schema_path=SCHEMA,
                        analysis_path=ANALYSIS, actor="alice")
    assert run.status == "PENDING_SIGNOFF" and run.document["results_sha256"] == base.document["results_sha256"]
    assert run.document["schema"]["provenance"]["manual_mappings"][0]["target"] == "paid"


def test_a_note_is_required_for_manual_mappings(wd, data):
    prop, _ = make_proposal(wd, NO_PAID)
    assert "note is required" in fail(400, svc(wd, data).approve, prop["proposal_sha256"],
                                      {"reviewer": "bob", "manual": [{"target": "paid", "source": "Paid?"}]})


def test_manual_mapping_supersedes_a_proposed_one_and_records_what_it_replaced(wd, data):
    prop, _ = make_proposal(wd, GOOD)
    pid, s = prop["proposal_sha256"], svc(wd, data)
    # 'Buyer Email' is already used by the proposed customer_email mapping -> must be freed first
    msg = fail(400, approve, s, pid, [{"target": "region", "source": "Buyer Email"}])
    assert "already used" in msg and "customer_email" in msg
    res = approve(s, pid, [{"target": "region", "source": "Buyer Email"}], exclude=["customer_email"])
    mm = res["schema"]["provenance"]["manual_mappings"][0]
    assert mm["superseded_source"] == "Area" and res["schema"]["provenance"]["excluded_accepted"] == ["customer_email"]
    cols = {c["name"]: c.get("source") for c in res["schema"]["columns"]}
    assert cols["region"] == "Buyer Email" and cols["customer_email"] is None


def test_manual_mapping_to_the_same_source_as_proposed_is_allowed(wd, data):
    prop, _ = make_proposal(wd, GOOD)
    res = approve(svc(wd, data), prop["proposal_sha256"], [{"target": "amount", "source": "Total (EUR)"}])
    assert res["schema"]["provenance"]["manual_mappings"][0]["superseded_source"] == "Total (EUR)"


@pytest.mark.parametrize("manual,needle", [
    ([{"target": "paid", "source": "Paid?"}, {"target": "paid", "source": "Paid?"}], "more than once"),
    ([{"target": "paid", "source": "Paid?"}, {"target": "customer_email", "source": "Paid?"}], "more than one"),
    ([{"target": "nope", "source": "Paid?"}], "not in the schema"),
    ([{"target": "paid", "source": "Nope"}], "not in the file"),
    ([{"target": "paid", "source": "Area"}], "only 0%"),
    ([{"target": "paid"}], "target and a source"),
    ("paid", "list"),
])
def test_invalid_manual_lists_are_refused(wd, data, manual, needle):
    prop, _ = make_proposal(wd, NO_PAID)
    msg = fail(400, approve, svc(wd, data), prop["proposal_sha256"], manual)
    assert needle in msg
    assert not (wd / "schemas").exists()


def test_evidence_claimed_by_the_browser_is_ignored(wd, data):
    prop, _ = make_proposal(wd, NO_PAID)
    forged = [{"target": "paid", "source": "Area", "evidence": {"parse_rate": 1.0, "non_null": 8, "warnings": []}}]
    assert "only 0%" in fail(400, approve, svc(wd, data), prop["proposal_sha256"], forged)


def test_manual_mappings_are_reverified_at_approval_if_the_file_changed_or_vanished(wd, data):
    prop, _ = make_proposal(wd, NO_PAID)
    pid, s = prop["proposal_sha256"], svc(wd, data)
    s.check_manual(pid, {"target": "paid", "source": "Paid?"})
    (data / CSV).write_text((data / CSV).read_text() + "9,z@x.io,EU,1.00,2026-02-01,true\n")
    assert "differs" in fail(409, approve, s, pid, [{"target": "paid", "source": "Paid?"}])
    (data / CSV).unlink()
    assert "not found" in fail(409, approve, s, pid, [{"target": "paid", "source": "Paid?"}])
    assert s.get(pid)["state"]["state"] == "pending"


def test_manual_mappings_cannot_be_approved_without_a_data_folder(wd):
    prop, _ = make_proposal(wd, NO_PAID)
    assert "--data-dir" in fail(409, approve, ReviewService(wd), prop["proposal_sha256"],
                                [{"target": "paid", "source": "Paid?"}])


def test_four_eyes_still_applies_to_manual_approvals(wd, data):
    prop, _ = make_proposal(wd, NO_PAID, actor="alice")
    assert "four-eyes" in fail(400, approve, svc(wd, data), prop["proposal_sha256"],
                               [{"target": "paid", "source": "Paid?"}], reviewer="alice")


# ---------------------------------------------------------------- exact column names (regression)
def test_long_and_control_character_headers_survive_the_whole_flow(wd, data):
    import csv, io
    header = "Order number of the customer purchase " + "x" * 100 + "\tend"
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([header, "Label"])
    w.writerows([["1", "a"], ["2", "b"], ["3", "c"]])
    (data / "long.csv").write_text(buf.getvalue())
    target = schema_from_dict({"name": "t", "columns": [{"name": "k", "type": "integer", "required": True, "unique": True},
                                                        {"name": "label", "type": "string"}]})
    tbl = read_source(data / "long.csv", max_bytes=10 ** 8)
    from datapipe.mapping import clean_name
    prop, _ = make_proposal(wd, [m(clean_name(header), "k")], tbl=tbl, name="long.csv", target=target)
    assert prop["items"][0]["status"] == "accepted" and prop["items"][0]["source"] == header     # exact, not cleaned
    s = svc(wd, data)
    res = approve(s, prop["proposal_sha256"], [{"target": "label", "source": "Label"}])
    run = run_pipeline(data / "long.csv", workdir=wd, policy_name="low", schema_path=wd / res["schema_file"], actor="alice")
    # (a tab inside the header makes the delimiter auto-detection warn: that is correct behaviour)
    assert run.status == "COMPLETED_WITH_WARNINGS" and run.document["counts"] == {
        "rows_total": 3, "valid": 3, "quarantined": 0, "issues_by_rule": {}}


# ---------------------------------------------------------------- over HTTP
from test_webui_http import Client, TOKEN  # noqa: E402


@pytest.fixture
def srv(wd, data):
    import threading
    prop, _ = make_proposal(wd, NO_PAID)
    server = make_server(wd, port=0, token=TOKEN, data_dirs=[data])
    threading.Thread(target=lambda: server.serve_forever(0.05), daemon=True).start()
    server.pid = prop["proposal_sha256"]
    yield server
    server.shutdown()
    server.server_close()


def test_check_endpoint_is_protected_like_every_other_write(srv):
    path = f"/api/proposals/{srv.pid}/check"
    body = {"target": "paid", "source": "Paid?"}
    assert Client(srv).post(path, body)[0] == 401
    c = Client(srv).login()
    assert c.post(path, body, csrf=False)[0] == 403
    assert c.post(path, body, csrf="wrong")[0] == 403
    assert c.post(path, body, headers={"Origin": "http://evil.example"})[0] == 403
    assert c.post(path, body, headers={"Content-Type": "text/plain"})[0] == 415
    c.host = f"evil.example:{srv.port}"
    assert c.post(path, body)[0] == 421


def test_check_endpoint_roundtrip_and_no_data_leak(srv):
    c = Client(srv).login()
    status, _, raw = c.post(f"/api/proposals/{srv.pid}/check", {"target": "paid", "source": "Paid?"})
    assert status == 200 and json.loads(raw)["evidence"]["parse_rate"] == 1.0
    for secret in (b"anna@example.com", b"120.50", b"2026-01-05"):
        assert secret not in raw
    status, _, raw = c.post(f"/api/proposals/{srv.pid}/check", {"target": "paid", "source": "Area"})
    assert status == 400 and b"only 0%" in raw
    assert c.post(f"/api/proposals/{srv.pid}/check", {"target": "paid"})[0] == 400


def test_detail_endpoint_reports_availability(srv):
    c = Client(srv).login()
    _, _, raw = c.req("GET", f"/api/proposals/{srv.pid}")
    assert json.loads(raw)["manual_remap"]["available"] is True


_ = Scripted
