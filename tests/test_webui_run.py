"""The 'Run a file' part of the app: choosing from lists only, one run at a time, safe downloads, a real browser run."""
import http.client
import json
import re
import shutil
import threading
import time

import pytest

from datapipe.webui import make_server
from conftest import EX

TOKEN = "run-token-abcdefghijklmnopqrstuvwxyz"


@pytest.fixture
def app(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(EX / "sales.csv", data)
    shutil.copy(EX / "schema_sales.json", data)
    shutil.copy(EX / "analysis_sales.json", data)
    (data / "notes.txt").write_text("not a data file")
    server = make_server(tmp_path / "work", port=0, token=TOKEN, data_dirs=[data])
    threading.Thread(target=lambda: server.serve_forever(0.05), daemon=True).start()
    server.data, server.work = data, tmp_path / "work"
    yield server
    server.shutdown()
    server.server_close()


class Client:
    def __init__(self, server):
        self.server, self.cookie, self.csrf = server, None, server.csrf

    def req(self, method, path, body=None, headers=None, authed=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.server.port}"}
        if authed and self.cookie:
            h["Cookie"] = f"dp_session={self.cookie}"
        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            h.update({"Content-Type": "application/json", "X-DataPipe-CSRF": self.csrf, "Content-Length": str(len(payload))})
        h.update(headers or {})
        conn.request(method, path, body=payload, headers=h)
        r = conn.getresponse()
        data = r.read()
        out = (r.status, {k.lower(): v for k, v in r.getheaders()}, data)
        conn.close()
        return out

    def login(self):
        status, headers, _ = self.req("GET", f"/?t={TOKEN}", authed=False)
        assert status == 303
        self.cookie = re.search(r"dp_session=([^;]+)", headers["set-cookie"]).group(1)
        return self

    def json(self, method, path, body=None):
        status, _, data = self.req(method, path, body)
        return status, json.loads(data)


def run_and_wait(c, options, **over):
    body = {"file": options["files"][0]["id"], "schema": options["schemas"][0]["id"], "analysis": options["analyses"][0]["id"],
            "policy": "low", "actor": "alice"}
    body.update(over)
    status, res = c.json("POST", "/api/run/start", body)
    assert status == 200, res
    for _ in range(300):
        status, st = c.json("GET", "/api/run/status")
        if st["state"] != "running":
            return st
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def test_lists_only_data_schemas_and_metrics_and_requires_login(app):
    c = Client(app)
    assert c.req("GET", "/api/run/options")[0] == 401
    c.login()
    status, o = c.json("GET", "/api/run/options")
    assert status == 200
    assert [f["name"] for f in o["files"]] == ["sales.csv"]                 # notes.txt and the JSON configs are not data files
    assert [f["name"] for f in o["schemas"]] == ["schema_sales.json"] and [f["name"] for f in o["analyses"]] == ["analysis_sales.json"]
    assert "_path" not in json.dumps(o) and str(app.data) not in json.dumps(o["files"])      # no server paths for files


def test_a_run_produces_results_that_can_be_read_and_downloaded(app):
    c = Client(app).login()
    _, o = c.json("GET", "/api/run/options")
    st = run_and_wait(c, o)
    assert st["state"] == "done"
    status, r = c.json("GET", f"/api/run/result?run={st['run_id']}")
    assert status == 200 and r["status"] in ("COMPLETED", "COMPLETED_WITH_WARNINGS") and r["counts"]["valid"] > 0
    assert "clean.csv" in r["files"] and r["metrics"]
    status, headers, body = c.req("GET", f"/api/run/download/{st['run_id']}/clean.csv")
    assert status == 200 and "attachment" in headers["content-disposition"] and body.startswith(b"order_id")
    assert headers["x-content-type-options"] == "nosniff"
    _, o2 = c.json("GET", "/api/run/options")
    assert o2["recent"][0]["run_id"] == st["run_id"]


@pytest.mark.parametrize("path", ["/api/run/download/../../audit.jsonl", "/api/run/download/20260101T000000Z-aaaaaa/../../audit.jsonl",
                                  "/api/run/download/20260101T000000Z-aaaaaa/secret.txt", "/api/run/download/x/clean.csv",
                                  "/api/run/result?run=../../etc", "/api/run/result?run=nope"])
def test_downloads_and_results_cannot_leave_the_run_folders(app, path):
    c = Client(app).login()
    assert c.req("GET", path)[0] in (400, 404)


def test_start_only_accepts_ids_from_the_lists_and_a_real_policy(app):
    c = Client(app).login()
    _, o = c.json("GET", "/api/run/options")
    good = {"file": o["files"][0]["id"], "schema": o["schemas"][0]["id"], "policy": "low", "actor": "alice"}
    for bad in ({"file": str(app.data / "sales.csv")}, {"file": "../sales.csv"}, {"schema": "0"}, {"policy": "none"},
                {"actor": "x" * 200}, {"analysis": "../../x"}):
        status, res = c.json("POST", "/api/run/start", {**good, **bad})
        assert status == 400, (bad, res)
    assert c.json("GET", "/api/run/status")[1]["state"] == "idle"


def test_start_needs_csrf_and_login(app):
    c = Client(app).login()
    _, o = c.json("GET", "/api/run/options")
    body = {"file": o["files"][0]["id"], "schema": o["schemas"][0]["id"], "policy": "low", "actor": "alice"}
    assert c.req("POST", "/api/run/start", body, headers={"X-DataPipe-CSRF": "wrong"})[0] == 403
    assert Client(app).req("POST", "/api/run/start", body)[0] == 401
    assert c.req("POST", "/api/run/start", body, headers={"Origin": "http://evil.example"})[0] == 403
    assert c.json("GET", "/api/run/status")[1]["state"] == "idle"


def test_only_one_run_at_a_time(app, monkeypatch):
    gate = threading.Event()
    import datapipe.webui.runner as runner
    real = runner.run_pipeline
    monkeypatch.setattr(runner, "run_pipeline", lambda *a, **k: (gate.wait(10), real(*a, **k))[1])
    c = Client(app).login()
    _, o = c.json("GET", "/api/run/options")
    body = {"file": o["files"][0]["id"], "schema": o["schemas"][0]["id"], "policy": "low", "actor": "alice"}
    assert c.json("POST", "/api/run/start", body)[0] == 200
    status, res = c.json("POST", "/api/run/start", body)
    assert status == 409 and "already in progress" in res["error"]
    assert c.json("GET", "/api/run/status")[1]["state"] == "running"
    gate.set()
    for _ in range(300):
        if c.json("GET", "/api/run/status")[1]["state"] != "running":
            break
        time.sleep(0.1)
    assert c.json("GET", "/api/run/status")[1]["state"] == "done"


def test_a_failing_run_is_reported_not_hidden(app):
    (app.data / "bad.csv").write_text("")
    c = Client(app).login()
    _, o = c.json("GET", "/api/run/options")
    bad = next(f for f in o["files"] if f["name"] == "bad.csv")
    st = run_and_wait(c, o, file=bad["id"])
    assert st["state"] == "done"
    _, r = c.json("GET", f"/api/run/result?run={st['run_id']}")
    assert r["status"] == "FAILED" and r["reasons"]


def test_draft_schema_writes_a_reviewable_file_and_lists_it(app):
    c = Client(app).login()
    _, o = c.json("GET", "/api/run/options")
    status, res = c.json("POST", "/api/run/draft-schema", {"file": o["files"][0]["id"]})
    assert status == 200 and res["columns"] > 0 and (app.work / "schemas" / "sales-DRAFT.json").is_file()
    assert "sales-DRAFT.json" in [s["name"] for s in c.json("GET", "/api/run/options")[1]["schemas"]]


def test_symlinked_files_are_never_listed(app, tmp_path):
    secret = tmp_path / "secret.csv"
    secret.write_text("a\n1\n")
    try:
        (app.data / "link.csv").symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    c = Client(app).login()
    assert "link.csv" not in [f["name"] for f in c.json("GET", "/api/run/options")[1]["files"]]


# ---------------------------------------------------------------- the real page
pw = pytest.importorskip("playwright.sync_api")


def test_run_a_file_in_the_browser(app):
    from playwright.sync_api import expect, sync_playwright
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:
            pytest.skip(f"Chromium not available: {exc}")
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{app.port}/?t={TOKEN}&go=run")
        expect(page.get_by_role("heading", name="Run a file")).to_be_visible()
        expect(page.locator("#run-go")).to_be_disabled()
        page.select_option("#run-file", index=1)
        page.select_option("#run-schema", index=1)
        page.select_option("#run-analysis", index=1)
        expect(page.locator("#run-go")).to_be_enabled()
        page.locator("#run-actor").fill("alice")
        page.select_option("#run-policy", "low")
        page.locator("#run-go").click()
        expect(page.locator("#run-summary")).to_be_visible(timeout=60000)
        expect(page.locator("#run-summary")).to_contain_text("rows read")
        assert page.locator("table.metric").count() >= 1
        with page.expect_download() as d:
            page.get_by_role("link", name="Cleaned data (clean.csv)").click()
        assert d.value.suggested_filename.endswith("-clean.csv")
        assert page.evaluate("document.documentElement.scrollWidth - innerWidth") <= 1
        page.get_by_role("link", name="Review mappings").click()
        expect(page.get_by_role("heading", name="Mapping proposals")).to_be_visible()
        browser.close()
        assert errors == []


# ---------------------------------------------------------------- does the schema fit the file? (before running)
def test_check_says_which_schema_fits_the_file_without_running_anything(app, tmp_path):
    shutil.copy(EX / "schema_buyers.json", app.data)                        # a second, unrelated schema
    c = Client(app).login()
    _, o = c.json("GET", "/api/run/options")
    sales = next(f for f in o["files"] if f["name"] == "sales.csv")
    ids = {s["name"]: s["id"] for s in o["schemas"]}
    status, r = c.json("POST", "/api/run/check", {"file": sales["id"], "schema": ids["schema_buyers.json"]})
    assert status == 200 and r["known"] and r["chosen"]["missing_required_count"] > 0         # wrong schema: said so
    assert r["best"]["name"] == "schema_sales.json" and r["best"]["missing_required_count"] == 0
    status, r = c.json("POST", "/api/run/check", {"file": sales["id"], "schema": ids["schema_sales.json"]})
    assert r["chosen"]["missing_required_count"] == 0 and r["chosen"]["matched"] == r["chosen"]["schema_columns"]
    assert c.json("GET", "/api/run/status")[1]["state"] == "idle"                               # nothing was started
    assert c.json("POST", "/api/run/check", {"file": "../x"})[0] == 400


def test_the_page_warns_about_a_wrong_schema_and_offers_the_right_one(app):
    from playwright.sync_api import expect, sync_playwright
    shutil.copy(EX / "schema_buyers.json", app.data)
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:
            pytest.skip(f"Chromium not available: {exc}")
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{app.port}/?t={TOKEN}&go=run")
        page.wait_for_selector("#run-file")
        page.select_option("#run-file", index=1)
        expect(page.locator("#run-schema")).not_to_have_value("")                 # nothing chosen yet: the fitting schema is picked
        expect(page.locator("#run-fit")).to_contain_text("fits the file")
        wrong = page.locator("#run-schema option", has_text="schema_buyers.json").get_attribute("value")
        page.select_option("#run-schema", wrong)
        expect(page.locator("#run-fit")).to_contain_text("does not fit this file")
        page.get_by_role("button", name="Use schema_sales.json instead (fits)").click()
        expect(page.locator("#run-fit")).to_contain_text("fits the file")
        browser.close()


# ---------------------------------------------------------------- adding your own files
def test_a_file_dropped_into_a_listed_folder_shows_up_after_a_refresh_and_folders_are_shown(app):
    c = Client(app).login()
    o = c.json("GET", "/api/run/options")[1]
    assert o["folders"] == [str(app.data.resolve())]
    (app.data / "new_export.csv").write_text("order_id,region\nA1,N\n")
    assert "new_export.csv" in [f["name"] for f in c.json("GET", "/api/run/options")[1]["files"]]


def test_the_app_command_also_watches_an_inbox_folder_in_the_work_folder(tmp_path):
    from argparse import Namespace
    from datapipe.cli import _data_dirs
    dirs = _data_dirs(Namespace(data_dir=[str(tmp_path / "mine")], workdir=str(tmp_path / "work")), True)
    assert dirs == [tmp_path / "mine", tmp_path / "work" / "inbox"] and (tmp_path / "work" / "inbox").is_dir()
    assert _data_dirs(Namespace(data_dir=[], workdir=str(tmp_path / "w2")), False)[0].is_dir()      # review: current folder, no inbox
    assert not (tmp_path / "w2" / "inbox").exists()

# ---------------------------------------------------------------- metrics as CSV
def test_metrics_download_as_csv_files_and_as_one_zip(app):
    import io
    import zipfile
    c = Client(app).login()
    _, o = c.json("GET", "/api/run/options")
    st = run_and_wait(c, o)
    _, r = c.json("GET", f"/api/run/result?run={st['run_id']}")
    name = next(iter(r["metrics"]))
    status, headers, body = c.req("GET", f"/api/run/metrics/{st['run_id']}/{name}.csv")
    assert status == 200 and "attachment" in headers["content-disposition"] and headers["content-type"].startswith("text/csv")
    assert body.startswith(b"\xef\xbb\xbf")                                   # byte-order mark: Excel reads non-ASCII text correctly
    import csv
    rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
    assert rows[0] == r["metrics"][name]["columns"] and len(rows) - 1 == r["metrics"][name]["total_rows"]
    status, headers, body = c.req("GET", f"/api/run/metrics/{st['run_id']}/all.zip")
    assert status == 200 and headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(body)).namelist()
    assert sorted(names) == sorted(f"{n}.csv" for n in r["metrics"])


