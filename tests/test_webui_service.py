import json
import os
import threading

import pytest

from datapipe.audit import AuditLog
from datapipe.ingest import parse_csv
from datapipe.webui.service import ApiError, ReviewService
from helpers import GOOD, Scripted, m, make_proposal, try_symlink

PID_LEN = 64
NEEDS_REVIEW = GOOD[:4] + [m("Ordered On", "order_date", 0.6), m("Paid?", "paid")]      # order_date needs review


def svc(wd, **kw):
    return ReviewService(wd, **kw)


def fail(status, fn, *a, **kw):
    with pytest.raises(ApiError) as e:
        fn(*a, **kw)
    assert e.value.status == status, e.value.message
    return e.value.message


# ---------------------------------------------------------------- reading
def test_list_and_get(wd):
    prop, _ = make_proposal(wd)
    s = svc(wd)
    row = s.list_proposals()["proposals"][0]
    assert row["id"] == prop["proposal_sha256"] and row["state"] == "pending" and row["integrity_ok"] is True
    assert row["summary"]["accepted"] == 6 and row["required_unmapped"] == 0
    assert s.get(prop["proposal_sha256"])["proposal"]["actor"] == "alice"


def test_bad_files_are_skipped_not_fatal(wd, tmp_path):
    make_proposal(wd)
    d = wd / "mappings"
    (d / "broken.json").write_text("{not json")
    (d / "other.json").write_text(json.dumps({"hello": "world"}))
    (d / "huge.json").write_text("[" + "0," * 3_000_000 + "0]")
    outside = tmp_path / "secret.json"
    outside.write_text("{}")
    try_symlink(outside, d / "link.json")            # where symlinks are not permitted (Windows) this case is simply absent
    out = svc(wd).list_proposals()
    assert len(out["proposals"]) == 1
    assert {x["file"] for x in out["skipped"]} == {"broken.json", "other.json", "huge.json"}


def test_get_rejects_bad_or_unknown_ids(wd):
    make_proposal(wd)
    s = svc(wd)
    for bad in ("../../etc/passwd", "zz", "", "a" * 64, "A" * 64, None):
        fail(404, s.get, bad)


def test_tampered_proposal_is_flagged_and_cannot_be_approved(wd):
    prop, path = make_proposal(wd)
    doc = json.loads(path.read_text())
    doc["items"][0]["source"] = "Paid?"
    path.write_text(json.dumps(doc))
    s = svc(wd)
    assert s.list_proposals()["proposals"][0]["integrity_ok"] is False
    assert s.get(prop["proposal_sha256"])["integrity_ok"] is False
    assert "hash mismatch" in fail(400, s.approve, prop["proposal_sha256"], {"reviewer": "bob"})
    assert "hash mismatch" in fail(400, s.reject, prop["proposal_sha256"], {"reviewer": "bob", "note": "x"})


# ---------------------------------------------------------------- approving
def test_approve_writes_schema_audits_and_locks_the_proposal(wd):
    prop, _ = make_proposal(wd)
    pid = prop["proposal_sha256"]
    s = svc(wd)
    res = s.approve(pid, {"reviewer": "bob"})
    assert res["schema_file"].startswith("schemas/") and (wd / res["schema_file"]).is_file()
    saved = json.loads((wd / res["schema_file"]).read_text())
    assert saved["provenance"]["approved_by"] == "bob"
    assert {c["name"]: c.get("source") for c in saved["columns"]}["amount"] == "Total (EUR)"
    ev = AuditLog(wd / "audit.jsonl").records()[-1]
    assert ev["event"] == "mapping_approved" and ev["actor"] == "bob" and ev["data"]["schema_file"] == res["schema_file"]
    assert s.get(pid)["state"]["state"] == "approved" and AuditLog(wd / "audit.jsonl").verify()[0]
    assert "already approved" in fail(409, s.approve, pid, {"reviewer": "carol"})
    assert "already approved" in fail(409, s.reject, pid, {"reviewer": "carol", "note": "no"})


