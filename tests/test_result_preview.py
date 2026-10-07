"""The result page: a look at the cleaned data, a button for its folder, and a way forward when a metrics file stopped the run (in that case
nothing is written, because cleaned data exists only when every check passed)."""
import csv
import json
import re
import shutil
import threading
import time

import pytest

from conftest import EX
from datapipe.webui import make_server
from datapipe.webui.runner import PREVIEW_CELL, PREVIEW_COLUMNS, PREVIEW_ROWS, RunService
from test_webui_run import TOKEN, Client

RUN_ID = "20261007T120000Z-abcdef"
SALES_ROWS = len((EX / "sales.csv").read_text(encoding="utf-8").strip().splitlines()) - 1      # the data rows of the example file


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    for name in ("sales.csv", "schema_sales.json", "analysis_sales.json", "schema_buyers.json", "analysis_buyers.json"):
        shutil.copy(EX / name, d)
    from datapipe.sample import generate
    generate(d / "buyers_sample.csv", rows=300)
    return d


@pytest.fixture
def app(tmp_path, data):
    server = make_server(tmp_path / "work", port=0, token=TOKEN, data_dirs=[data])
    server.runner.opened = []
    server.runner._opener = server.runner.opened.append                              # no real window is opened by a test
    threading.Thread(target=lambda: server.serve_forever(0.05), daemon=True).start()
    server.work = tmp_path / "work"
    yield server
    server.shutdown()
    server.server_close()


def run_via_api(c, file, schema, analysis, policy="business"):
    o = c.json("GET", "/api/run/options")[1]
    pick = lambda items, name: next(x["id"] for x in items if x["name"] == name)
    body = {"file": pick(o["files"], file), "schema": pick(o["schemas"], schema), "policy": policy, "actor": "tester"}
    if analysis:
        body["analysis"] = pick(o["analyses"], analysis)
    assert c.json("POST", "/api/run/start", body)[0] == 200
    for _ in range(300):
        st = c.json("GET", "/api/run/status")[1]
        if st["state"] != "running":
            return st
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def fake_run(work, clean_bytes=None, result=None):
    """A run folder made by hand, for the cases a real run does not produce."""
    folder = work / "runs" / RUN_ID
    folder.mkdir(parents=True)
    if clean_bytes is not None:
        (folder / "clean.csv").write_bytes(clean_bytes)
    (folder / "result.json").write_text(json.dumps(result or {"status": "COMPLETED", "counts": {"valid": 3}, "policy": {"name": "low"}}), encoding="utf-8")
    return folder


# ---------------------------------------------------------------- the preview
def test_the_preview_is_exactly_the_first_rows_of_clean_csv_and_says_how_many_there_are(app):
    c = Client(app).login()
    st = run_via_api(c, "sales.csv", "schema_sales.json", "analysis_sales.json", "low")
    assert st["state"] == "done"
    status, p = c.json("GET", f"/api/run/preview?run={st['run_id']}")
    with open(app.work / "runs" / st["run_id"] / "clean.csv", encoding="utf-8-sig", newline="") as fh:
        expected = list(csv.reader(fh))
    assert status == 200 and [p["columns"]] + p["rows"] == expected[:PREVIEW_ROWS + 1]
    counts = c.json("GET", f"/api/run/result?run={st['run_id']}")[1]["counts"]
    assert p["total_rows"] == counts["valid"] and p["all_columns"] == len(expected[0]) and p["policy"] == "low"
    assert "anna@example.com" in json.dumps(p)                                       # the low policy keeps personal columns, so does the file


def test_a_masked_column_is_masked_in_the_preview_as_in_the_file(app):
    c = Client(app).login()
    st = run_via_api(c, "sales.csv", "schema_sales.json", "analysis_sales.json", "business")
    p = c.json("GET", f"/api/run/preview?run={st['run_id']}")[1]
    assert p["policy"] == "business" and "example.com" not in json.dumps(p), "the preview must not show what the file hides"
    assert p["rows"], "and it still shows the other columns"


def test_the_preview_reads_only_the_start_and_cuts_big_cells_and_wide_files(app):
    wide = ",".join(f"c{i}" for i in range(PREVIEW_COLUMNS + 5))
    line = ",".join("x" * 300 for _ in range(PREVIEW_COLUMNS + 5))
    folder = fake_run(app.work, ("\ufeff" + wide + "\n" + (line + "\n") * 50).encode("utf-8"))
    p = Client(app).login().json("GET", f"/api/run/preview?run={RUN_ID}")[1]
    assert len(p["rows"]) == PREVIEW_ROWS and len(p["columns"]) == PREVIEW_COLUMNS and p["all_columns"] == PREVIEW_COLUMNS + 5
    assert p["columns"][0] == "c0", "the byte-order mark is not part of the first column name"
    assert all(len(cell) <= PREVIEW_CELL for row in p["rows"] for cell in row) and all(len(row) == PREVIEW_COLUMNS for row in p["rows"])
    assert p["total_rows"] == 3 and folder.is_dir()


