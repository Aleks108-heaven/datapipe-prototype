"""The program can be stopped from its page, tells the page when it is gone, and (the Mac app, which has no window of its own) stops by
itself when its page is closed - but never during a run, and never because a page merely went quiet."""
import http.client
import json
import os
import re
import subprocess
import sys
import threading
import time

import pytest

from conftest import ROOT
from datapipe import cli
from datapipe.webui import make_server
from datapipe.webui.lifetime import PageWatch
from test_webui_run import TOKEN, Client


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


# ---------------------------------------------------------------- who has the page open
def test_nobody_has_opened_the_page_yet_the_browser_is_given_time():
    clock = Clock()
    watch = PageWatch(clock, first_page_within=600, grace=10, stale_after=1800)
    assert not watch.should_stop()
    clock.now += 599
    assert not watch.should_stop()
    clock.now += 2
    assert watch.should_stop()                                  # the browser never came: do not run for ever


def test_closing_the_only_page_stops_after_the_grace_but_a_reload_does_not():
    clock = Clock()
    watch = PageWatch(clock, first_page_within=600, grace=10, stale_after=1800)
    watch.alive("page-one")
    clock.now += 500
    assert not watch.should_stop()
    watch.bye("page-one")
    clock.now += 9
    assert not watch.should_stop()                              # a reload says goodbye and is back within moments
    watch.alive("page-two")                                     # ... and it is
    clock.now += 100
    assert not watch.should_stop() and watch.open_pages() == 1
    watch.bye("page-two")
    clock.now += 11
    assert watch.should_stop()


def test_a_second_tab_keeps_the_program_alive_when_the_first_is_closed():
    clock = Clock()
    watch = PageWatch(clock, first_page_within=600, grace=10, stale_after=1800)
    watch.alive("a1234567")
    watch.alive("b1234567")
    watch.bye("a1234567")
    clock.now += 60
    watch.alive("b1234567")
    assert not watch.should_stop() and watch.open_pages() == 1
    watch.bye("b1234567")
    watch.bye("b1234567")                                       # saying goodbye twice is harmless
    clock.now += 11
    assert watch.should_stop()


def test_a_page_that_went_quiet_is_given_up_on_only_after_a_long_time():
    clock = Clock()
    watch = PageWatch(clock, first_page_within=600, grace=10, stale_after=1800)
    watch.alive("sleepy12")
    clock.now += 1799                                           # a background tab, a laptop that was asleep
    assert not watch.should_stop() and watch.open_pages() == 1
    clock.now += 2
    assert not watch.should_stop()                              # just given up on: the grace starts now
    clock.now += 11
    assert watch.should_stop() and watch.open_pages() == 0


def test_work_in_progress_always_keeps_the_program_alive():
    clock = Clock()
    watch = PageWatch(clock, first_page_within=600, grace=10, stale_after=1800)
    watch.alive("page-one")
    watch.bye("page-one")
    clock.now += 10_000
    assert watch.should_stop() and not watch.should_stop(busy=True)


def test_the_watcher_thread_asks_with_the_busy_flag_and_stops_the_server_once(monkeypatch):
    answers = iter([False, False, True])
    asked = []

    class Watch:
        def should_stop(self, busy=False):
            asked.append(busy)
            return next(answers)

    class Server:
        watch, runner, stopped = Watch(), type("R", (), {"busy": staticmethod(lambda: True)})(), []

        def shutdown(self):
            self.stopped.append(True)

    server = Server()
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    cli._stop_when_closed(server)
    assert asked == [True, True, True] and server.stopped == [True]


