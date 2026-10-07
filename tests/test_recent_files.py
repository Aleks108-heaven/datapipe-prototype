"""The files a person chose from anywhere: remembered between runs of the app, newest first, capped, forgettable, never trusted blindly."""
import json
import shutil
import threading

import pytest

from conftest import EX
from datapipe.webui import make_server
from datapipe.webui.runner import MAX_PICKED, RECENT_FILE, RunService
from test_webui_run import TOKEN, Client


@pytest.fixture
def world(tmp_path):
    """A work folder, one listed data folder, and a place elsewhere where the person keeps files."""
    w = type("World", (), {})()
    w.work, w.data, w.elsewhere = tmp_path / "work", tmp_path / "data", tmp_path / "Downloads"
    w.data.mkdir()
    w.elsewhere.mkdir()
    shutil.copy(EX / "sales.csv", w.data)

    def make(name, folder=None):
        path = (folder or w.elsewhere) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(EX / "sales.csv", path)
        return path

    def service():                                   # a fresh RunService on the same work folder = the app started again
        return RunService(w.work, data_dirs=[w.data], chooser=lambda: None)

    def choose(svc, path):
        return svc.add_file({"path": str(path)})

    def names(svc):
        return [f["name"] for f in svc.options()["files"] if f["chosen"]]
    w.make, w.service, w.choose, w.names = make, service, choose, names
    return w


def test_chosen_files_are_still_listed_after_the_app_is_restarted_newest_first(world):
    a, b, c = (world.make(n) for n in ("a.csv", "b.csv", "c.csv"))
    svc = world.service()
    for p in (a, b, c):
        world.choose(svc, p)
    assert world.names(svc) == ["c.csv", "b.csv", "a.csv"]
    again = world.service()                                          # restart
    assert world.names(again) == ["c.csv", "b.csv", "a.csv"]
    o = again.options()
    assert o["chosen_count"] == 3 and o["max_chosen"] == MAX_PICKED
    assert [f["name"] for f in o["files"] if not f["chosen"]] == ["sales.csv"]          # the listed folder is unchanged


def test_choosing_a_file_again_moves_it_to_the_front_and_the_list_is_capped(world):
    svc = world.service()
    paths = [world.make(f"f{i:02d}.csv") for i in range(MAX_PICKED + 5)]
    for p in paths:
        world.choose(svc, p)
    kept = world.names(world.service())
    assert len(kept) == MAX_PICKED and kept[0] == f"f{MAX_PICKED + 4:02d}.csv" and "f00.csv" not in kept and "f04.csv" not in kept
    world.choose(svc, paths[-MAX_PICKED])                            # the oldest one still kept, chosen again
    again = world.names(world.service())
    assert again[0] == paths[-MAX_PICKED].name and len(again) == MAX_PICKED


def test_the_saved_file_holds_only_paths_and_is_written_whole(world):
    a = world.make("a.csv")
    world.choose(world.service(), a)
    saved = world.work / RECENT_FILE
    assert json.loads(saved.read_text(encoding="utf-8")) == {"files": [str(a.resolve())]}
    assert [p.name for p in world.work.iterdir() if p.suffix == ".tmp"] == []          # no temporary file left behind


def test_a_file_that_is_gone_is_hidden_and_comes_back_when_it_does(world):
    a, b = world.make("a.csv"), world.make("b.csv")
    svc = world.service()
    world.choose(svc, a)
    world.choose(svc, b)
    a.rename(a.with_suffix(".away"))                                 # a USB stick that is not plugged in
    assert world.names(world.service()) == ["b.csv"]
    a.with_suffix(".away").rename(a)
    assert world.names(world.service()) == ["b.csv", "a.csv"]


@pytest.mark.parametrize("damaged", ["", "{nope", "[]", "null", '{"files": 5}', '{"files": "x"}', '{"other": []}',
                                     '{"files": [5, null, "", "relative.csv", "C:\\\\x\\\\y.txt", ["a"], {"p": 1}]}'])
def test_a_damaged_or_hand_edited_file_means_an_empty_list_not_an_error(world, damaged):
    world.work.mkdir(parents=True)
    (world.work / RECENT_FILE).write_text(damaged, encoding="utf-8")
    svc = world.service()
    assert world.names(svc) == [] and svc.options()["chosen_count"] == 0
    world.choose(svc, world.make("fresh.csv"))                      # and it still works afterwards
    assert world.names(world.service()) == ["fresh.csv"]


def test_a_saved_path_is_checked_again_like_any_other_file(world, tmp_path):
    secret = tmp_path / "secret.csv"
    secret.write_text("a\n1\n")
    link = world.elsewhere / "link.csv"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    (world.work).mkdir(parents=True)
    (world.work / RECENT_FILE).write_text(json.dumps({"files": [str(link), str(tmp_path), str(world.data / "sales.csv")]}), encoding="utf-8")
    svc = world.service()
    assert world.names(svc) == []                                    # a symlink and a folder are never listed; a listed-folder file is not doubled
    assert [f["name"] for f in svc.options()["files"]] == ["sales.csv"]


