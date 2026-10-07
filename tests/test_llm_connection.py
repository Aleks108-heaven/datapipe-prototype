"""The saved model-server connection: validation, the per-user file, the "is it there, which models?" check against fake
servers, the confirmation a remote server needs, the map command's use of it, and the Settings page."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import SCHEMA
from datapipe.cli import main
from datapipe.llm import connection, keystore
from datapipe.webui import make_server
from test_llm_key import KEY
from test_mapping import RENAMED
from test_webui_run import Client, TOKEN


# ---------------------------------------------------------------- a fake model server
class FakeServer:
    """Answers GET /v1/models the way the test says. `mode` picks the behaviour; `seen` records the Authorization header."""
    def __init__(self):
        self.mode, self.seen, owner = "ok", [], self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                owner.seen.append(self.headers.get("Authorization"))
                m = owner.mode
                if m == "slow":
                    time.sleep(1.5)
                if m in ("401", "403", "404", "500"):
                    body = b'{"error": "SECRET-BODY-TEXT"}'
                    self.send_response(int(m))
                elif m == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:1/steal")
                    body = b""
                else:
                    body = {"ok": b'{"data": [{"id": "llama3.2"}, {"id": "qwen2.5:7b"}, {"id": "llama3.2"}]}',
                            "ollama": b'{"models": [{"name": "mistral:latest"}]}',
                            "empty": b'{"data": []}', "html": b"<html>hello</html>", "slow": b'{"data": []}',
                            "weird": b'{"data": [5, null, {"x": 1}, {"id": "a\\nb"}, "plain"]}'}[m]
                    self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    s = FakeServer()
    yield s
    s.close()


# ---------------------------------------------------------------- validation and the file
@pytest.mark.parametrize("bad", [None, 5, "", "   ", "127.0.0.1:11434/v1", "localhost:11434", "ftp://127.0.0.1/v1", "file:///etc/passwd",
                                 "http://api.example.com/v1", "https://", "http://127.0.0.1:99999/v1", "http://127.0.0.1:abc/v1",
                                 "https://user:pw@api.example.com/v1", "https://api.example.com/v1?x=1", "https://api.example.com/v1#f",
                                 "https://api.example.com/v 1", "https://127.0.0.1@evil.example/v1", "http://" + "a" * 400])
def test_addresses_that_are_not_safe_or_not_addresses_are_refused_with_a_reason(bad):
    with pytest.raises(ValueError) as exc:
        connection.check_url(bad)
    assert str(exc.value)


@pytest.mark.parametrize("good,expected", [("http://127.0.0.1:11434/v1/", "http://127.0.0.1:11434/v1"), ("  http://localhost:1234/v1  ", "http://localhost:1234/v1"),
                                           ("http://[::1]:8080/v1", "http://[::1]:8080/v1"), ("https://api.example.com/v1", "https://api.example.com/v1")])
def test_good_addresses_are_normalised(good, expected):
    assert connection.check_url(good) == expected


def test_model_names():
    assert connection.check_model(None) is None and connection.check_model("   ") is None
    assert connection.check_model(" llama3.2:latest ") == "llama3.2:latest"
    for bad in (5, ["a"], "two words", "a\nb", "x" * 201):
        with pytest.raises(ValueError):
            connection.check_model(bad)


def test_save_load_clear_roundtrip_in_the_per_user_folder_next_to_but_apart_from_the_key(tmp_path):
    assert connection.load() == {"base_url": None, "model": None}
    keystore.save_key(KEY)
    connection.save("http://127.0.0.1:11434/v1/", " llama3.2 ")
    assert connection.load() == {"base_url": "http://127.0.0.1:11434/v1", "model": "llama3.2"}
    assert connection.path() == tmp_path / "user-config" / "llm-connection.json"
    assert KEY not in connection.path().read_text() and "llama3.2" not in keystore.key_path().read_text()
    assert sorted(p.name for p in connection.path().parent.iterdir()) == ["llm-connection.json", "llm.json"]       # no temp files left
    connection.clear()
    connection.clear()
    assert connection.load() == {"base_url": None, "model": None} and keystore.load_key() == KEY


@pytest.mark.parametrize("damaged", ["{nope", "[]", "", '{"base_url": 5}', '{"base_url": "http://evil.example/v1"}', '{"model": "x"}',
                                     '{"base_url": "http://127.0.0.1:1/v1", "model": 7}'])
def test_a_damaged_or_hand_edited_file_means_nothing_is_saved(damaged):
    connection.path().parent.mkdir(parents=True, exist_ok=True)
    connection.path().write_text(damaged, encoding="utf-8")
    assert connection.load() == {"base_url": None, "model": None}


# ---------------------------------------------------------------- asking the server
def test_a_working_server_lists_its_models_once_each(fake):
    r = connection.list_models(fake.url)
    assert r["state"] == "ok" and r["models"] == ["llama3.2", "qwen2.5:7b"] and r["locality"] == "local" and r["ms"] >= 0
    assert "2 models" in r["message"]


def test_the_ollama_native_list_shape_is_understood_too(fake):
    fake.mode = "ollama"
    assert connection.list_models(fake.url)["models"] == ["mistral:latest"]


def test_odd_entries_in_the_list_are_skipped_or_cleaned(fake):
    fake.mode = "weird"
    assert connection.list_models(fake.url)["models"] == ["a b", "plain"]


@pytest.mark.parametrize("mode,state", [("empty", "no_models"), ("html", "bad_response"), ("401", "needs_key"), ("403", "needs_key"),
                                        ("404", "not_found"), ("500", "http_error"), ("redirect", "http_error")])
def test_each_failure_gets_its_own_state_and_never_repeats_what_the_server_said(fake, mode, state):
    fake.mode = mode
    r = connection.list_models(fake.url)
    assert r["state"] == state and r["models"] == [] and r["message"]
    assert "SECRET-BODY-TEXT" not in json.dumps(r) and "steal" not in json.dumps(r)


def test_a_closed_port_says_nothing_is_listening(fake):
    fake.close()
    r = connection.list_models(fake.url)
    assert r["state"] == "unreachable" and "running" in r["message"]


def test_a_server_that_does_not_answer_in_time_is_reported_not_waited_for(fake, monkeypatch):
    monkeypatch.setattr(connection, "CHECK_TIMEOUT", 0.3)
    fake.mode = "slow"
    t0 = time.monotonic()
    assert connection.list_models(fake.url)["state"] == "timeout"
    assert time.monotonic() - t0 < 1.4


def test_the_saved_key_goes_in_the_header_and_never_in_the_result(fake):
    connection.list_models(fake.url)
    keystore.save_key(KEY)
    r = connection.list_models(fake.url)
    assert fake.seen == [None, "Bearer " + KEY] and KEY not in json.dumps(r)


def test_a_cloud_model_behind_a_local_address_is_reported_as_remote(fake):
    assert connection.list_models(fake.url, "gpt-oss:120b-cloud")["locality"] == "cloud"
    assert connection.describe({"base_url": fake.url, "model": "x"})["locality"] == "local"
    assert connection.describe({"base_url": "https://api.example.com/v1", "model": "x"}) == {
        "base_url": "https://api.example.com/v1", "model": "x", "locality": "cloud", "on_this_machine": False}
    assert connection.describe({"base_url": None, "model": None})["locality"] is None


# ---------------------------------------------------------------- the app
@pytest.fixture
def app(tmp_path):
    server = make_server(tmp_path / "work", port=0, token=TOKEN, data_dirs=[])
    threading.Thread(target=lambda: server.serve_forever(0.05), daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def test_settings_carry_the_saved_connection_and_the_presets(app):
    c = Client(app).login()
    status, body = c.json("GET", "/api/settings")
    assert status == 200 and body["llm"]["connection"]["base_url"] is None
    assert [p["id"] for p in body["llm"]["presets"]] == ["ollama", "lmstudio", "llamacpp"]
    assert all(p["base_url"].startswith("http://127.0.0.1:") for p in body["llm"]["presets"])
    c.json("POST", "/api/settings/llm", {"base_url": "http://127.0.0.1:1234/v1", "model": "m"})
    assert c.json("GET", "/api/settings")[1]["llm"]["connection"] == {"base_url": "http://127.0.0.1:1234/v1", "model": "m",
                                                                      "locality": "local", "on_this_machine": True}


def test_a_local_connection_is_saved_straight_away_and_can_be_forgotten(app):
    c = Client(app).login()
    status, res = c.json("POST", "/api/settings/llm", {"base_url": "http://localhost:11434/v1", "model": "llama3.2"})
    assert status == 200 and res["saved"] is True and res["connection"]["locality"] == "local"
    assert connection.load()["model"] == "llama3.2"
    status, res = c.json("POST", "/api/settings/llm/clear", {})
    assert status == 200 and res["connection"]["base_url"] is None and connection.load()["base_url"] is None


def test_a_remote_server_or_cloud_model_is_saved_only_after_an_explicit_yes(app):
    c = Client(app).login()
    for body in ({"base_url": "https://api.example.com/v1", "model": "m"}, {"base_url": "http://127.0.0.1:11434/v1", "model": "gpt-oss:120b-cloud"}):
        for confirm in (None, False, "yes", 1):
            status, res = c.json("POST", "/api/settings/llm", {**body, "confirm_remote": confirm})
            assert status == 200 and res["saved"] is False and res["needs_confirmation"] is True and res["connection"]["locality"] == "cloud"
            assert connection.load()["base_url"] is None
        status, res = c.json("POST", "/api/settings/llm", {**body, "confirm_remote": True})
        assert res["saved"] is True and connection.load()["base_url"] == body["base_url"]
        connection.clear()


@pytest.mark.parametrize("bad", [{}, [], "x", {"base_url": "api.example.com"}, {"base_url": "http://api.example.com/v1"},
                                 {"base_url": "http://127.0.0.1:1/v1", "model": "two words"}, {"base_url": "http://127.0.0.1:1/v1", "model": 5}])
def test_bad_connections_are_refused_with_a_reason_and_nothing_is_saved(app, bad):
    c = Client(app).login()
    for path in ("/api/settings/llm", "/api/settings/llm/check"):
        status, body = c.json("POST", path, bad)
        assert status == 400 and body["error"], (path, bad)
    assert connection.load()["base_url"] is None


def test_the_check_endpoint_reports_state_models_and_where_the_data_would_go(app, fake):
    c = Client(app).login()
    status, r = c.json("POST", "/api/settings/llm/check", {"base_url": fake.url, "model": ""})
    assert status == 200 and r["state"] == "ok" and r["models"] == ["llama3.2", "qwen2.5:7b"] and r["locality"] == "local"
    fake.mode = "401"
    assert c.json("POST", "/api/settings/llm/check", {"base_url": fake.url})[1]["state"] == "needs_key"
    assert connection.load()["base_url"] is None                                                # checking never saves


def test_the_connection_endpoints_need_login_csrf_and_a_same_origin_request(app, fake):
    body = {"base_url": fake.url, "model": "m"}
    for path in ("/api/settings/llm", "/api/settings/llm/check", "/api/settings/llm/clear"):
        assert Client(app).req("POST", path, body, authed=False)[0] == 401
        c = Client(app).login()
        assert c.req("POST", path, body, headers={"X-DataPipe-CSRF": "wrong"})[0] == 403
        assert c.req("POST", path, body, headers={"Origin": "http://evil.example"})[0] == 403
        assert c.req("GET", path)[0] in (404, 405)
    assert connection.load()["base_url"] is None and fake.seen == []                             # nothing reached the model server


# ---------------------------------------------------------------- the map command uses it
def _dry_run(wd, capsys, *extra):
    code = main(["--workdir", str(wd), "map", str(RENAMED), "--schema", str(SCHEMA), "--policy", "business",
                 "--provider", "openai-compat", "--dry-run", *extra])
    return code, capsys.readouterr()


def test_map_uses_the_saved_connection_when_given_no_address_or_model(wd, capsys):
    connection.save("http://127.0.0.1:11434/v1", "llama3.2")
    code, out = _dry_run(wd, capsys)
    assert code == 0 and "egress mode: local" in out.out and "provider: openai-compat" in out.out


def test_a_saved_remote_connection_is_cloud_egress_and_needs_the_key(wd, capsys):
    connection.save("https://api.example.com/v1", "m")
    code, out = _dry_run(wd, capsys)
    assert code == 1 and "API key" in out.err                                                   # refused before anything is built
    keystore.save_key(KEY)
    code, out = _dry_run(wd, capsys)
    assert code == 0 and "egress mode: shapes" in out.out


def test_explicit_options_and_the_environment_beat_the_saved_connection(wd, capsys, monkeypatch):
    connection.save("https://api.example.com/v1", "m")                                          # would need a key; the explicit local address does not
    code, out = _dry_run(wd, capsys, "--base-url", "http://127.0.0.1:1234/v1")
    assert code == 0 and "egress mode: local" in out.out
    connection.clear()
    connection.save("http://127.0.0.1:11434/v1", None)                                          # no model saved...
    code, out = _dry_run(wd, capsys)
    assert code == 1 and "no model configured" in out.err
    monkeypatch.setenv("DATAPIPE_LLM_MODEL", "from-env")                                        # ...the environment supplies one
    assert _dry_run(wd, capsys)[0] == 0


# ---------------------------------------------------------------- in a real browser
def test_the_settings_page_connection_card_in_the_browser(app, fake):
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
        expect(page.locator("#set-llm-saved")).to_contain_text("Nothing saved yet")
        expect(page.locator("#set-llm-url")).to_have_value("http://127.0.0.1:11434/v1")          # Ollama's address is pre-filled
        expect(page.locator("#set-llm-where")).to_contain_text("Runs on this computer")

        page.select_option("#set-llm-preset", "lmstudio")
        expect(page.locator("#set-llm-url")).to_have_value("http://127.0.0.1:1234/v1")
        page.locator("#set-llm-url").fill(fake.url)                                                # typing an address switches the preset to "other"
        expect(page.locator("#set-llm-preset")).to_have_value("other")

        page.locator("#set-llm-check").click()                                                     # a working server: models appear, the only choice is not guessed
        expect(page.locator("#set-llm-state")).to_contain_text("2 models found")
        assert page.locator("#set-llm-models option").count() == 2
        expect(page.locator("#set-llm-state")).to_contain_text("Click the Model box")
        page.locator("#set-llm-model").fill("nope")
        page.locator("#set-llm-check").click()
        expect(page.locator("#set-llm-state")).to_contain_text("is not one of them")

        fake.mode = "401"                                                                          # a server that wants a key says so in words
        page.locator("#set-llm-check").click()
        expect(page.locator("#set-llm-state")).to_contain_text("asked for a key")

        page.locator("#set-llm-model").fill("llama3.2")
        page.locator("#set-llm-save").click()
        expect(page.locator("#set-llm-msg")).to_have_text("Connection saved.")
        expect(page.locator("#set-llm-saved")).to_contain_text("model llama3.2")

        page.locator("#set-llm-url").fill("https://api.example.com/v1")                           # remote: warned, not saved until confirmed
        expect(page.locator("#set-llm-where")).to_contain_text("Remote server")
        page.locator("#set-llm-save").click()
        expect(page.locator("#set-llm-confirm")).to_contain_text("not a model on your computer")
        assert connection.load()["base_url"] == fake.url
        page.locator("#set-llm-confirm-no").click()
        assert connection.load()["base_url"] == fake.url
        page.locator("#set-llm-save").click()
        page.locator("#set-llm-confirm-yes").click()
        expect(page.locator("#set-llm-saved")).to_contain_text("https://api.example.com/v1")
        assert connection.load()["base_url"] == "https://api.example.com/v1"

        page.reload()                                                                              # survives a reload
        expect(page.locator("#set-llm-url")).to_have_value("https://api.example.com/v1")
        expect(page.locator("#set-llm-where")).to_contain_text("Remote server")
        page.locator("#set-llm-clear").click()
        expect(page.locator("#set-llm-msg")).to_have_text("Saved connection removed.")
        assert connection.load()["base_url"] is None
        assert page.evaluate("document.documentElement.scrollWidth - innerWidth") <= 1
        browser.close()
        assert errors == []
