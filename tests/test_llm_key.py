"""The saved LLM API key: a per-user file, write-only from the page, never echoed, never logged, never in the work folder."""
import json
import os
import re
import sys
import threading

import pytest

from datapipe.llm import keystore
from datapipe.llm.providers import OpenAICompatProvider, ProviderError
from datapipe.webui import make_server
from test_webui_run import Client, TOKEN

KEY = "lm-studio-SECRET-0123456789abcdef"


# ---------------------------------------------------------------- the key file
def test_save_load_and_clear_roundtrip_in_the_per_user_folder(tmp_path):
    assert keystore.load_key() is None
    keystore.save_key("  " + KEY + "\n")                                   # pasted with spaces/newline around it: trimmed
    assert keystore.load_key() == KEY
    assert keystore.key_path() == tmp_path / "user-config" / "llm.json"
    assert keystore.status() == {"saved": True, "environment": False, "path": str(keystore.key_path())}
    keystore.clear_key()
    assert keystore.load_key() is None and not keystore.key_path().exists()
    keystore.clear_key()                                                   # removing twice is fine


def test_a_replaced_key_leaves_no_temp_files_and_a_damaged_file_means_no_key(tmp_path):
    keystore.save_key("first-key")
    keystore.save_key(KEY)
    assert [p.name for p in keystore.key_path().parent.iterdir()] == ["llm.json"]
    for damaged in ("{not json", "[]", '{"api_key": 5}', '{"api_key": "has space"}', '{"other": "x"}', ""):
        keystore.key_path().write_text(damaged, encoding="utf-8")
        assert keystore.load_key() is None, damaged


@pytest.mark.parametrize("bad", ["", "   ", "two words", "line\nbreak", "tab\tinside", "ключ-кириллица", "café", "x" * 513, None, 12345, ["a"]])
def test_keys_that_could_break_an_http_header_are_refused_without_echoing_them(bad):
    with pytest.raises(ValueError) as exc:
        keystore.save_key(bad)
    if isinstance(bad, str) and bad.strip():
        assert bad not in str(exc.value)
    assert keystore.load_key() is None


def test_the_longest_allowed_key_is_accepted():
    keystore.save_key("k" * 512)
    assert keystore.load_key() == "k" * 512


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_the_key_file_is_readable_by_its_owner_only():
    keystore.save_key(KEY)
    assert keystore.key_path().stat().st_mode & 0o077 == 0


# ---------------------------------------------------------------- the provider uses it, in the right order
def test_the_saved_key_is_used_when_nothing_else_is_given_and_env_and_argument_win(monkeypatch):
    keystore.save_key("saved-key")
    assert OpenAICompatProvider(model="m").api_key == "saved-key"
    monkeypatch.setenv("DATAPIPE_LLM_API_KEY", "env-key")
    assert OpenAICompatProvider(model="m").api_key == "env-key"
    assert OpenAICompatProvider(model="m", api_key="arg-key").api_key == "arg-key"


def test_a_non_local_endpoint_is_accepted_with_a_saved_key_and_refused_without_one():
    with pytest.raises(ProviderError, match="Settings page"):
        OpenAICompatProvider(model="m", base_url="https://api.example.com/v1")
    keystore.save_key("saved-key")
    assert OpenAICompatProvider(model="m", base_url="https://api.example.com/v1").locality == "cloud"


@pytest.mark.parametrize("code", [401, 403])
def test_a_rejected_key_says_what_to_do_and_never_repeats_the_key(monkeypatch, code):
    import urllib.error
    keystore.save_key(KEY)
    prov = OpenAICompatProvider(model="m")

    def refuse(self, payload):
        raise urllib.error.HTTPError("http://127.0.0.1:1234/v1/chat/completions", code, "Unauthorized", {}, None)
    monkeypatch.setattr(OpenAICompatProvider, "_post", refuse)
    with pytest.raises(ProviderError) as exc:
        prov._complete("system", "user")
    assert f"HTTP {code}" in str(exc.value) and "Settings page" in str(exc.value) and KEY not in str(exc.value)