def test_a_file_the_app_would_refuse_is_not_remembered(world):
    proposal = world.elsewhere / "mapping-x.json"
    proposal.write_text(json.dumps({"proposal_sha256": "a" * 64}))
    (world.elsewhere / "notes.txt").write_text("x")
    svc = world.service()
    for path in (proposal, world.elsewhere / "notes.txt", world.elsewhere / "missing.csv", world.elsewhere):
        with pytest.raises(Exception):
            world.choose(svc, path)
    assert not (world.work / RECENT_FILE).exists() and world.names(world.service()) == []


def test_two_files_with_the_same_name_can_be_told_apart_without_showing_a_path(world):
    one, two = world.make("export.csv", world.elsewhere / "Bank"), world.make("export.csv", world.elsewhere / "Shop")
    svc = world.service()
    world.choose(svc, one)
    world.choose(svc, two)
    chosen = [f for f in svc.options()["files"] if f["chosen"]]
    assert [f["folder"] for f in chosen] == ["Shop", "Bank"] and len({f["id"] for f in chosen}) == 2
    assert str(world.elsewhere) not in json.dumps(svc.options()["files"])        # the name of the folder, never its path


def test_not_being_able_to_save_does_not_stop_choosing(world, monkeypatch):
    import datapipe.webui.runner as runner
    monkeypatch.setattr(runner.tempfile, "mkstemp", lambda **k: (_ for _ in ()).throw(PermissionError("read-only")))
    svc = world.service()
    assert world.choose(svc, world.make("a.csv"))["added"]["name"] == "a.csv"
    assert world.names(svc) == ["a.csv"] and world.names(world.service()) == []          # works now, simply not remembered


# ---------------------------------------------------------------- forgetting
@pytest.fixture
def app(world):
    server = make_server(world.work, port=0, token=TOKEN, data_dirs=[world.data])
    threading.Thread(target=lambda: server.serve_forever(0.05), daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def test_forgetting_empties_the_list_for_good_and_leaves_the_files_alone(world, app):
    c = Client(app).login()
    paths = [world.make(n) for n in ("a.csv", "b.csv")]
    for p in paths:
        assert c.json("POST", "/api/run/add-file", {"path": str(p)})[0] == 200
    assert c.json("GET", "/api/run/options")[1]["chosen_count"] == 2
    status, res = c.json("POST", "/api/run/forget-files", {})
    assert status == 200 and res == {"forgotten": 2}
    o = c.json("GET", "/api/run/options")[1]
    assert o["chosen_count"] == 0 and [f["name"] for f in o["files"]] == ["sales.csv"]
    assert all(p.is_file() for p in paths)                           # the person's files are untouched
    assert world.names(world.service()) == []                        # and a restart does not bring them back
    assert c.json("POST", "/api/run/forget-files", {})[1] == {"forgotten": 0}


def test_forgetting_needs_login_csrf_and_a_same_origin_request(world, app):
    c = Client(app).login()
    c.json("POST", "/api/run/add-file", {"path": str(world.make("a.csv"))})
    assert Client(app).req("POST", "/api/run/forget-files", {}, authed=False)[0] == 401
    assert c.req("POST", "/api/run/forget-files", {}, headers={"X-DataPipe-CSRF": "wrong"})[0] == 403
    assert c.req("POST", "/api/run/forget-files", {}, headers={"Origin": "http://evil.example"})[0] == 403
    assert c.req("GET", "/api/run/forget-files")[0] in (404, 405)
    assert c.json("GET", "/api/run/options")[1]["chosen_count"] == 1


# ---------------------------------------------------------------- the page
def test_the_page_shows_where_a_chosen_file_is_and_can_forget_it(world, app):
    pw = pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect
    app.runner._can_browse = True
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:
            pytest.skip(f"Chromium not available: {exc}")
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{app.port}/?t={TOKEN}&go=run")
        page.wait_for_selector("#run-file")
        expect(page.locator("#add-files summary")).to_be_visible()
        page.locator("#add-files summary").click()
        expect(page.locator("#run-forget")).to_be_disabled()                       # nothing chosen yet, nothing to forget
        page.locator("#run-path-box summary").click()
        page.locator("#run-path").fill(str(world.make("export.csv", world.elsewhere / "Bank")))
        page.locator("#run-path-go").click()
        expect(page.locator("#run-file option:checked")).to_contain_text("export.csv")
        expect(page.locator("#run-file option:checked")).to_contain_text("chosen by you · Bank")
        page.reload()                                                              # the list is rebuilt from the server: still there
        page.wait_for_selector("#run-file")
        assert page.locator("#run-file option", has_text="chosen by you · Bank").count() == 1
        page.locator("#add-files summary").click()
        expect(page.locator("#add-files")).to_contain_text("remembers the last 20")
        expect(page.locator("#run-forget")).to_be_enabled()
        page.locator("#run-forget").click()
        expect(page.locator("#run-msg")).to_contain_text("Forgot 1 chosen file(s)")
        assert page.locator("#run-file option", has_text="chosen by you").count() == 0
        assert page.evaluate("document.documentElement.scrollWidth - innerWidth") <= 1
        browser.close()
        assert errors == []