def test_four_eyes_enforced_server_side(wd):
    prop, _ = make_proposal(wd, actor="alice")
    assert "four-eyes" in fail(400, svc(wd).approve, prop["proposal_sha256"], {"reviewer": "alice"})
    assert not (wd / "schemas").exists()                     # nothing written on refusal
    assert svc(wd).get(prop["proposal_sha256"])["state"]["state"] == "pending"


def test_needs_review_items_require_explicit_include_and_a_note(wd):
    prop, _ = make_proposal(wd, NEEDS_REVIEW)
    pid, s = prop["proposal_sha256"], svc(wd)
    assert "order_date" in fail(400, s.approve, pid, {"reviewer": "bob"})                     # required, not included
    assert "note is required" in fail(400, s.approve, pid, {"reviewer": "bob", "include": ["order_date"]})
    res = s.approve(pid, {"reviewer": "bob", "include": ["order_date"], "note": "checked the source system"})
    prov = res["schema"]["provenance"]
    assert prov["included_needs_review"] == ["order_date"] and prov["review_note"] == "checked the source system"


def test_excluding_a_verified_optional_mapping_needs_a_note_and_is_recorded(wd):
    prop, _ = make_proposal(wd)
    pid, s = prop["proposal_sha256"], svc(wd)
    fail(400, s.approve, pid, {"reviewer": "bob", "exclude": ["customer_email"]})
    res = s.approve(pid, {"reviewer": "bob", "exclude": ["customer_email"], "note": "PII not needed"})
    cols = {c["name"]: c for c in res["schema"]["columns"]}
    assert "source" not in cols["customer_email"] and res["schema"]["provenance"]["excluded_accepted"] == ["customer_email"]
    assert "region" in json.dumps(res["schema"]["provenance"]["mapped_targets"])


def test_excluding_a_required_mapping_is_refused(wd):
    prop, _ = make_proposal(wd)
    assert "amount" in fail(400, svc(wd).approve, prop["proposal_sha256"],
                            {"reviewer": "bob", "exclude": ["amount"], "note": "x"})


def test_rejected_items_cannot_be_included_and_unknown_targets_fail(wd):
    prop, _ = make_proposal(wd, GOOD[:5] + [m("Area", "paid", 0.99)])    # 'paid' only has a rejected claim
    pid, s = prop["proposal_sha256"], svc(wd)
    rejected = next(i["target"] for i in prop["items"] if i["status"] == "rejected")
    assert rejected == "paid"
    assert "cannot be included" in fail(400, s.approve, pid, {"reviewer": "bob", "include": [rejected], "note": "force it"})
    fail(400, s.approve, pid, {"reviewer": "bob", "include": ["nonexistent"], "note": "x"})
    assert s.get(pid)["state"]["state"] == "pending"


def test_fixed_reviewer_overrides_whatever_the_browser_sends(wd):
    prop, _ = make_proposal(wd)
    s = svc(wd, fixed_reviewer="carol")
    res = s.approve(prop["proposal_sha256"], {"reviewer": "mallory"})
    assert res["schema"]["provenance"]["approved_by"] == "carol"
    assert AuditLog(wd / "audit.jsonl").records()[-1]["actor"] == "carol"


@pytest.mark.parametrize("payload", [
    {}, {"reviewer": ""}, {"reviewer": "   "}, {"reviewer": "x" * 81}, {"reviewer": "bo\nb"}, {"reviewer": 5},
    {"reviewer": "bob", "include": "order_date"}, {"reviewer": "bob", "include": [1]},
    {"reviewer": "bob", "include": ["x"] * 501}, {"reviewer": "bob", "note": "n" * 501}, {"reviewer": "bob", "note": 5},
])
def test_malformed_payloads_are_400(wd, payload):
    prop, _ = make_proposal(wd)
    fail(400, svc(wd).approve, prop["proposal_sha256"], payload)


def test_non_object_payload_is_400(wd):
    prop, _ = make_proposal(wd)
    for bad in (None, [], "x", 5):
        fail(400, svc(wd).approve, prop["proposal_sha256"], bad)
        fail(400, svc(wd).reject, prop["proposal_sha256"], bad)