# ---------------------------------------------------------------- the app
@pytest.fixture
def app(tmp_path):
    server = make_server(tmp_path / "work", port=0, token=TOKEN, data_dirs=[])
    threading.Thread(target=lambda: server.serve_forever(0.05), daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def test_the_page_can_save_and_remove_a_key_but_is_never_sent_it_back(app, tmp_path):
    c = Client(app).login()
    status, body = c.json("GET", "/api/settings")
    assert status == 200 and body["llm_key"]["saved"] is False

    status, body = c.json("POST", "/api/settings/llm-key", {"key": KEY})
    assert status == 200 and body["llm_key"]["saved"] is True
    assert keystore.load_key() == KEY

    for path in ("/api/settings", "/api/run/options", "/api/settings/audit"):
        raw = c.req("GET", path)[2].decode()
        assert KEY not in raw, path
    assert KEY not in c.req("POST", "/api/settings/llm-key", {"key": KEY})[2].decode()
    status, body = c.json("POST", "/api/settings", {"policy": "low", "theme": "dark"})      # saving ordinary settings...
    assert status == 200 and keystore.load_key() == KEY                                      # ...does not touch the key
    assert KEY not in (tmp_path / "work" / "settings.json").read_text()                      # and it is never in the work folder
    assert not any(KEY in p.read_text(errors="ignore") for p in (tmp_path / "work").rglob("*") if p.is_file())

    status, body = c.json("POST", "/api/settings/llm-key/clear", {})
    assert status == 200 and body["llm_key"]["saved"] is False and keystore.load_key() is None


def test_the_environment_variable_is_reported_as_taking_priority(app, monkeypatch):
    c = Client(app).login()
    monkeypatch.setenv("DATAPIPE_LLM_API_KEY", "from-env-12345")
    status, body = c.json("GET", "/api/settings")
    assert body["llm_key"]["environment"] is True and "from-env-12345" not in json.dumps(body)


@pytest.mark.parametrize("bad", [{"key": ""}, {"key": "a b"}, {"key": "x" * 600}, {"key": 5}, {}, [], "text"])
def test_the_page_refuses_bad_keys_with_a_reason_and_saves_nothing(app, bad):
    c = Client(app).login()
    status, body = c.json("POST", "/api/settings/llm-key", bad)
    assert status == 400 and body["error"] and "x" * 100 not in body["error"]
    assert keystore.load_key() is None


def test_key_endpoints_need_login_and_csrf(app):
    anon = Client(app)
    assert anon.req("POST", "/api/settings/llm-key", {"key": KEY}, authed=False)[0] == 401
    c = Client(app).login()
    assert c.req("POST", "/api/settings/llm-key", {"key": KEY}, headers={"X-DataPipe-CSRF": "wrong"})[0] == 403
    assert c.req("POST", "/api/settings/llm-key/clear", {}, headers={"Origin": "http://evil.example"})[0] == 403
    assert c.req("GET", "/api/settings/llm-key")[0] == 404                      # there is no way to read it
    assert keystore.load_key() is None


# ---------------------------------------------------------------- in a real browser
def test_the_settings_page_key_field_in_the_browser(app):
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
        field = page.locator("#set-llmkey")
        expect(field).to_have_attribute("type", "password")                       # not shown on screen while typing
        expect(page.locator("#set-llmkey-state")).to_contain_text("No key is saved")
        expect(page.locator("#set-llmkey-clear")).to_be_disabled()

        field.fill("two words")                                                   # refused: the reason is shown and the text stays
        page.locator("#set-llmkey-save").click()
        expect(page.locator("#set-llmkey-msg")).to_contain_text("visible ASCII")
        expect(field).to_have_value("two words")
        assert keystore.load_key() is None

        field.fill(KEY)
        page.locator("#set-llmkey-save").click()
        expect(page.locator("#set-llmkey-msg")).to_have_text("Key saved.")
        expect(field).to_have_value("")                                           # the typed key is wiped from the field
        expect(page.locator("#set-llmkey-state")).to_contain_text("A key is saved")
        expect(page.locator("#set-llmkey-clear")).to_be_enabled()
        assert KEY not in page.content() and KEY not in page.inner_text("body")  # and it is nowhere in the page
        assert keystore.load_key() == KEY

        page.reload()                                                             # still saved after a reload; still not shown
        expect(page.locator("#set-llmkey-state")).to_contain_text("A key is saved")
        assert KEY not in page.content()

        page.locator("#set-llmkey-clear").click()
        expect(page.locator("#set-llmkey-msg")).to_have_text("Saved key removed.")
        expect(page.locator("#set-llmkey-state")).to_contain_text("No key is saved")
        assert page.evaluate("document.documentElement.scrollWidth - innerWidth") <= 1
        browser.close()
        assert errors == [] and keystore.load_key() is None
