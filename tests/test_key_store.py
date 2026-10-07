"""Where the API key is kept: the Windows Credential Manager when there is one, the file otherwise, never both, never lost."""
import json
import subprocess
import sys
import threading
import uuid

import pytest

from datapipe.llm import keystore
from datapipe.webui import make_server
from test_llm_key import KEY
from test_webui_run import TOKEN, Client


class FakeCredentialManager:
    """An in-memory stand-in for the Windows Credential Manager, so the logic is tested on every platform."""
    def __init__(self, monkeypatch):
        self.value, self.fail_writes, self.lose_writes = None, False, False
        monkeypatch.setattr(keystore, "_cm_available", lambda: True)
        monkeypatch.setattr(keystore, "_cm_read", self.read)
        monkeypatch.setattr(keystore, "_cm_write", self.write)
        monkeypatch.setattr(keystore, "_cm_delete", self.delete)

    def read(self):
        return self.value

    def write(self, key):
        if self.fail_writes:
            raise OSError(5, "refused")
        if not self.lose_writes:                       # a store that says yes and keeps nothing
            self.value = key

    def delete(self):
        self.value = None


@pytest.fixture
def cm(monkeypatch):
    return FakeCredentialManager(monkeypatch)


def files_holding(secret, folder):
    return [p for p in folder.rglob("*") if p.is_file() and secret in p.read_text(errors="ignore")] if folder.exists() else []


def test_a_saved_key_goes_to_the_credential_manager_and_no_file_holds_it(cm, tmp_path):
    keystore.save_key("  " + KEY + "\n")
    assert cm.value == KEY and keystore.load_key() == KEY
    assert not keystore.key_path().exists() and files_holding(KEY, tmp_path) == []
    assert keystore.status() == {"saved": True, "environment": False, "store": "credential-manager", "path": str(keystore.key_path())}


def test_saving_again_replaces_the_key_and_removes_an_old_plain_text_copy(cm):
    keystore.key_path().parent.mkdir(parents=True)
    keystore.key_path().write_text(json.dumps({"api_key": "old-key-in-a-file"}))
    keystore.save_key(KEY)
    assert cm.value == KEY and not keystore.key_path().exists()
    keystore.save_key("second-key")
    assert cm.value == "second-key" and keystore.load_key() == "second-key"


def test_a_key_in_the_old_file_moves_into_the_credential_manager_when_first_read(cm):
    keystore.key_path().parent.mkdir(parents=True)
    keystore.key_path().write_text(json.dumps({"api_key": KEY}))
    assert keystore.load_key() == KEY
    assert cm.value == KEY and not keystore.key_path().exists()
    assert keystore.load_key() == KEY                                             # and it keeps working from there


def test_the_old_file_is_kept_unless_the_move_is_proved(cm):
    keystore.key_path().parent.mkdir(parents=True)
    keystore.key_path().write_text(json.dumps({"api_key": KEY}))
    cm.lose_writes = True                                                          # "saved", but reading it back finds nothing
    assert keystore.load_key() == KEY and keystore.key_path().exists()
    cm.lose_writes, cm.fail_writes = False, True                                   # refused outright
    assert keystore.load_key() == KEY and keystore.key_path().exists() and cm.value is None


def test_when_the_credential_manager_refuses_the_key_is_saved_in_the_file_instead_of_being_lost(cm):
    cm.fail_writes = True
    keystore.save_key(KEY)
    assert cm.value is None and keystore.load_key() == KEY and keystore.status()["store"] == "file"
    cm.fail_writes = False                                                         # later it works again: the next read moves it
    assert keystore.load_key() == KEY and cm.value == KEY and not keystore.key_path().exists()


def test_removing_the_key_removes_it_from_both_places(cm):
    keystore.save_key(KEY)
    keystore.key_path().parent.mkdir(parents=True, exist_ok=True)
    keystore.key_path().write_text(json.dumps({"api_key": "stray-copy"}))
    keystore.clear_key()
    assert cm.value is None and not keystore.key_path().exists() and keystore.load_key() is None
    assert keystore.status()["saved"] is False
    keystore.clear_key()                                                           # removing twice is fine


def test_a_damaged_credential_manager_entry_means_no_key_not_a_crash(cm):
    cm.value = "two words"                                                         # not a valid key
    assert keystore.load_key() is None
    cm.value = "x" * 600
    assert keystore.load_key() is None


def test_the_environment_variable_still_takes_priority_over_the_saved_key(cm, monkeypatch):
    from datapipe.llm.providers import OpenAICompatProvider
    keystore.save_key("saved-key")
    monkeypatch.setenv(keystore.ENV_VAR, "env-key")
    assert OpenAICompatProvider(model="m").api_key == "env-key" and keystore.status()["environment"] is True