def test_a_preview_that_cannot_exist_is_a_clear_refusal_not_a_crash(app, tmp_path):
    c = Client(app).login()
    for bad in ("../x", "..%2f..%2fetc", "nope", "20261007T120000Z-ABCDEF", RUN_ID + "/..", ""):
        assert c.req("GET", f"/api/run/preview?run={bad}")[0] == 404, bad
    assert c.req("GET", "/api/run/preview")[0] == 404
    fake_run(app.work, None)                                                         # a run folder with no clean.csv (a failed run)
    status, doc = c.json("GET", f"/api/run/preview?run={RUN_ID}")
    assert status == 404 and doc["error"] == "this run has no cleaned data"


def test_unreadable_cleaned_data_gives_a_409_with_a_reason(app):
    c = Client(app).login()
    folder = fake_run(app.work, b"\xff\xfe\x00bad,bytes\n\xff\xff\xff\n")
    status, doc = c.json("GET", f"/api/run/preview?run={RUN_ID}")
    assert status == 409 and "preview" in doc["error"]
    (folder / "clean.csv").write_text("a\n" + "y" * 200_000 + "\n", encoding="utf-8")           # a cell bigger than the csv module accepts
    assert c.json("GET", f"/api/run/preview?run={RUN_ID}")[0] == 409


def test_a_symlinked_clean_csv_is_never_followed(app, tmp_path):
    secret = tmp_path / "secret.csv"
    secret.write_text("password\nhunter2\n")
    folder = fake_run(app.work, None)
    try:
        (folder / "clean.csv").symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    status, doc = Client(app).login().json("GET", f"/api/run/preview?run={RUN_ID}")
    assert status == 404 and "hunter2" not in json.dumps(doc)


def test_the_preview_needs_a_login(app):
    fake_run(app.work, b"a\n1\n")
    assert Client(app).req("GET", f"/api/run/preview?run={RUN_ID}")[0] == 401


# ---------------------------------------------------------------- the folder button
def test_the_folder_button_opens_that_runs_folder_and_nothing_else(app):
    c = Client(app).login()
    folder = fake_run(app.work, b"a\n1\n")
    status, doc = c.json("POST", "/api/run/open-folder", {"run": RUN_ID})
    assert status == 200 and doc == {"opened": True} and app.runner.opened == [folder.resolve()] or app.runner.opened == [folder]


@pytest.mark.parametrize("bad", [{"run": "../.."}, {"run": "C:\\Windows"}, {"run": "/etc"}, {"run": "x"}, {"run": 5}, {"run": ["a"]}, {"run": None}, {}, [], "x",
                                 {"run": "20261007T120000Z-abcdef"}])
def test_the_folder_button_takes_only_the_id_of_a_run_that_exists(app, bad):
    status, doc = Client(app).login().json("POST", "/api/run/open-folder", bad)
    assert status == 404 and app.runner.opened == []


def test_when_no_file_manager_exists_the_person_is_told_where_to_look_instead(app):
    def refuse(folder):
        raise OSError("no file manager is available")
    app.runner._opener = refuse
    fake_run(app.work, b"a\n1\n")
    status, doc = Client(app).login().json("POST", "/api/run/open-folder", {"run": RUN_ID})
    assert status == 409 and "path is shown on the page" in doc["error"]


def test_the_folder_button_needs_login_csrf_and_a_same_origin_request(app):
    fake_run(app.work, b"a\n1\n")
    c = Client(app).login()
    body = {"run": RUN_ID}
    assert Client(app).req("POST", "/api/run/open-folder", body, authed=False)[0] == 401
    assert c.req("POST", "/api/run/open-folder", body, headers={"X-DataPipe-CSRF": "wrong"})[0] == 403
    assert c.req("POST", "/api/run/open-folder", body, headers={"Origin": "http://evil.example"})[0] == 403
    assert c.req("GET", "/api/run/open-folder")[0] in (404, 405)
    assert app.runner.opened == []


def test_the_default_opener_uses_the_system_tool_without_a_shell(monkeypatch, tmp_path):
    import datapipe.webui.runner as runner
    calls = []

    class Child:
        def wait(self):
            return 0
    monkeypatch.setattr(runner.sys, "platform", "linux")
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/bin/xdg-open")
    monkeypatch.setattr(runner.subprocess, "Popen", lambda cmd, **kw: calls.append((cmd, kw)) or Child())
    runner._open_in_file_manager(tmp_path)
    assert calls[0][0] == ["/usr/bin/xdg-open", str(tmp_path)] and "shell" not in calls[0][1]
    monkeypatch.setattr(runner.sys, "platform", "darwin")
    runner._open_in_file_manager(tmp_path)
    assert calls[1][0] == ["open", str(tmp_path)]
    monkeypatch.setattr(runner.sys, "platform", "linux")
    monkeypatch.setattr(runner.shutil, "which", lambda name: None)
    with pytest.raises(OSError):
        runner._open_in_file_manager(tmp_path)


# ---------------------------------------------------------------- the page
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