@pytest.mark.parametrize("path", ["/api/run/metrics/20260101T000000Z-aaaaaa/x.csv", "/api/run/metrics/../x.csv",
                                  "/api/run/metrics/20260101T000000Z-aaaaaa/..%2f..%2faudit.csv", "/api/run/metrics/nope/all.zip"])
def test_metrics_downloads_cannot_leave_the_run_folders(app, path):
    assert Client(app).login().req("GET", path)[0] in (400, 404)


def test_csv_cells_that_a_spreadsheet_would_run_as_formulas_are_neutralised_but_numbers_are_exact():
    from datapipe.webui.runner import RunService
    body = RunService._csv_bytes({"columns": ["a", "b", "c"], "rows": [["=1+1", -5, "-12.50"], ["@x", 1.5, None], ["plain", 0, "+49 30"]]})
    assert body.decode("utf-8-sig").splitlines() == ["a,b,c", "'=1+1,-5,-12.50", "'@x,1.5,", "plain,0,+49 30"]


# ---------------------------------------------------------------- settings (the gear)
def test_settings_defaults_save_and_survive_a_restart(app):
    c = Client(app).login()
    status, d = c.json("GET", "/api/settings")
    assert status == 200 and d["settings"] == {"actor": "", "policy": "business", "max_file_mb": None, "max_memory_gb": None, "theme": "system"}
    assert d["info"]["workdir"] == str(app.work.resolve()) and "business" in d["info"]["policies"]
    status, res = c.json("POST", "/api/settings", {"actor": "  Ana   Silva ", "policy": "regulated", "theme": "dark", "max_file_mb": 50, "max_memory_gb": 8})
    assert status == 200 and res["settings"]["actor"] == "Ana Silva" and res["settings"]["max_memory_gb"] == 8
    assert json.loads((app.work / "settings.json").read_text())["policy"] == "regulated"
    from datapipe.webui.settings import SettingsStore
    assert SettingsStore(app.work).get()["theme"] == "dark"                 # a new process would read the same file
    _, o = c.json("GET", "/api/run/options")
    assert o["settings"]["policy"] == "regulated"