def test_only_the_mac_app_stops_by_itself_without_being_asked(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/Applications/datapipe.app/Contents/MacOS/datapipe")
    assert cli._in_mac_app()
    monkeypatch.setattr(sys, "executable", "/usr/local/bin/datapipe")
    assert not cli._in_mac_app()
    monkeypatch.setattr(sys, "executable", "/Applications/datapipe.app/Contents/MacOS/datapipe")
    monkeypatch.setattr(sys, "platform", "win32")
    assert not cli._in_mac_app()
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert not cli._in_mac_app()


class FakeTk:
    """Stands in for tkinter: a root whose event loop runs until quit() is called, and which can replay what macOS would send."""

    def __init__(self, events=(), broken=False):
        self.events, self.broken, self.root = list(events), broken, None

    def Tk(self):
        if self.broken:
            raise RuntimeError("no window server")
        self.root = FakeRoot(self.events)
        return self.root


class FakeRoot:
    def __init__(self, events):
        self.commands, self.withdrawn, self.events, self._quit, self.after_calls = {}, False, events, threading.Event(), []

    def withdraw(self):
        self.withdrawn = True

    def createcommand(self, name, func):
        self.commands[name] = func

    def after(self, ms, func):
        self.after_calls.append(ms)
        func()

    def quit(self):
        self._quit.set()

    def mainloop(self):
        for name in self.events:                                  # what macOS sends while the app runs
            self.commands[name]()
        assert self._quit.wait(5), "the event loop was never ended"


class FakeServer:
    def __init__(self, ends_by_itself=False):
        self.ends_by_itself, self.ended, self.closed, self.shutdowns = ends_by_itself, threading.Event(), False, 0

    def serve_forever(self):
        if not self.ends_by_itself:
            assert self.ended.wait(5), "the server was never asked to stop"

    def shutdown(self):
        self.shutdowns += 1
        self.ended.set()

    def server_close(self):
        self.closed = True


def test_the_mac_app_listens_to_macos_and_quit_ends_the_server_too():
    tk, server = FakeTk(events=["tk::mac::Quit"]), FakeServer()
    assert cli._serve_in_mac_app(server, "http://127.0.0.1:1/?t=x", load_tk=lambda: tk) is True
    assert tk.root.withdrawn and set(tk.root.commands) == {"tk::mac::Quit", "tk::mac::ReopenApplication"}
    assert server.ended.is_set() and server.closed and server.shutdowns == 1


def test_when_the_server_ends_by_itself_the_event_loop_ends_too():
    tk, server = FakeTk(), FakeServer(ends_by_itself=True)                    # the Stop button, or the page was closed
    assert cli._serve_in_mac_app(server, "http://127.0.0.1:1/?t=x", load_tk=lambda: tk) is True
    assert tk.root.after_calls == [0] and server.closed


def test_opening_the_mac_app_again_shows_the_page_again(monkeypatch, capsys):
    import webbrowser
    opened = []
    monkeypatch.setattr(webbrowser, "open", opened.append)
    monkeypatch.delenv("DATAPIPE_NO_BROWSER", raising=False)
    tk, server = FakeTk(events=["tk::mac::ReopenApplication", "tk::mac::Quit"]), FakeServer()
    assert cli._serve_in_mac_app(server, "http://127.0.0.1:7/?t=secret&go=run", load_tk=lambda: tk)
    assert opened == ["http://127.0.0.1:7/?t=secret&go=run"]
    monkeypatch.setenv("DATAPIPE_NO_BROWSER", "1")                              # tests and CI: say so instead of opening a browser
    cli._show_again("http://127.0.0.1:7/?t=secret&go=run")
    assert opened == ["http://127.0.0.1:7/?t=secret&go=run"] and "opened again" in capsys.readouterr().err


def test_without_tk_or_a_window_server_the_mac_app_serves_the_plain_way():
    assert cli._serve_in_mac_app(FakeServer(), "x", load_tk=lambda: FakeTk(broken=True)) is False

    def no_tk():
        raise ImportError("no tkinter")
    assert cli._serve_in_mac_app(FakeServer(), "x", load_tk=no_tk) is False


def test_only_the_mac_app_ever_loads_tk():
    """Windows and Linux builds do not carry Tk, so nothing outside the Mac branch may import it."""
    import ast
    tree = ast.parse((ROOT / "datapipe" / "cli.py").read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert "tkinter" not in imported and "Tkinter" not in imported


def test_a_double_click_from_finder_may_add_a_process_number_and_is_still_a_double_click(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(cli, "_double_click_args", lambda: ["--workdir", str(tmp_path), "app", "--data-dir", str(tmp_path)])
    monkeypatch.setattr(cli, "_cmd_review", lambda args: seen.setdefault("cmd", args.cmd) and 0)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "argv", ["datapipe", "-psn_0_12345"])
    assert cli.main() == 0 and seen["cmd"] == "app"
    seen.clear()
    monkeypatch.setattr(sys, "argv", ["datapipe", "policies"])                      # a real argument is not a double click
    assert cli.main() == 0 and not seen


# ---------------------------------------------------------------- the endpoints
@pytest.fixture
def app(tmp_path):
    server = make_server(tmp_path / "work", port=0, token=TOKEN, data_dirs=[])
    thread = threading.Thread(target=lambda: server.serve_forever(0.05), daemon=True)
    thread.start()
    server.thread = thread
    yield server
    server.shutdown()
    server.server_close()


def test_the_page_endpoints_need_a_login_the_csrf_token_and_a_sane_id(app):
    c = Client(app)
    assert c.req("POST", "/api/page/alive", {"id": "abcdefgh"})[0] == 401
    c.login()
    assert c.req("POST", "/api/page/alive", {"id": "abcdefgh"}, headers={"X-DataPipe-CSRF": "wrong"})[0] == 403
    for bad in (None, "", "short", "has space!!", "x" * 65, 12345678, ["abcdefgh"], "abcdefgh\n"):
        assert c.json("POST", "/api/page/alive", {"id": bad})[0] == 400, bad
    assert c.json("POST", "/api/page/alive", [])[0] == 400
    assert app.watch.open_pages() == 0
    assert c.json("POST", "/api/page/alive", {"id": "abcdefgh"}) == (200, {"ok": True})
    assert app.watch.open_pages() == 1
    assert c.json("POST", "/api/page/bye", {"id": "abcdefgh"}) == (200, {"ok": True})
    assert app.watch.open_pages() == 0


def test_quit_needs_a_login_and_the_csrf_token_and_stops_the_server(app):
    c = Client(app)
    assert c.req("POST", "/api/quit", {})[0] == 401
    c.login()
    assert c.req("POST", "/api/quit", {}, headers={"X-DataPipe-CSRF": "wrong"})[0] == 403
    assert c.req("GET", "/api/quit")[0] == 404                                  # a link in a page can never stop it: only a POST with the token
    assert app.thread.is_alive()
    assert c.json("POST", "/api/quit", {}) == (200, {"ok": True})
    app.thread.join(5)
    assert not app.thread.is_alive()


def test_quit_is_refused_while_a_run_is_in_progress(app):
    c = Client(app).login()
    app.runner.busy = lambda: True
    status, body = c.json("POST", "/api/quit", {})
    assert status == 409 and "a run is in progress" in body["error"]
    assert app.thread.is_alive()


def test_busy_means_a_run_or_a_proposal_being_made(app):
    runner = app.runner
    assert not runner.busy()
    runner._job = {"state": "running", "started": time.time(), "file": "x.csv"}
    assert runner.busy()
    runner._job = {"state": "done"}
    assert not runner.busy()
    with runner._proposing:
        assert runner.busy()
    assert not runner.busy()


# ---------------------------------------------------------------- a real program
def test_a_real_app_process_stops_itself_after_its_page_is_closed_but_not_before(tmp_path):
    env = {**os.environ, "DATAPIPE_NO_BROWSER": "1", "DATAPIPE_CONFIG_DIR": str(tmp_path / "cfg"), "PYTHONUNBUFFERED": "1", "DATAPIPE_KEY_STORE": "file"}
    proc = subprocess.Popen([sys.executable, "-m", "datapipe", "--workdir", str(tmp_path / "work"), "app", "--port", "0", "--no-browser",
                             "--data-dir", str(tmp_path), "--exit-when-closed"], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        link = None
        for line in proc.stdout:
            found = re.search(r"(http://127\.0\.0\.1:(\d+)/\?t=\S+)", line)
            if found:
                link = found
                break
        assert link, "the program printed no link"
        port, token = int(link.group(2)), re.search(r"t=([^&\s]+)", link.group(1)).group(1)

        def call(method, path, body=None, cookie=None, csrf=None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            headers = {"Host": f"127.0.0.1:{port}"}
            if cookie:
                headers["Cookie"] = f"dp_session={cookie}"
            data = None
            if body is not None:
                data = json.dumps(body).encode()
                headers.update({"Content-Type": "application/json", "X-DataPipe-CSRF": csrf or "", "Content-Length": str(len(data))})
            conn.request(method, path, body=data, headers=headers)
            r = conn.getresponse()
            out = r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
            conn.close()
            return out

        status, headers, _ = call("GET", f"/?t={token}")
        cookie = re.search(r"dp_session=([^;]+)", headers["set-cookie"]).group(1)
        status, _, page = call("GET", "/", cookie=cookie)
        csrf = re.search(rb'name="csrf" content="([^"]+)"', page).group(1).decode()
        assert call("POST", "/api/page/alive", {"id": "realpage1"}, cookie, csrf)[0] == 200
        time.sleep(3)
        assert proc.poll() is None, "it stopped while a page was open"
        assert call("POST", "/api/page/bye", {"id": "realpage1"}, cookie, csrf)[0] == 200
        assert proc.wait(timeout=40) == 0, "it did not stop after its page was closed"
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()
        proc.wait()


# ---------------------------------------------------------------- in a browser
pw = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402


@pytest.fixture
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:
            pytest.skip(f"Chromium not available: {exc}")
        yield b
        b.close()


def settings_page(browser, app, **ctx):
    page = browser.new_context(**ctx).new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{app.port}/?t={TOKEN}&go=run")
    page.goto(f"http://127.0.0.1:{app.port}/#/settings")
    page.wait_for_selector("#set-stop")
    return page


def test_the_stop_button_asks_then_stops_the_program_and_says_so(browser, app):
    page = settings_page(browser, app)
    asked = []
    page.on("dialog", lambda d: (asked.append(d.message), d.accept()))
    page.locator("#set-stop").click()
    expect(page.locator("#stopped-card")).to_be_visible()
    assert "Stop datapipe now?" in asked[0]
    expect(page.locator("#stopped-card")).to_contain_text("You can close this tab")
    expect(page.locator("#lost")).to_be_hidden()
    app.thread.join(5)
    assert not app.thread.is_alive()
    assert page.errors == []


def test_declining_the_question_leaves_the_program_running(browser, app):
    page = settings_page(browser, app)
    page.on("dialog", lambda d: d.dismiss())
    page.locator("#set-stop").click()
    page.wait_for_timeout(300)
    assert app.thread.is_alive() and page.locator("#stopped-card").count() == 0


def test_a_run_in_progress_is_not_cut_off_by_the_stop_button(browser, app):
    page = settings_page(browser, app)
    app.runner.busy = lambda: True
    page.on("dialog", lambda d: d.accept())
    page.locator("#set-stop").click()
    expect(page.locator("#set-stop-msg")).to_contain_text("a run is in progress")
    assert app.thread.is_alive() and page.locator("#stopped-card").count() == 0


def test_the_stop_screen_and_button_in_ukrainian(browser, app):
    page = settings_page(browser, app, locale="uk-UA")
    expect(page.locator("#set-stop")).to_have_text("Зупинити datapipe")
    page.on("dialog", lambda d: d.accept())
    page.locator("#set-stop").click()
    expect(page.locator("#stopped-card")).to_contain_text("Цю вкладку можна закрити")
    assert page.evaluate("window.__i18nMissing") == []


def test_a_page_whose_program_has_gone_says_so_after_two_missed_checks(browser, app):
    page = browser.new_context().new_page()
    page.clock.install()
    page.goto(f"http://127.0.0.1:{app.port}/?t={TOKEN}&go=run")
    page.wait_for_selector("#app h2")
    expect(page.locator("#lost")).to_be_hidden()
    app.shutdown()
    app.server_close()
    page.clock.run_for(25_000)
    page.wait_for_timeout(300)
    expect(page.locator("#lost")).to_be_hidden()                                # one missed check is not yet a verdict
    page.clock.run_for(25_000)
    expect(page.locator("#lost")).to_be_visible()
    expect(page.locator("#lost")).to_contain_text("datapipe is no longer answering")
    assert page.locator("#lost").get_attribute("role") == "alert"


def test_leaving_the_page_tells_the_program_goodbye(browser, app):
    page = browser.new_context().new_page()
    page.goto(f"http://127.0.0.1:{app.port}/?t={TOKEN}&go=run")
    page.wait_for_selector("#app h2")
    deadline = time.time() + 5
    while app.watch.open_pages() != 1 and time.time() < deadline:
        time.sleep(0.05)
    assert app.watch.open_pages() == 1
    page.goto("about:blank")
    deadline = time.time() + 5
    while app.watch.open_pages() != 0 and time.time() < deadline:
        time.sleep(0.05)
    assert app.watch.open_pages() == 0