def test_a_finished_run_shows_the_cleaned_data_a_main_download_button_and_a_folder_button(app, page):
    choose(page, "#run-file", "sales.csv")
    page.select_option("#run-policy", "low")
    page.locator("#run-go").click()
    expect(page.locator("#run-summary")).to_be_visible(timeout=60000)
    preview = page.locator("#run-preview")
    expect(preview).to_contain_text("Preview of the cleaned data")
    expect(preview).to_contain_text(f"The first {SALES_ROWS} of {SALES_ROWS} rows of clean.csv; the file has all of them.")
    assert page.locator("#run-preview-table tbody tr").count() == SALES_ROWS and page.locator("#run-preview-table th").first.inner_text() == "order_id"
    assert "anna@example.com" in preview.inner_text()
    main = page.get_by_role("link", name="Cleaned data (clean.csv)")
    expect(main).to_have_class("dl main")                                             # the button the person came for stands out
    with page.expect_download() as d:
        main.click()
    assert d.value.suggested_filename.endswith("-clean.csv")
    page.locator("#run-open-folder").click()
    expect(page.locator("#run-open-msg")).to_contain_text("The folder is open")
    assert len(app.runner.opened) == 1 and app.runner.opened[0].parent.name == "runs"
    assert page.evaluate("document.documentElement.scrollWidth - innerWidth") <= 1


def test_the_business_preview_says_it_is_masked(app, page):
    choose(page, "#run-file", "sales.csv")
    page.select_option("#run-policy", "business")
    page.locator("#run-go").click()
    expect(page.locator("#run-preview")).to_contain_text("Personal columns are masked here exactly as in the file.", timeout=60000)
    assert "example.com" not in page.locator("#run-preview").inner_text()


def test_after_a_metrics_failure_the_page_explains_and_offers_the_run_without_metrics(app, page):
    choose(page, "#run-file", "buyers_sample.csv")
    expect(page.locator("#run-fit")).to_contain_text("fits the file")
    choose(page, "#run-analysis", "analysis_sales.json")                              # the combination from the screenshot, run anyway
    expect(page.locator("#run-metrics-fit")).to_contain_text("does not fit this schema")
    page.locator("#run-go").click()
    expect(page.locator("#run-summary .badge")).to_have_text("Could not finish", timeout=60000)
    expect(page.locator("#run-summary")).to_contain_text("Referenced column")
    expect(page.locator("#run-preview")).to_have_count(0)                              # nothing was written, so nothing to preview
    assert page.get_by_role("link", name="Cleaned data (clean.csv)").count() == 0
    expect(page.locator("#run-metrics-failed")).to_contain_text("nothing was written for this run")
    page.locator("#run-rerun-nometrics").click()                                       # the way forward: the same run, without the metrics file
    expect(page.locator("#run-summary .badge")).not_to_have_text("Could not finish", timeout=60000)
    expect(page.locator("#run-summary")).to_contain_text("rows read")
    expect(page.locator("#run-preview")).to_contain_text("Preview of the cleaned data")
    expect(page.locator("#run-metrics-failed")).to_have_count(0)
    assert page.locator("#run-analysis option:checked").inner_text() == "No metrics (cleaning only)"


def test_the_rerun_button_is_not_offered_when_the_form_has_moved_to_another_file(app, page):
    choose(page, "#run-file", "buyers_sample.csv")
    choose(page, "#run-analysis", "analysis_sales.json")
    page.locator("#run-go").click()
    expect(page.locator("#run-metrics-failed")).to_be_visible(timeout=60000)
    choose(page, "#run-file", "sales.csv")                                             # the person looks at another file...
    page.get_by_role("link", name="Review mappings").click()
    page.get_by_role("link", name="Run a file").click()
    page.wait_for_selector("#run-file")
    page.goto(f"http://127.0.0.1:{app.port}/#/run/" + app.runner.recent()[0]["run_id"])
    page.wait_for_selector("#run-summary")
    expect(page.locator("#run-metrics-failed")).to_have_count(0)                       # ...so "run again" would run the wrong file: not offered


def test_the_new_buttons_and_notes_are_in_ukrainian(app, page):
    Client(app).login().json("POST", "/api/settings", {"language": "uk"})
    page.reload()
    page.wait_for_selector("#run-file")
    choose(page, "#run-file", "buyers_sample.csv")
    choose(page, "#run-analysis", "analysis_sales.json")
    page.locator("#run-go").click()
    expect(page.locator("#run-metrics-failed")).to_contain_text("для цього запуску нічого не записано", timeout=60000)
    expect(page.locator("#run-rerun-nometrics")).to_have_text("Запустити ще раз без метрик")
    expect(page.locator("#run-open-folder")).to_have_text("Відкрити папку запуску")
    page.locator("#run-rerun-nometrics").click()
    expect(page.locator("#run-preview")).to_contain_text("Попередній перегляд очищених даних", timeout=60000)
    expect(page.locator("#run-preview")).to_contain_text(re.compile(r"Перші 20 із \d+ рядків clean\.csv"))     # the sample has a few deliberately bad rows, so not all 300 are valid
    assert page.evaluate("window.__i18nMissing") == []
