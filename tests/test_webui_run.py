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
