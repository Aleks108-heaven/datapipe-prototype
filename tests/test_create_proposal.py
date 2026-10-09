"""Creating a mapping proposal from the app (Review mappings > Create a proposal): the same lists as the Run tab, the built-in offline matcher,
nothing leaving the computer, and a review of what it made that works like the review of a proposal made with `datapipe map`."""
import json
import shutil
import threading

import pytest

from conftest import EX
from datapipe.audit import AuditLog
from datapipe.cli import main
from datapipe.sample import generate
from datapipe.webui import make_server
from test_webui_run import TOKEN, Client

pw = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402


@pytest.fixture
def app(tmp_path):
    work, data = tmp_path / "work", tmp_path / "data"
    data.mkdir()
    for name in ("sales.csv", "sales_renamed.csv", "schema_sales.json", "analysis_sales.json"):
        shutil.copy(EX / name, data)
    server = make_server(work, port=0, token=TOKEN, data_dirs=[data])
    threading.Thread(target=lambda: server.serve_forever(0.05), daemon=True).start()
    server.work, server.data = work, data
    yield server
    server.shutdown()
    server.server_close()


def listed(c, kind, name):
    items = c.json("GET", "/api/run/options")[1][kind]
    return next(x["id"] for x in items if x["name"] == name)


def body(c, **over):
    out = {"file": listed(c, "files", "sales_renamed.csv"), "schema": listed(c, "schemas", "schema_sales.json"), "policy": "business", "actor": "anna"}
    out.update(over)
    return out


def saved(app):
    folder = app.work / "mappings"
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def audit_events(app):
    return [r["event"] for r in AuditLog(app.work / "audit.jsonl").records()] if (app.work / "audit.jsonl").exists() else []


# ---------------------------------------------------------------- the service
def test_a_proposal_is_made_from_the_lists_saved_for_review_and_recorded(app):
    c = Client(app).login()
    status, res = c.json("POST", "/api/proposals", body(c))
    assert status == 200, res
    assert res["name"] == "sales_renamed.csv" and res["actor"] == "anna"
    assert res["summary"] == {"accepted": 5, "needs_review": 1, "rejected": 0} and res["required_unmapped"] == 0
    assert saved(app) == [f"mapping-{res['id'][:10]}.json"]                          # one finished file, no half-written one beside it
    text = (app.work / "mappings" / saved(app)[0]).read_text(encoding="utf-8")
    doc = json.loads(text)
    assert doc["proposal_sha256"] == res["id"] and doc["actor"] == "anna" and doc["policy"] == "business"
    assert doc["provider"]["name"] == "heuristic" and doc["egress"]["mode"] == "none" and doc["source"]["name"] == "sales_renamed.csv"
    assert str(app.data) not in text and str(app.work) not in text                    # a proposal is passed around: it holds names, never where a file lives
    rows = c.json("GET", "/api/proposals")[1]["proposals"]
    assert [(r["id"], r["state"], r["actor"], r["integrity_ok"]) for r in rows] == [(res["id"], "pending", "anna", True)]
    assert audit_events(app) == ["mapping_proposed"]
    assert AuditLog(app.work / "audit.jsonl").verify()[0] is True


def test_it_makes_the_same_proposal_as_the_command_line(app, tmp_path):
    c = Client(app).login()
    res = c.json("POST", "/api/proposals", body(c))[1]
    assert main(["--workdir", str(tmp_path / "cli"), "map", str(EX / "sales_renamed.csv"), "--schema", str(EX / "schema_sales.json"), "--actor", "anna"]) == 0
    by_cli = json.loads(next((tmp_path / "cli" / "mappings").glob("mapping-*.json")).read_text(encoding="utf-8"))
    by_app = json.loads((app.work / "mappings" / f"mapping-{res['id'][:10]}.json").read_text(encoding="utf-8"))
    for key in ("items", "summary", "unmapped_targets", "unmapped_sources", "thresholds", "provider", "egress", "target_fingerprint", "policy", "actor"):
        assert by_app[key] == by_cli[key], key
    assert by_app["source"]["sha256"] == by_cli["source"]["sha256"] and by_app["source"]["columns"] == by_cli["source"]["columns"]