def test_note_control_characters_are_neutralised(wd):
    prop, _ = make_proposal(wd)
    res = svc(wd).approve(prop["proposal_sha256"], {"reviewer": "bob", "note": "ok\x00\x1b[31m red"})
    assert "\x1b" not in json.dumps(res) and "\x00" not in (wd / "audit.jsonl").read_text()


def test_schema_file_name_is_sanitised(wd):
    prop, _ = make_proposal(wd)
    path = next((wd / "mappings").glob("*.json"))
    doc = json.loads(path.read_text())
    # a hostile schema name inside a (re-sealed) proposal must not escape the schemas/ folder
    from datapipe.pipeline import sha256_of
    doc["target_schema"]["name"] = "../../evil name"
    from datapipe.schema import schema_from_dict
    doc["target_fingerprint"] = schema_from_dict(doc["target_schema"]).fingerprint()
    doc.pop("proposal_sha256")
    doc["proposal_sha256"] = sha256_of(doc)
    path.write_text(json.dumps(doc))
    res = svc(wd).approve(doc["proposal_sha256"], {"reviewer": "bob"})
    assert (wd / res["schema_file"]).resolve().parent == (wd / "schemas").resolve()
    assert set(res["schema_file"]) <= set("abcdefghijklmnopqrstuvwxyz0123456789_-./")


# ---------------------------------------------------------------- rejecting
def test_reject_needs_reason_and_records_state(wd):
    prop, _ = make_proposal(wd)
    pid, s = prop["proposal_sha256"], svc(wd)
    assert "reason" in fail(400, s.reject, pid, {"reviewer": "bob"})
    assert "reason" in fail(400, s.reject, pid, {"reviewer": "bob", "note": "  "})
    s.reject(pid, {"reviewer": "bob", "note": "wrong file"})
    st = s.get(pid)["state"]
    assert st["state"] == "rejected" and st["by"] == "bob" and st["note"] == "wrong file"
    assert "already rejected" in fail(409, s.approve, pid, {"reviewer": "carol"})
    assert not (wd / "schemas").exists()


def test_proposer_may_withdraw_their_own_proposal(wd):
    prop, _ = make_proposal(wd, actor="alice")
    svc(wd).reject(prop["proposal_sha256"], {"reviewer": "alice", "note": "withdrawn"})


def test_concurrent_approvals_across_separate_services_exactly_one_wins(wd):
    """Separate ReviewService objects share no lock (like a CLI approval racing the UI): the audit-level
    guard must still let exactly one decision through, and the schema file must belong to the winner."""
    prop, _ = make_proposal(wd)
    pid, results = prop["proposal_sha256"], []
    barrier = threading.Barrier(6)

    def worker(i):
        barrier.wait()
        try:
            svc(wd).approve(pid, {"reviewer": f"rev{i}"})
            results.append(f"rev{i}")
        except ApiError as e:
            results.append(e.status)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    winners = [r for r in results if isinstance(r, str)]
    assert len(winners) == 1 and results.count(409) == 5
    approvals = [r for r in AuditLog(wd / "audit.jsonl").records() if r["event"] == "mapping_approved"]
    assert len(approvals) == 1 and approvals[0]["actor"] == winners[0]
    files = list((wd / "schemas").iterdir())
    assert len(files) == 1 and files[0].suffix == ".json"                 # no stray .tmp files from the losers
    assert json.loads(files[0].read_text())["provenance"]["approved_by"] == winners[0]
    assert AuditLog(wd / "audit.jsonl").verify()[0]


def test_concurrent_approvals_through_one_service_exactly_one_wins(wd):
    prop, _ = make_proposal(wd)
    pid, results, s = prop["proposal_sha256"], [], svc(wd)
    barrier = threading.Barrier(6)

    def worker(i):
        barrier.wait()
        try:
            s.approve(pid, {"reviewer": f"rev{i}"})
            results.append("ok")
        except ApiError as e:
            results.append(e.status)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results, key=str) == [409] * 5 + ["ok"]
    assert len([r for r in AuditLog(wd / "audit.jsonl").records() if r["event"] == "mapping_approved"]) == 1


_ = (Scripted, parse_csv)
