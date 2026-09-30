import json

import pytest

from conftest import SCHEMA
from datapipe.errors import SchemaError
from datapipe.ingest import parse_csv
from datapipe.policy import get_policy
from datapipe.schema import compare_columns, infer_schema, load_schema, schema_from_dict
from datapipe.validate import validate


def col(**kw):
    return {"name": "x", **kw}


def test_schema_rejects_typos_and_bad_definitions():
    for doc in [
        {"columns": [col(type="integr")]},
        {"columns": [col(requird=True)]},
        {"colums": []},
        {"columns": []},
        {"columns": [col(name="1bad")]},
        {"columns": [col(name="a b")]},
        {"columns": [col(), col()]},
        {"columns": [col(type="string", scale=2)]},
        {"columns": [col(type="integer", format="%Y")]},
        {"columns": [col(type="string", min=1)]},
        {"columns": [col(type="integer", min="abc")]},
        {"columns": [col(type="decimal", scale=99)]},
        {"columns": [col(pattern="(")]},
    ]:
        with pytest.raises(SchemaError):
            schema_from_dict(doc)


def test_schema_roundtrip_and_stable_fingerprint():
    s1 = load_schema(SCHEMA)
    s2 = schema_from_dict(json.loads(json.dumps(s1.to_dict())))
    assert s1.fingerprint() == s2.fingerprint()
    changed = s1.to_dict()
    changed["columns"][3]["min"] = "1"
    assert schema_from_dict(changed).fingerprint() != s1.fingerprint()


def test_inference_is_conservative():
    t = parse_csv("Order ID,Zip,Flag,Qty,Price,Day,Email,Note\n"
                  "1,01234,true,1,1.5,2026-01-01,a@x.io,hi\n"
                  "2,98765,false,0,2.25,2026-01-02,b@x.io,\n")
    s = {c.name: c for c in infer_schema(t).columns}
    assert s["order_id"].type == "integer" and s["order_id"].source == "Order ID"
    assert s["zip"].type == "string"                 # leading zero must not become an integer
    assert s["flag"].type == "boolean"
    assert s["qty"].type == "integer"                # 0/1 style values are integers, not booleans
    assert s["price"].type == "decimal" and s["day"].type == "date"
    assert s["email"].pii is True                    # fail-safe PII guess
    assert s["note"].required is False and s["order_id"].required is True


def test_inference_dedupes_and_sanitizes_names():
    t = parse_csv("a-b,a b,3d\n1,2,3\n")
    assert [c.name for c in infer_schema(t).columns] == ["a_b", "a_b_2", "_3d"]


def test_drift_detection():
    s = load_schema(SCHEMA)
    missing, extra = compare_columns(s, ["order_id", "region", "surprise"])
    assert "amount" in [c.name for c in missing] and extra == ["surprise"]


# ---------------------------------------------------------------- validation
def v(text, policy="low", schema=None):
    t = parse_csv(text)
    return validate(t, schema or load_schema(SCHEMA), get_policy(policy))


HEAD = "order_id,customer_email,region,amount,order_date,paid\n"


def test_valid_rows_are_typed():
    r = v(HEAD + "1,a@x.io,EU,1.50,2026-01-01,true\n")
    assert not r.quarantined and r.valid_rows[0]["order_id"] == 1


def test_required_min_allowed_pattern_rules():
    r = v(HEAD + ",a@x.io,EU,1.50,2026-01-01,true\n"          # required
              "2,notanemail,EU,1.50,2026-01-01,true\n"        # pattern
              "3,a@x.io,XX,1.50,2026-01-01,true\n"            # allowed
              "4,a@x.io,EU,-0.01,2026-01-01,true\n"           # min boundary just below
              "5,a@x.io,EU,0.00,2026-01-01,true\n")           # min boundary exactly on -> valid
    assert r.counts_by_rule() == {"allowed": 1, "min": 1, "pattern": 1, "required": 1}
    assert [x["order_id"] for x in r.valid_rows] == [5]


def test_unique_quarantines_every_member_of_a_duplicate_group():
    r = v(HEAD + "1,a@x.io,EU,1,2026-01-01,true\n1,b@x.io,EU,2,2026-01-01,true\n2,c@x.io,EU,3,2026-01-01,true\n")
    assert sorted(q.row for q in r.quarantined) == [1, 2] and len(r.valid_rows) == 1


def test_nulls_are_not_duplicates():
    s = schema_from_dict({"columns": [{"name": "k", "type": "integer", "unique": True}]})
    r = validate(parse_csv("k\n\n\n5\n"), s, get_policy("low"))
    assert not r.quarantined


def test_pii_values_masked_in_issues_by_policy():
    text = HEAD + "1,bad-email-jane@,EU,1,2026-01-01,true\n"
    low = v(text, "low").issues
    biz = v(text, "business").issues
    assert any(i.value == "bad-email-jane@" for i in low)
    assert all("jane" not in json.dumps(i.as_dict()) for i in biz)


def test_string_constraints_max_length():
    s = schema_from_dict({"columns": [{"name": "c", "type": "string", "max_length": 3}]})
    r = validate(parse_csv("c\nabc\nabcd\n"), s, get_policy("low"))
    assert [q.row for q in r.quarantined] == [2]


def test_structural_rows_are_quarantined_with_reason():
    r = v(HEAD + "1,a@x.io,EU\n")
    assert r.quarantined[0].issues[0].rule == "structure" and r.rows_total == 1


def test_custom_null_tokens():
    s = schema_from_dict({"null_tokens": ["", "N/A"], "columns": [{"name": "n", "type": "integer", "required": True}]})
    r = validate(parse_csv("n\nN/A\n3\n"), s, get_policy("low"))
    assert r.counts_by_rule() == {"required": 1}
