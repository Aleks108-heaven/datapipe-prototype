"""Does the chosen metrics file fit the chosen schema? Found when a run of a products file stopped with the database's own "Referenced column
"amount" not found": the page had swapped the schema but left the sales metrics file selected, and said nothing until the run had failed."""
import json
import shutil
import threading

import pytest

from conftest import EX
from datapipe.webui import make_server
from datapipe.webui.runner import RunService
from test_webui_run import TOKEN, Client

PAIRS = [("schema_sales.json", "analysis_sales.json"), ("schema_buyers.json", "analysis_buyers.json"),
         ("schema_products_buyers.json", "analysis_products_buyers.json")]


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    for schema, analysis in PAIRS:
        shutil.copy(EX / schema, d)
        shutil.copy(EX / analysis, d)
    shutil.copy(EX / "sales.csv", d)
    from datapipe.sample import generate
    generate(d / "buyers_sample.csv", rows=300)
    (d / "analysis_pii.json").write_text(json.dumps({"metrics": [{"name": "emails", "description": "", "sql": "SELECT COUNT(customer_email) AS n FROM data"}]}))
    (d / "analysis_broken.json").write_text(json.dumps({"metrics": [{"name": "oops", "sql": "SELEC nonsense"}]}))
    (d / "analysis_generic.json").write_text(json.dumps({"metrics": [{"name": "rows", "sql": "SELECT COUNT(*) AS n FROM data"}]}))
    return d


@pytest.fixture
def svc(tmp_path, data):
    s = RunService(tmp_path / "work", data_dirs=[data], chooser=lambda: None)
    o = s.options()
    s.sid = {x["name"]: x["id"] for x in o["schemas"]}
    s.aid = {x["name"]: x["id"] for x in o["analyses"]}
    s.verdict = lambda schema, analysis, policy="business": s.check({"schema": s.sid[schema], "analysis": s.aid[analysis], "policy": policy})["metrics"]
    return s


# ---------------------------------------------------------------- the check itself
@pytest.mark.parametrize("policy", ["low", "business", "regulated"])
@pytest.mark.parametrize("schema,analysis", PAIRS)
def test_every_schema_fits_its_own_metrics_file_under_every_policy(svc, schema, analysis, policy):
    m = svc.verdict(schema, analysis, policy)
    assert m["chosen"]["ok"] is True and m["best"]["name"] == analysis


def test_the_mismatch_from_the_screenshot_is_named_in_plain_terms_and_the_right_file_is_offered(svc):
    m = svc.verdict("schema_products_buyers.json", "analysis_sales.json")
    assert m["chosen"]["ok"] is False and m["chosen"]["metric"] == "total_amount" and m["chosen"]["column"] == "amount"
    assert m["chosen"]["hidden"] is False
    assert m["best"] == {"id": svc.aid["analysis_products_buyers.json"], "name": "analysis_products_buyers.json"}   # the one that goes with the schema


def test_a_metric_on_personal_data_is_called_out_when_the_policy_keeps_that_column_out(svc):
    low = svc.verdict("schema_sales.json", "analysis_pii.json", "low")
    assert low["chosen"]["ok"] is True                                              # the low policy keeps personal columns
    for policy in ("business", "regulated"):
        m = svc.verdict("schema_sales.json", "analysis_pii.json", policy)["chosen"]
        assert m["ok"] is False and m["column"] == "customer_email" and m["hidden"] is True, policy
    other = svc.verdict("schema_buyers.json", "analysis_pii.json", "business")["chosen"]
    assert other["ok"] is False and other["hidden"] is False                        # a schema without that column simply does not have it


def test_sql_that_does_not_parse_does_not_fit_either(svc):
    m = svc.verdict("schema_sales.json", "analysis_broken.json")["chosen"]
    assert m["ok"] is False and m["metric"] == "oops" and m["column"] is None and "parse" in m["message"]


def test_a_generic_metric_fits_every_schema_and_is_never_offered_in_place_of_the_twin(svc):
    for schema, analysis in PAIRS:
        assert svc.verdict(schema, "analysis_generic.json")["chosen"]["ok"] is True
        assert svc.verdict(schema, "analysis_broken.json")["best"]["name"] == analysis       # the twin wins over other files that merely run


def test_nothing_is_said_until_there_is_a_schema_and_a_metrics_choice(svc):
    assert svc.check({"analysis": svc.aid["analysis_sales.json"]}) == {"known": False, "metrics": None}
    assert svc.check({"schema": svc.sid["schema_sales.json"]})["metrics"]["chosen"] is None      # no metrics file chosen: nothing to judge
    assert svc.check({"schema": svc.sid["schema_sales.json"], "analysis": "no-such-id"})["metrics"]["chosen"] is None
    assert svc.check({"schema": "no-such-id", "analysis": svc.aid["analysis_sales.json"]})["metrics"] is None


def test_an_unknown_or_hostile_policy_value_is_treated_as_business_not_a_crash(svc):
    for bad in (None, 5, ["low"], {"a": 1}, "nonsense", ""):
        m = svc.check({"schema": svc.sid["schema_sales.json"], "analysis": svc.aid["analysis_sales.json"], "policy": bad})["metrics"]
        assert m["chosen"]["ok"] is True


def test_the_check_reads_no_data_and_still_works_without_a_file(svc, data):
    out = svc.check({"schema": svc.sid["schema_buyers.json"], "analysis": svc.aid["analysis_buyers.json"], "policy": "business"})
    assert out["known"] is False and out["metrics"]["chosen"]["ok"] is True
    file_id = next(f["id"] for f in svc.options()["files"] if f["name"] == "buyers_sample.csv")
    with_file = svc.check({"file": file_id, "schema": svc.sid["schema_buyers.json"], "analysis": svc.aid["analysis_buyers.json"]})
    assert with_file["known"] is True and with_file["chosen"]["missing_required_count"] == 0 and with_file["metrics"]["chosen"]["ok"] is True
    assert str(data) not in json.dumps(with_file)                                    # names of columns and metrics only