@pytest.mark.parametrize("bad", [{"policy": "none"}, {"theme": "neon"}, {"max_file_mb": 0}, {"max_file_mb": -5}, {"max_file_mb": "lots"},
                                 {"max_file_mb": True}, {"max_memory_gb": 0.01}, {"max_memory_gb": 99999}, {"actor": "x" * 81}, {"actor": 5}])
def test_settings_reject_nonsense_and_save_nothing(app, bad):
    c = Client(app).login()
    status, res = c.json("POST", "/api/settings", bad)
    assert status == 400 and res["error"]
    assert not (app.work / "settings.json").exists()


def test_settings_need_login_and_csrf_and_a_damaged_file_falls_back(app):
    c = Client(app).login()
    assert Client(app).req("GET", "/api/settings")[0] == 401
    assert c.req("POST", "/api/settings", {"theme": "dark"}, headers={"X-DataPipe-CSRF": "wrong"})[0] == 403
    app.work.mkdir(parents=True, exist_ok=True)
    (app.work / "settings.json").write_text('{"policy": "nonsense", "theme": "dark", "max_file_mb": "x"}')
    s = c.json("GET", "/api/settings")[1]["settings"]
    assert s["policy"] == "business" and s["theme"] == "dark" and s["max_file_mb"] is None       # bad keys ignored one by one
    (app.work / "settings.json").write_text("not json at all")
    assert c.json("GET", "/api/settings")[0] == 200