def test_a_second_person_decides_it_and_the_new_schema_appears_in_the_run_lists(app):
    c = Client(app).login()
    pid = c.json("POST", "/api/proposals", body(c))[1]["id"]
    status, refused = c.json("POST", f"/api/proposals/{pid}/approve", {"reviewer": "anna", "note": "x", "include": ["order_date"]})
    assert status == 400 and "four-eyes" in refused["error"]                          # the proposer cannot approve it, whichever way it was made
    status, done = c.json("POST", f"/api/proposals/{pid}/approve", {"reviewer": "bob", "note": "dates checked", "include": ["order_date"]})
    assert status == 200 and done["schema_file"].startswith("schemas/")
    assert [r["state"] for r in c.json("GET", "/api/proposals")[1]["proposals"]] == ["approved"]
    names = [s["name"] for s in c.json("GET", "/api/run/options")[1]["schemas"]]
    assert any(n.endswith(f"-mapped-{pid[:10]}.json") for n in names), names          # the loop closes: the Run tab can use it


def test_only_ids_from_the_lists_and_valid_values_are_accepted_and_nothing_is_written(app, tmp_path):
    c = Client(app).login()
    good = body(c)
    schema_id, file_id = good["schema"], good["file"]
    analysis_id = listed(c, "analyses", "analysis_sales.json")
    bad = [{"file": "../x"}, {"file": ""}, {"file": 5}, {"file": str(app.data / "sales_renamed.csv")}, {"file": schema_id}, {"file": analysis_id},
           {"schema": "0"}, {"schema": file_id}, {"schema": analysis_id}, {"schema": str(app.data / "schema_sales.json")},
           {"policy": "nope"}, {"policy": None}, {"policy": ["low"]}, {"actor": "x" * 81}, {"actor": 7}, {"actor": ["anna"]}]
    for over in bad:
        status, res = c.json("POST", "/api/proposals", {**good, **over})
        assert status == 400, (over, status, res)
    for payload in ([], "x", 5):                                                        # a body that is not an object
        assert c.json("POST", "/api/proposals", payload)[0] == 400, payload
    assert saved(app) == [] and audit_events(app) == []                                 # every refusal left no proposal and no record behind
    for policy in (["low"], {"a": 1}):                                                  # the Run action crashed (500) on a policy that is not text, too
        assert c.json("POST", "/api/run/start", {"file": file_id, "schema": schema_id, "policy": policy, "actor": "anna"})[0] == 400, policy


def test_the_same_guards_as_every_other_action_apply(app):
    c = Client(app).login()
    good = body(c)
    assert Client(app).req("POST", "/api/proposals", good, authed=False)[0] == 401      # not signed in
    status, _, _ = c.req("POST", "/api/proposals", good, headers={"X-DataPipe-CSRF": "wrong"})
    assert status == 403
    status, _, _ = c.req("POST", "/api/proposals", good, headers={"Origin": "http://evil.example"})
    assert status == 403
    assert saved(app) == []


def test_the_size_limit_from_settings_applies_and_nothing_is_saved(app):
    generate(app.data / "big.csv", mb=2)
    c = Client(app).login()
    assert c.json("POST", "/api/settings", {"max_file_mb": 1})[0] == 200
    status, res = c.json("POST", "/api/proposals", body(c, file=listed(c, "files", "big.csv")))
    assert status == 400 and "limit" in res["error"], res
    assert saved(app) == [] and audit_events(app) == []


def test_a_file_with_too_many_columns_is_refused_with_the_reason(app):
    (app.data / "wide.csv").write_text(",".join(f"c{i}" for i in range(201)) + "\n" + ",".join("1" for _ in range(201)) + "\n", encoding="utf-8")
    c = Client(app).login()
    status, res = c.json("POST", "/api/proposals", body(c, file=listed(c, "files", "wide.csv")))
    assert status == 400 and "more than 200 columns" in res["error"], res
    assert saved(app) == []