def test_the_file_store_can_be_chosen_and_other_systems_have_no_credential_manager(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv(keystore.STORE_ENV, "auto")
    assert keystore._cm_available() is True
    monkeypatch.setenv(keystore.STORE_ENV, "FILE")
    assert keystore._cm_available() is False
    monkeypatch.setenv(keystore.STORE_ENV, "auto")
    for other in ("linux", "darwin"):
        monkeypatch.setattr(sys, "platform", other)
        assert keystore._cm_available() is False


# ---------------------------------------------------------------- the real Windows Credential Manager
@pytest.mark.skipif(sys.platform != "win32", reason="the Windows Credential Manager")
def test_the_real_windows_credential_manager_holds_the_key_and_windows_itself_says_so(monkeypatch, tmp_path):
    target = f"datapipe-test-{uuid.uuid4().hex}"                                   # its own entry: the person's real one is never touched
    monkeypatch.setenv(keystore.TARGET_ENV, target)
    monkeypatch.setenv(keystore.STORE_ENV, "auto")

    def windows_lists_it():
        # cmdkey prints two lines ("Currently stored credentials for <name>:" and "* NONE *") for an entry that does not exist, and
        # more (target, type, user, persistence) for one that does; counting lines does not depend on the display language of Windows
        out = subprocess.run(["cmdkey", f"/list:{target}"], capture_output=True, text=True).stdout
        return len([line for line in out.splitlines() if line.strip()]) > 2
    try:
        assert keystore.load_key() is None and not windows_lists_it()
        keystore.save_key(KEY)
        assert windows_lists_it(), "Windows does not list the entry"
        assert keystore.load_key() == KEY and keystore.status()["store"] == "credential-manager"
        assert not keystore.key_path().exists() and files_holding(KEY, tmp_path) == []
        keystore.key_path().parent.mkdir(parents=True, exist_ok=True)              # an old file next to it is moved in and deleted
        keystore.clear_key()
        keystore.key_path().write_text(json.dumps({"api_key": "moved-key-123"}))
        assert keystore.load_key() == "moved-key-123" and windows_lists_it() and not keystore.key_path().exists()
        keystore.save_key("k" * 512)                                               # the longest allowed key fits
        assert keystore.load_key() == "k" * 512
        keystore.clear_key()
        assert not windows_lists_it() and keystore.load_key() is None
    finally:
        subprocess.run(["cmdkey", f"/delete:{target}"], capture_output=True)       # whatever happened, leave nothing behind


# ---------------------------------------------------------------- the page
@pytest.fixture
def app(tmp_path):
    server = make_server(tmp_path / "work", port=0, token=TOKEN, data_dirs=[])
    threading.Thread(target=lambda: server.serve_forever(0.05), daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def test_the_api_says_where_the_key_is_kept_and_still_never_sends_it(cm, app):
    c = Client(app).login()
    assert c.json("GET", "/api/settings")[1]["llm_key"] == {"saved": False, "environment": False, "store": "credential-manager", "path": str(keystore.key_path())}
    status, body = c.json("POST", "/api/settings/llm-key", {"key": KEY})
    assert status == 200 and body["llm_key"]["saved"] is True and body["llm_key"]["store"] == "credential-manager"
    for path in ("/api/settings", "/api/run/options", "/api/settings/audit"):
        assert KEY not in c.req("GET", path)[2].decode(), path
    assert KEY not in c.req("POST", "/api/settings/llm-key", {"key": KEY})[2].decode()
    status, body = c.json("POST", "/api/settings/llm-key/clear", {})
    assert body["llm_key"]["saved"] is False and cm.value is None


def test_the_settings_page_says_where_the_key_is_kept(app, monkeypatch, cm):
    pw = pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:
            pytest.skip(f"Chromium not available: {exc}")
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{app.port}/?t={TOKEN}&go=run")
        page.get_by_role("link", name="Settings").click()
        expect(page.locator("#set-llmkey-state")).to_contain_text("No key is saved. Stored in Windows Credential Manager.")
        expect(page.locator("#set-llmkey-where")).to_contain_text("encrypted for your Windows login")
        page.locator("#set-llmkey").fill(KEY)
        page.locator("#set-llmkey-save").click()
        expect(page.locator("#set-llmkey-msg")).to_have_text("Key saved.")
        expect(page.locator("#set-llmkey-state")).to_contain_text("A key is saved on this computer. Stored in Windows Credential Manager.")
        assert cm.value == KEY and KEY not in page.content() and KEY not in page.inner_text("body")
        monkeypatch.setattr(keystore, "_cm_available", lambda: False)             # where a file is used, the page says so and says it is plain text
        page.reload()
        expect(page.locator("#set-llmkey-state")).to_contain_text("File: ")
        expect(page.locator("#set-llmkey-where")).to_contain_text("plain text")
        assert page.evaluate("document.documentElement.scrollWidth - innerWidth") <= 1
        browser.close()
        assert errors == []