@pytest.fixture
def app(tmp_path, data):
    server = make_server(tmp_path / "work", port=0, token=TOKEN, data_dirs=[data])
    threading.Thread(target=lambda: server.serve_forever(0.05), daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def test_the_check_endpoint_answers_in_the_same_shape_and_still_refuses_a_bad_file_id(app):
    c = Client(app).login()
    o = c.json("GET", "/api/run/options")[1]
    sid = {x["name"]: x["id"] for x in o["schemas"]}
    aid = {x["name"]: x["id"] for x in o["analyses"]}
    status, r = c.json("POST", "/api/run/check", {"schema": sid["schema_products_buyers.json"], "analysis": aid["analysis_sales.json"], "policy": "business"})
    assert status == 200 and r["metrics"]["chosen"]["ok"] is False and r["metrics"]["best"]["name"] == "analysis_products_buyers.json"
    assert c.json("POST", "/api/run/check", {"file": "../x"})[0] == 400


# ---------------------------------------------------------------- the page, replaying the click path from the screenshot
pw = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402


def choose(page, selector, name):
    page.select_option(selector, page.locator(f"{selector} option", has_text=name).first.get_attribute("value"))


@pytest.fixture
def page(app):
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:
            pytest.skip(f"Chromium not available: {exc}")
        pg = browser.new_page(viewport={"width": 1100, "height": 1000})
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.goto(f"http://127.0.0.1:{app.port}/?t={TOKEN}&go=run")
        pg.wait_for_selector("#run-file")
        pg.locator("#run-actor").fill("tester")
        yield pg
        browser.close()
        assert pg.errors == []


def selected(page, selector):
    return page.locator(f"{selector} option:checked").inner_text().split("  (")[0]


def test_switching_the_schema_with_the_suggestion_button_brings_its_metrics_file_along(page):
    choose(page, "#run-file", "sales.csv")
    expect(page.locator("#run-fit")).to_contain_text("fits the file")
    assert (selected(page, "#run-schema"), selected(page, "#run-analysis")) == ("schema_sales.json", "analysis_sales.json")
    expect(page.locator("#run-metrics-fit")).to_contain_text("The metrics fit this schema")
    choose(page, "#run-file", "buyers_sample.csv")                                 # the person moves on to another file...
    expect(page.locator("#run-fit")).to_contain_text("does not fit this file")
    page.get_by_role("button", name="Use schema_buyers.json instead (fits)").click()   # ...and takes the page's suggestion
    expect(page.locator("#run-fit")).to_contain_text("fits the file")
    assert (selected(page, "#run-schema"), selected(page, "#run-analysis")) == ("schema_buyers.json", "analysis_buyers.json"), \
        "the metrics file must follow the schema; this is the bug in the screenshot"
    expect(page.locator("#run-metrics-fit")).to_contain_text("The metrics fit this schema")
    page.select_option("#run-policy", "low")
    page.locator("#run-go").click()
    expect(page.locator("#run-summary")).to_be_visible(timeout=60000)
    expect(page.locator("#run-summary")).to_contain_text("rows read")
    expect(page.locator("#run-summary .badge")).not_to_contain_text("Could not finish")


def test_a_metrics_file_that_does_not_fit_is_flagged_before_the_run_with_two_ways_out(page):
    choose(page, "#run-file", "buyers_sample.csv")
    expect(page.locator("#run-fit")).to_contain_text("fits the file")
    choose(page, "#run-analysis", "analysis_sales.json")                              # the combination from the screenshot
    note = page.locator("#run-metrics-fit")
    expect(note).to_contain_text("does not fit this schema")
    expect(note).to_contain_text("“total_amount” needs the column “amount”")
    expect(page.locator("#run-metrics-use")).to_have_text("Use analysis_buyers.json instead (fits)")
    page.locator("#run-metrics-use").click()
    assert selected(page, "#run-analysis") == "analysis_buyers.json"
    expect(note).to_contain_text("The metrics fit this schema")
    choose(page, "#run-analysis", "analysis_sales.json")
    expect(note).to_contain_text("does not fit this schema")
    page.locator("#run-metrics-none").click()
    assert selected(page, "#run-analysis") == "No metrics (cleaning only)"
    expect(note).to_have_text("")
    expect(page.locator("#run-go")).to_be_enabled()                                   # a warning, not a lock: the person decides


def test_a_personal_data_metric_is_explained_as_the_policy_keeping_it_out(page):
    choose(page, "#run-file", "sales.csv")
    choose(page, "#run-analysis", "analysis_pii.json")
    page.select_option("#run-policy", "business")
    expect(page.locator("#run-metrics-fit")).to_contain_text("personal data that this policy keeps out of the metrics")
    page.select_option("#run-policy", "low")                                           # changing the policy re-checks
    expect(page.locator("#run-metrics-fit")).to_contain_text("The metrics fit this schema")


def test_the_warning_is_in_ukrainian_too(app, page):
    Client(app).login().json("POST", "/api/settings", {"language": "uk"})
    page.reload()
    page.wait_for_selector("#run-file")
    choose(page, "#run-file", "buyers_sample.csv")
    choose(page, "#run-analysis", "analysis_sales.json")
    note = page.locator("#run-metrics-fit")
    expect(note).to_contain_text("Цей файл метрик не підходить до цієї схеми")
    expect(note).to_contain_text("“total_amount” потрібен стовпець “amount”")
    expect(page.locator("#run-metrics-none")).to_have_text("Запустити без метрик")
    assert page.evaluate("window.__i18nMissing") == []