def test_one_proposal_at_a_time(app):
    c = Client(app).login()
    assert app.runner._proposing.acquire(blocking=False)
    try:
        status, res = c.json("POST", "/api/proposals", body(c))
        assert status == 409 and "already being created" in res["error"], res
    finally:
        app.runner._proposing.release()
    assert c.json("POST", "/api/proposals", body(c))[0] == 200


def test_a_proposal_from_a_file_chosen_anywhere_still_lets_a_column_be_chosen_again(app, tmp_path):
    """The review finds the original file by name among the data folders and the files chosen from anywhere (checked against the recorded
    sha256): without that, a proposal made from a file in Downloads would have no 'use a different file column' on its page."""
    elsewhere = tmp_path / "downloads" / "supplier_jan.csv"
    elsewhere.parent.mkdir()
    shutil.copy(EX / "sales_renamed.csv", elsewhere)
    c = Client(app).login()
    added = c.json("POST", "/api/run/add-file", {"path": str(elsewhere)})[1]["added"]
    assert added["kind"] == "file"
    pid = c.json("POST", "/api/proposals", body(c, file=added["id"]))[1]["id"]
    remap = c.json("GET", f"/api/proposals/{pid}")[1]["manual_remap"]
    assert remap["available"] is True and "Order No" in remap["columns"], remap
    status, check = c.json("POST", f"/api/proposals/{pid}/check", {"target": "region", "source": "Area"})
    assert status == 200 and check["evidence"]["parse_rate"] == 1.0                    # verified against the real values in that file
    elsewhere.write_text(elsewhere.read_text(encoding="utf-8") + "99,x@y.co,N,1.0,2024-01-01,yes\n", encoding="utf-8")
    remap = c.json("GET", f"/api/proposals/{pid}")[1]["manual_remap"]
    assert remap["available"] is False and "differs" in remap["reason"], remap          # a changed file is never used
    elsewhere.unlink()
    remap = c.json("GET", f"/api/proposals/{pid}")[1]["manual_remap"]
    assert remap["available"] is False and "not found" in remap["reason"], remap


# ---------------------------------------------------------------- the page
@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:
            pytest.skip(f"Chromium not available: {exc}")
        yield b
        b.close()


@pytest.fixture
def page(browser, app):
    app.runner._can_browse = True                                  # as on a normal desktop (a CI machine has no file window)
    ctx = browser.new_context(viewport={"width": 1366, "height": 900}, locale="en-US")
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.goto(f"http://127.0.0.1:{app.port}/?t={TOKEN}")
    pg.goto(f"http://127.0.0.1:{app.port}/#/")
    yield pg
    assert pg.errors == [], pg.errors
    ctx.close()


def choose(page, selector, name):
    page.select_option(selector, page.locator(f"{selector} option", has_text=name).first.get_attribute("value"))