def test_the_chosen_theme_is_in_the_page_before_it_paints(app):
    c = Client(app).login()
    assert b'data-theme' not in c.req("GET", "/")[2].split(b"<style")[0]
    c.json("POST", "/api/settings", {"theme": "dark"})
    head = c.req("GET", "/")[2].split(b"<style")[0]
    assert b'<html lang="en" data-theme="dark">' in head and b'name="color-scheme" content="dark"' in head


def test_a_limit_saved_in_settings_really_limits_the_run(app):
    big = app.data / "big.csv"
    big.write_text("order_id,region\n" + "".join(f"ORD{i:07d},North\n" for i in range(120_000)))       # about 1.7 MB
    c = Client(app).login()
    c.json("POST", "/api/settings", {"max_file_mb": 1})
    _, o = c.json("GET", "/api/run/options")
    st = run_and_wait(c, o, file=next(f for f in o["files"] if f["name"] == "big.csv")["id"])
    _, r = c.json("GET", f"/api/run/result?run={st['run_id']}")
    assert r["status"] == "FAILED" and any("max-file-mb" in x for x in r["reasons"])
    assert json.loads((app.work / "runs" / st["run_id"] / "result.json").read_text())["policy"]["max_file_bytes"] == 1024 * 1024


def test_audit_check_endpoint_reports_the_chain(app):
    c = Client(app).login()
    assert c.json("GET", "/api/settings/audit")[1]["ok"] is True                 # no log yet: nothing wrong
    _, o = c.json("GET", "/api/run/options")
    run_and_wait(c, o)
    r = c.json("GET", "/api/settings/audit")[1]
    assert r["ok"] and r["records"] >= 2
    log = app.work / "audit.jsonl"
    log.write_text(log.read_text().replace("run_started", "run_edited", 1))
    assert c.json("GET", "/api/settings/audit")[1]["ok"] is False