def test_an_empty_review_tab_offers_the_form_and_the_whole_loop_works_in_the_browser(page, app):
    expect(page.get_by_role("heading", name="Mapping proposals")).to_be_visible()
    expect(page.locator("#prop-empty")).to_contain_text("No proposals found.")
    expect(page.locator("#prop-empty")).to_contain_text(f"Looking in: {app.work / 'mappings'}")
    expect(page.locator("#prop-go")).to_be_disabled()
    expect(page.locator("#prop-why")).to_have_text("To create a proposal, choose a data file and a schema.")        # the name is already filled in from Settings or the system
    choose(page, "#prop-file", "sales_renamed.csv")
    choose(page, "#prop-schema", "schema_sales.json")
    page.locator("#prop-actor").fill("anna")
    expect(page.locator("#prop-go")).to_be_enabled()
    expect(page.locator("#prop-why")).to_have_text("")
    page.locator("#prop-go").click()
    flash = page.locator("#prop-flash")
    expect(flash).to_be_visible()
    for part in ("sales_renamed.csv", "5 verified", "1 need review", "0 rejected", "A different person than anna must approve it"):
        expect(flash).to_contain_text(part)
    expect(page.locator(".card.click")).to_have_count(1)                                          # the new proposal is listed, ready to open
    expect(page.locator("#prop-box")).not_to_have_attribute("open", "")                           # the form folds away once there is something to review
    page.locator(".card.click").click()                                                           # the review itself is the one every proposal gets
    page.wait_for_selector("#items .card")
    page.locator("#reviewer").fill("anna")
    expect(page.locator("#why")).to_contain_text("must be a different person than the proposer (anna)")
    page.locator("#reviewer").fill("bob")
    page.locator('[data-target="order_date"]').get_by_role("radio", name="Include").click()
    page.locator("#note").fill("dates checked")
    page.locator("#approve").click()
    expect(page.locator("#result")).to_contain_text("Approved. Schema created")
    page.get_by_role("link", name="Run a file").click()
    page.wait_for_selector("#run-schema")
    assert page.locator("#run-schema option", has_text="-mapped-").count() == 1                   # and the Run tab can use the schema it made


def test_the_form_stays_one_click_away_when_proposals_exist_and_its_command_line_hint_is_complete(page, app):
    c = Client(app).login()
    c.json("POST", "/api/proposals", body(c))
    page.reload()
    page.wait_for_selector(".card.click")
    expect(page.locator("#prop-empty")).to_have_count(0)
    expect(page.locator("#prop-file")).to_be_hidden()
    page.locator("#prop-box > summary").click()
    expect(page.locator("#prop-file")).to_be_visible()
    page.locator("#prop-cmd-box > summary").click()
    cmd = page.locator("#prop-cmd").inner_text()
    assert cmd.startswith("python -m datapipe --workdir ") and str(app.work) in cmd and cmd.endswith("map <file> --schema <schema.json>"), cmd
    assert cmd.index("--workdir") < cmd.index(" map ")                                            # the order that matters: --workdir before map


def test_choosing_a_file_on_this_computer_fills_the_form_with_it(page, app, tmp_path):
    elsewhere = tmp_path / "downloads" / "supplier_jan.csv"
    elsewhere.parent.mkdir()
    shutil.copy(EX / "sales_renamed.csv", elsewhere)
    app.runner._chooser = lambda: elsewhere                                                       # the file window is native: a stand-in answers
    page.locator("#prop-actor").fill("anna")
    page.locator("#prop-browse").click()
    expect(page.locator("#prop-file option:checked")).to_contain_text("supplier_jan.csv")        # a locator, not wait_for_function(string): the page's CSP forbids eval
    assert page.locator("#prop-actor").input_value() == "anna"                                    # what was typed survives the redraw
    choose(page, "#prop-schema", "schema_sales.json")
    page.locator("#prop-go").click()
    expect(page.locator("#prop-flash")).to_contain_text("supplier_jan.csv")
    page.locator(".card.click").click()
    page.wait_for_selector("#items .card")
    expect(page.locator('[data-target="region"] select')).to_be_visible()                         # 'use a different file column' is there for a file from anywhere


def test_a_refusal_is_shown_next_to_the_button_and_the_form_keeps_what_was_typed(page, app):
    generate(app.data / "big.csv", mb=2)
    Client(app).login().json("POST", "/api/settings", {"max_file_mb": 1})
    page.reload()
    page.wait_for_selector("#prop-file")
    choose(page, "#prop-file", "big.csv")
    choose(page, "#prop-schema", "schema_sales.json")
    page.locator("#prop-actor").fill("anna")
    page.locator("#prop-go").click()
    expect(page.locator("#prop-msg")).to_contain_text("limit")
    expect(page.locator("#prop-go")).to_be_enabled()                                              # nothing is stuck: fix it and try again
    assert page.locator("#prop-actor").input_value() == "anna" and page.locator("#prop-file").evaluate("e => e.selectedOptions[0].text").startswith("big.csv")
    assert saved(app) == []