def test_gear_settings_and_csv_buttons_in_the_browser(app):
    from playwright.sync_api import expect, sync_playwright
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:
            pytest.skip(f"Chromium not available: {exc}")
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{app.port}/?t={TOKEN}&go=run")
        page.get_by_role("link", name="Settings").click()
        expect(page.get_by_role("heading", name="Settings")).to_be_visible()
        page.locator("#set-actor").fill("Ana Silva")
        page.select_option("#set-policy", "low")
        page.select_option("#set-theme", "dark")
        page.locator("#set-save").click()
        expect(page.locator("#set-msg")).to_contain_text("Saved")
        expect(page.locator("html")).to_have_attribute("data-theme", "dark")
        page.locator("#set-maxfile").fill("0")
        page.locator("#set-save").click()
        expect(page.locator("#set-msg")).to_contain_text("between")                       # the server's reason, shown to the user
        page.locator("#set-audit").click()
        expect(page.locator("#set-audit-msg")).to_contain_text("OK")
        page.reload()
        expect(page.locator("html")).to_have_attribute("data-theme", "dark")             # still dark after a reload (saved on the server)
        page.get_by_role("link", name="Run a file").click()
        expect(page.locator("#run-actor")).to_have_value("Ana Silva")
        expect(page.locator("#run-policy")).to_have_value("low")
        page.select_option("#run-file", index=1)
        page.select_option("#run-schema", index=1)
        page.select_option("#run-analysis", index=1)
        page.locator("#run-go").click()
        expect(page.locator("#run-summary")).to_be_visible(timeout=60000)
        with page.expect_download() as d:
            page.locator("#dl-metrics-zip").click()
        assert d.value.suggested_filename.endswith("-metrics.zip")
        with page.expect_download() as d2:
            page.get_by_role("link", name="Download this table (CSV)").first.click()
        assert d2.value.suggested_filename.endswith(".csv")
        assert page.evaluate("document.documentElement.scrollWidth - innerWidth") <= 1
        browser.close()
        assert errors == []

def test_desktop_nav_labels_keep_a_visible_space_between_the_words(app):
    """'Run' + 'a file' are two flex items; their space used to collapse and the nav read 'Runa file' / 'Reviewmappings'."""
    from playwright.sync_api import sync_playwright
    gap_js = """(id) => {
        const a = document.getElementById(id), span = a.querySelector('.navtext');
        const r = document.createRange(); r.selectNodeContents(a.firstChild);
        return span.getBoundingClientRect().left - r.getBoundingClientRect().right;
    }"""
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:
            pytest.skip(f"Chromium not available: {exc}")
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        page.goto(f"http://127.0.0.1:{app.port}/?t={TOKEN}&go=run")
        page.wait_for_selector("#nav-run")
        for nav_id in ("nav-run", "nav-review"):
            assert page.evaluate(gap_js, nav_id) >= 2, f"{nav_id}: no visible space between the words"
        page.set_viewport_size({"width": 390, "height": 844})                           # phones show the short labels only
        assert not page.locator("#nav-run .navtext").is_visible()
        browser.close()


# ---------------------------------------------------------------- level 2: built-in sample, standalone-program behaviour
def test_cli_sample_writes_a_reproducible_fake_file_with_deliberate_errors(tmp_path, capsys):
    from datapipe.cli import main
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    assert main(["sample", str(a), "--rows", "500"]) == 0 and main(["sample", str(b), "--rows", "500"]) == 0
    assert a.read_bytes() == b.read_bytes()                                         # same seed, same file
    text = a.read_text(encoding="utf-8")
    assert text.startswith("buyer_id,full_name,email,") and "example.com" in text and len(text.splitlines()) >= 500
    assert "wrote 500 rows" in capsys.readouterr().out


def test_the_moved_generator_gives_the_same_bytes_as_the_example_script(tmp_path):
    import subprocess
    import sys
    from conftest import ROOT
    script = tmp_path / "s.csv"
    subprocess.run([sys.executable, str(ROOT / "examples" / "make_synthetic_buyers.py"), str(script), "--rows", "300", "--seed", "7"], check=True, capture_output=True)
    from datapipe.sample import generate
    generate(tmp_path / "p.csv", rows=300, seed=7)
    assert script.read_bytes() == (tmp_path / "p.csv").read_bytes()


def test_double_click_arguments_create_the_folders_and_start_the_app(tmp_path, monkeypatch):
    from pathlib import Path
    from datapipe.cli import _double_click_args, build_parser
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    args = _double_click_args()
    assert (tmp_path / "datapipe" / "files").is_dir()
    parsed = build_parser().parse_args(args)
    assert parsed.cmd == "app" and parsed.workdir == str(tmp_path / "datapipe" / "work") and parsed.data_dir == [str(tmp_path / "datapipe" / "files")]


def test_bundled_examples_folder_is_used_inside_the_standalone_program(tmp_path, monkeypatch):
    import sys
    from datapipe.cli import _examples_dir
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert _examples_dir() == tmp_path / "examples"
    monkeypatch.delattr(sys, "_MEIPASS")
    assert _examples_dir().name == "examples"


def test_the_sample_endpoint_creates_numbered_files_the_app_can_list_and_run(app):
    shutil.copy(EX / "schema_buyers.json", app.data)
    shutil.copy(EX / "analysis_buyers.json", app.data)
    c = Client(app).login()
    status, first = c.json("POST", "/api/run/sample", {})
    assert status == 200 and first["name"] == "buyers_sample.csv" and first["rows"] > 10_000 and (app.data / "buyers_sample.csv").is_file()
    status, second = c.json("POST", "/api/run/sample", {})
    assert second["name"] == "buyers_sample-2.csv"                                   # never overwrites
    _, o = c.json("GET", "/api/run/options")
    assert {"buyers_sample.csv", "buyers_sample-2.csv"} <= {f["name"] for f in o["files"]}
    status, fit = c.json("POST", "/api/run/check", {"file": first["id"]})
    assert fit["best"]["name"] == "schema_buyers.json" and fit["best"]["missing_required_count"] == 0
    assert Client(app).req("POST", "/api/run/sample", {})[0] == 401


def test_sample_button_fills_the_form_and_picks_the_matching_schema_and_metrics(app):
    from playwright.sync_api import expect, sync_playwright
    shutil.copy(EX / "schema_buyers.json", app.data)
    shutil.copy(EX / "analysis_buyers.json", app.data)
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:
            pytest.skip(f"Chromium not available: {exc}")
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{app.port}/?t={TOKEN}&go=run")
        page.get_by_role("button", name="Create a fake sample file to try").click()
        expect(page.locator("#run-file")).to_have_value(re.compile(r".+"))
        expect(page.locator("#run-file option:checked")).to_contain_text("buyers_sample.csv")
        expect(page.locator("#run-schema option:checked")).to_contain_text("schema_buyers.json")
        expect(page.locator("#run-analysis option:checked")).to_contain_text("analysis_buyers.json")
        expect(page.locator("#run-go")).to_be_enabled()
        browser.close()

