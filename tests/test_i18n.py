"""Ukrainian interface: the table is complete and consistent with the page, real server messages are translated, data is never
translated, and every screen can be walked through in a real browser without an English sentence or a missing phrase."""
import ast
import json
import re
import shutil
import threading
from pathlib import Path

import pytest

from conftest import EX
from datapipe.webui import make_server, page as page_module
from datapipe.webui.i18n import LANGUAGES, TABLES, embedded
from datapipe.webui.i18n_uk import SERVER, UI, UK
from helpers import GOOD, m, make_proposal
from test_llm_connection import FakeServer, fake  # noqa: F401  (fake: the fixture that also keeps the proxy out of the way)
from test_webui_run import TOKEN, Client

CYRILLIC = re.compile("[А-Яа-яІіЇїЄєҐґ]")
PLACEHOLDER = re.compile(r"\{\d+\}")
NEEDS_REVIEW = GOOD[:4] + [m("Ordered On", "order_date", 0.6, "dates look similar"), m("Paid?", "paid")]
NO_PAID = GOOD[:5]


def page_keys():
    text = Path(page_module.__file__).read_text(encoding="utf-8")
    js = text[text.index('<script nonce="{{NONCE}}">'):]
    return {k.group(1).replace("\\'", "'") for k in re.finditer(r"\bt\(\s*'((?:[^'\\\n]|\\.)*)'", js)}


# ---------------------------------------------------------------- the table itself
def test_every_phrase_the_page_uses_has_a_translation_and_no_entry_is_stale():
    used = page_keys()
    missing, stale = sorted(used - set(UI)), sorted(set(UI) - used)
    assert not missing, "phrases in page.py without a Ukrainian entry: " + json.dumps(missing, ensure_ascii=False, indent=1)
    assert not stale, "Ukrainian entries the page no longer uses: " + json.dumps(stale, ensure_ascii=False, indent=1)


def test_no_key_is_listed_twice_and_the_two_parts_do_not_overlap():
    tree = ast.parse((Path(page_module.__file__).parent / "i18n_uk.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            dupes = sorted({k for k in keys if keys.count(k) > 1})
            assert not dupes, dupes
    assert not set(UI) & set(SERVER)


def test_placeholders_agree_and_every_value_is_really_ukrainian():
    for key, value in UK.items():
        assert sorted(PLACEHOLDER.findall(key)) == sorted(PLACEHOLDER.findall(value)), (key, value)
        assert CYRILLIC.search(value), f"not translated: {key!r}"
        assert value.strip() == value or key.strip() != key, f"stray space: {value!r}"      # a key with a space at the edge keeps it
        assert chr(0x2028) not in value and chr(0x2029) not in value


def test_every_server_phrase_still_exists_in_the_server_code():
    """A message that was reworded in the code must be reworded here too, or it silently stops being translated."""
    code = "\n".join(p.read_text(encoding="utf-8") for p in (Path(page_module.__file__).parents[1]).rglob("*.py") if "i18n" not in p.name)
    built_differently = {"Connected: {0} model found.": "Connected: %d model", "Connected: {0} models found.": "Connected: %d model",   # one code line, two sentences
                         "choose a file from the list": "choose a {what} from the list", "choose a schema from the list": "choose a {what} from the list",
                         "choose a metrics file from the list": "choose a {what} from the list",
                         "this proposal was already approved by {0}": "this proposal was already {", "this proposal was already rejected by {0}": "this proposal was already {",
                         "proposal was already approved by {0}": "proposal was already {", "proposal was already rejected by {0}": "proposal was already {",
                         "required target columns are not mapped: {0} (review the needs_review items, or fix the mapping by hand)": "required target columns are not mapped: {"}
    for key in SERVER:
        if key in built_differently:
            assert built_differently[key] in code, key
            continue
        probe = key.replace("the file-size limit (MB)", "{0}").replace("the memory limit (GB)", "{0}")      # the code builds these around a label
        pieces = [p for p in PLACEHOLDER.split(probe) if len(p) >= 12 and "\\" not in p]
        if not pieces:
            continue
        assert any(piece.strip()[:40] in code for piece in pieces), f"no longer in the code: {key!r}"


def test_the_table_embeds_safely_and_round_trips():
    text = embedded("uk")
    assert json.loads(text) == UK
    for bad in ("<", ">", "&", chr(0x2028), chr(0x2029)):
        assert bad not in text, repr(bad)
    assert not re.search(r"(?<!\\)/", text), "a slash must be escaped (a closing script tag cannot appear)"
    assert embedded("en") == "{}" and set(LANGUAGES) == {"system", "en", "uk"} and TABLES["uk"] == UK


def test_a_reviewer_name_cannot_inject_into_the_page_through_the_table_marker():
    text = page_module.render_page("N" * 16, "C" * 16, "{{I18N}}", "system", "uk")
    assert 'name="fixed-reviewer" content="{{I18N}}"' in text and text.count("var I18N = ") == 1
    assert "</script><" not in text.split("var I18N = ", 1)[1].split("\n", 1)[0]


# ---------------------------------------------------------------- the setting
@pytest.fixture
def app(tmp_path):
    work, data = tmp_path / "work", tmp_path / "data"
    data.mkdir()
    for name in ("sales.csv", "schema_sales.json", "analysis_sales.json"):
        shutil.copy(EX / name, data)
    shutil.copy(EX / "sales_renamed.csv", data / "sales_renamed.csv")
    shutil.copy(EX / "sales_renamed.csv", data / "sales_apr.csv")
    good, _ = make_proposal(work, NEEDS_REVIEW)
    from datapipe.ingest import read_source
    remap, _ = make_proposal(work, NO_PAID, tbl=read_source(EX / "sales_renamed.csv", max_bytes=10 ** 8), name="sales_apr.csv")
    from datapipe.ingest import parse_csv
    tricky = parse_csv("Include,Approved,Settings\n1,EU,5.00\n2,US,6.00\n")
    tricky.source_sha256 = "0" * 64
    named, _ = make_proposal(work, [m("Include", "order_id", 0.99), m("Approved", "region", 0.9), m("Settings", "amount", 0.99)],
                             tbl=tricky, name="Run a file.csv")
    server = make_server(work, port=0, token=TOKEN, data_dirs=[data])
    threading.Thread(target=lambda: server.serve_forever(0.05), daemon=True).start()
    server.data, server.work, server.named = data, work, named["proposal_sha256"]
    server.good, server.remap = good["proposal_sha256"], remap["proposal_sha256"]
    yield server
    server.shutdown()
    server.server_close()


def test_the_language_is_a_saved_setting_with_three_values(app):
    c = Client(app).login()
    assert c.json("GET", "/api/settings")[1]["settings"]["language"] == "system"
    for value in ("uk", "en", "system"):
        status, body = c.json("POST", "/api/settings", {"language": value})
        assert status == 200 and body["settings"]["language"] == value
        assert c.json("GET", "/api/settings")[1]["settings"]["language"] == value
    for bad in ("de", "UK", "", None, 5, ["uk"]):
        assert c.json("POST", "/api/settings", {"language": bad})[0] == 400, bad
    assert c.json("GET", "/api/settings")[1]["settings"]["language"] == "system"          # the refused values saved nothing


def test_the_page_carries_the_chosen_language_and_only_needs_the_table_for_it(app):
    c = Client(app).login()
    c.json("POST", "/api/settings", {"language": "en"})
    english = c.req("GET", "/")[2].decode()
    assert 'name="language" content="en"' in english and "var I18N = {};" in english and "Запуск" not in english
    c.json("POST", "/api/settings", {"language": "uk"})
    ukrainian = c.req("GET", "/")[2].decode()
    assert 'name="language" content="uk"' in ukrainian and "Запуск" in ukrainian
    c.json("POST", "/api/settings", {"language": "system"})
    assert 'name="language" content="system"' in c.req("GET", "/")[2].decode()


# ---------------------------------------------------------------- real messages from the server are translated
def collect_server_messages(app, tmp_path, fake):
    """Provoke real error / reason / hint messages through the API and the pipeline."""
    c = Client(app).login()
    out = []

    def err(method, path, body):
        status, doc = c.json(method, path, body)
        assert status >= 400 and "error" in doc, (path, body, status, doc)
        out.append(doc["error"])

    for bad in ({"language": "de"}, {"actor": "x" * 200}, {"policy": "nope"}, {"theme": "x"}, {"max_file_mb": "abc"}, {"max_file_mb": 0},
                {"max_memory_gb": "abc"}, {"max_memory_gb": 9999}, []):
        err("POST", "/api/settings", bad)
    for bad in ({"key": ""}, {"key": "a b"}, {"key": "x" * 600}, {"key": 5}):
        err("POST", "/api/settings/llm-key", bad)
    for bad in ({"base_url": ""}, {"base_url": "api.example.com"}, {"base_url": "http://api.example.com/v1"}, {"base_url": "http://127.0.0.1:99999/v1"},
                {"base_url": "http://u:p@127.0.0.1/v1"}, {"base_url": "http://127.0.0.1:1/v1", "model": "two words"}, {"base_url": "x" * 400}, {"base_url": "http://127.0.0.1:1/v1", "model": 5}, []):
        err("POST", "/api/settings/llm/check", bad)
    for mode in ("ok", "empty", "html", "401", "404", "500"):
        fake.mode = mode
        out.append(c.json("POST", "/api/settings/llm/check", {"base_url": fake.url})[1]["message"])
    fake.mode = "ok"
    fake.close()
    out.append(c.json("POST", "/api/settings/llm/check", {"base_url": fake.url})[1]["message"])               # nothing listening
    for bad in ("", "relative.csv", str(tmp_path), str(tmp_path / "missing.csv"), str(tmp_path / "notes.txt")):
        (tmp_path / "notes.txt").write_text("x")
        err("POST", "/api/run/add-file", {"path": bad})
    _, o = c.json("GET", "/api/run/options")
    err("POST", "/api/run/start", {"file": "../x", "schema": "0", "policy": "low", "actor": "a"})
    err("POST", "/api/run/start", {"file": o["files"][0]["id"], "schema": "0", "policy": "low", "actor": "a"})
    err("POST", "/api/run/start", {"file": o["files"][0]["id"], "schema": o["schemas"][0]["id"], "policy": "nope", "actor": "a"})
    err("POST", "/api/run/start", {"file": o["files"][0]["id"], "schema": o["schemas"][0]["id"], "policy": "low", "actor": "x" * 200})
    out.append(c.json("GET", "/api/run/result?run=nope")[1]["error"])
    err("POST", "/api/proposals/" + app.good + "/approve", {"reviewer": "", "note": ""})
    err("POST", "/api/proposals/" + app.good + "/approve", {"reviewer": "bob", "note": "", "include": ["order_date"]})
    err("POST", "/api/proposals/" + app.good + "/check", {"target": "region"})
    err("POST", "/api/proposals/" + app.good + "/approve", {"reviewer": "bob", "note": "x", "manual": "no"})
    err("POST", "/api/proposals/" + app.good + "/approve", {"reviewer": "bob", "note": "x" * 600})
    ok = {"file": o["files"][0]["id"], "schema": o["schemas"][0]["id"], "policy": "low", "actor": "a"}       # creating a proposal from the page
    err("POST", "/api/proposals", {**ok, "file": "../x"})
    err("POST", "/api/proposals", {**ok, "schema": "0"})
    err("POST", "/api/proposals", {**ok, "policy": ["low"]})
    err("POST", "/api/proposals", {**ok, "actor": "x" * 200})
    err("POST", "/api/proposals", [])
    (app.data / "no_rows.csv").write_text("")
    empty = next(f["id"] for f in c.json("GET", "/api/run/options")[1]["files"] if f["name"] == "no_rows.csv")
    err("POST", "/api/proposals", {**ok, "file": empty})                                                     # the core's own refusal, shown on the form
    assert app.runner._proposing.acquire(blocking=False)
    try:
        err("POST", "/api/proposals", ok)                                                                    # one at a time
    finally:
        app.runner._proposing.release()
    return c, out


def reasons_of_failed_runs(app, tmp_path):
    """Reasons and warnings that a finished run shows (they come from the pipeline, not from the web layer)."""
    c = Client(app).login()
    (app.data / "empty.csv").write_text("")
    (app.data / "headeronly.csv").write_text("order_id,region\n")
    (app.data / "wrong.csv").write_text("a,b\n1,2\n")
    out = []
    import time
    for name in ("empty.csv", "headeronly.csv", "wrong.csv"):
        _, o = c.json("GET", "/api/run/options")
        file = next(f for f in o["files"] if f["name"] == name)
        schema = next(s for s in o["schemas"] if s["name"] == "schema_sales.json")
        assert c.json("POST", "/api/run/start", {"file": file["id"], "schema": schema["id"], "policy": "business", "actor": "a"})[0] == 200
        for _ in range(100):
            st = c.json("GET", "/api/run/status")[1]
            if st["state"] != "running":
                break
            time.sleep(0.1)
        r = c.json("GET", f"/api/run/result?run={st['run_id']}")[1]
        out += r["reasons"] + r["warnings"]
    return out


def test_real_server_messages_are_translated_end_to_end(app, tmp_path, fake):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    c, messages = collect_server_messages(app, tmp_path, fake)
    messages += reasons_of_failed_runs(app, tmp_path)
    assert len(messages) > 40
    c.json("POST", "/api/settings", {"language": "uk"})
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:
            pytest.skip(f"Chromium not available: {exc}")
        page = browser.new_page()
        page.add_init_script("window.__i18nTest = true;")
        page.goto(f"http://127.0.0.1:{app.port}/?t={TOKEN}&go=run")
        page.wait_for_selector("#run-file")
        translated = page.evaluate("(msgs) => msgs.map(s => window.__i18n.tm(s))", messages)
        untranslated = sorted({m_ for m_, t_ in zip(messages, translated) if t_ == m_})
        assert not untranslated, "server messages with no Ukrainian entry:\n" + "\n".join(untranslated)
        for t_ in translated:
            assert CYRILLIC.search(t_) and not PLACEHOLDER.search(t_), t_
        # text that is not in the table (a file name, a column name) passes through unchanged, and so does a non-string
        assert page.evaluate("() => [window.__i18n.tm('sales.csv'), window.__i18n.tm('Ordered On'), window.__i18n.tm('')]") == ["sales.csv", "Ordered On", ""]
        browser.close()


# ---------------------------------------------------------------- the browser
ENGLISH = re.compile(r"\b(the|and|is|are|of|for|with|your|this|that|you|not|from|will|was|were|has|have)\b", re.I)
STRIP = ("<your-file>", "<analysis.json>", "C:\\Users\\you\\file.csv")      # placeholders and an example path inside a sentence


def check_screen(page, where):
    text = page.inner_text("body")
    for s in STRIP:
        text = text.replace(s, "")
    text = re.sub(r"[A-Za-z]:\\\S*", "", text)                      # a Windows path is data (a test folder may be called test_the_...)
    text = re.sub(r"(?<!\S)/\S*", "", text)                           # so is a Linux or macOS path (/tmp/pytest-of-runner/... contains the word "of")
    found = [text[max(0, hit.start() - 50):hit.end() + 30].replace("\n", " | ") for hit in ENGLISH.finditer(text)]
    assert not found, f"{where}: English words left on the page:\n" + "\n".join(found)
    assert page.evaluate("window.__i18nMissing") == [], f"{where}: phrases without a Ukrainian entry"
    assert page.evaluate("document.documentElement.lang") == "uk"


@pytest.fixture
def ukpage(app):
    pw = pytest.importorskip("playwright.sync_api")
    app.runner._can_browse = True            # as on a normal desktop: a CI runner has no file-window tool, which would open the typing box by default
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:
            pytest.skip(f"Chromium not available: {exc}")
        Client(app).login().json("POST", "/api/settings", {"language": "uk", "actor": "olena"})
        page = browser.new_page(viewport={"width": 1100, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        page.goto(f"http://127.0.0.1:{app.port}/?t={TOKEN}&go=run")
        page.wait_for_selector("#run-file")
        yield page
        browser.close()
        assert errors == []


def test_the_run_screen_in_ukrainian(ukpage, app):
    from playwright.sync_api import expect
    page = ukpage
    expect(page.get_by_role("heading", name="Запуск файлу")).to_be_visible()
    expect(page.get_by_role("link", name="Запуск файлу")).to_be_visible()
    expect(page.get_by_role("link", name="Перевірка зіставлень")).to_be_visible()
    expect(page.locator("#run-go")).to_have_text("Запустити")                       # the button says "Run", the menu says "Run (a file)"
    check_screen(page, "run, nothing chosen")
    expect(page.locator("#run-why")).to_contain_text("потрібні: файл даних та схема")
    page.select_option("#run-file", index=1)
    expect(page.locator("#run-fit")).to_contain_text("підходить до файлу")
    check_screen(page, "run, file and schema chosen")
    page.locator("#run-actor").fill("Олена")
    page.select_option("#run-policy", "low")
    expect(page.locator("#run-go")).to_be_enabled()
    expect(page.locator("#run-why")).to_have_text("")
    page.locator("#run-path-box summary").click()
    page.locator("#run-path").fill("nope.csv")
    page.locator("#run-path-go").click()
    expect(page.locator("#run-msg")).to_contain_text("вкажіть повний шлях")             # a server message, translated
    check_screen(page, "run, refused path")
    page.locator("#add-files summary").click()
    check_screen(page, "run, where the lists come from")
    page.locator("#run-go").click()
    expect(page.locator("#run-summary")).to_be_visible(timeout=60000)
    expect(page.locator("#run-summary")).to_contain_text("Прочитано рядків")
    assert page.locator("table.metric").count() >= 1
    check_screen(page, "run, result")
    page.get_by_role("link", name="Запуск файлу").click()
    expect(page.locator("#run-file")).to_be_visible()
    expect(page.get_by_text("Попередні запуски")).to_be_visible()
    check_screen(page, "run, earlier runs")


def test_the_memory_warning_in_ukrainian(ukpage, app, tmp_path):
    from playwright.sync_api import expect
    big = tmp_path / "big.csv"
    with open(big, "wb") as fh:
        fh.write(b"a,b\n" + b"1,2\n" * (21 * 1024 * 1024 // 4))
    Client(app).login().json("POST", "/api/settings", {"language": "uk", "max_memory_gb": 0.5})
    app.runner._can_browse = True
    page = ukpage
    page.reload()
    page.wait_for_selector("#run-file")
    page.locator("#run-path-box summary").click()
    page.locator("#run-path").fill(str(big))
    page.locator("#run-path-go").click()
    expect(page.locator("#run-size")).to_contain_text("імовірно, відхилять")
    check_screen(page, "run, memory warning")


def test_the_settings_screen_in_ukrainian(ukpage, app, fake):
    from playwright.sync_api import expect
    page = ukpage
    try:
        page.get_by_role("link", name="Налаштування").click()
        expect(page.get_by_role("heading", name="Налаштування")).to_be_visible()
        check_screen(page, "settings")
        page.locator("#set-llm-url").fill(fake.url)
        page.locator("#set-llm-check").click()
        expect(page.locator("#set-llm-state")).to_contain_text("Знайдено моделей: 2")
        expect(page.locator("#set-llm-state")).to_contain_text("відповів за")
        check_screen(page, "settings, models found")
        page.locator("#set-llm-model").fill("nope")
        page.locator("#set-llm-check").click()
        expect(page.locator("#set-llm-state")).to_contain_text("серед них немає")
        fake.mode = "401"
        page.locator("#set-llm-check").click()
        expect(page.locator("#set-llm-state")).to_contain_text("Сервер попросив ключ")
        fake.mode = "404"
        page.locator("#set-llm-check").click()
        expect(page.locator("#set-llm-state")).to_contain_text("Сервер відповів, але не за цією адресою")
        fake.close()
        page.locator("#set-llm-check").click()
        expect(page.locator("#set-llm-state")).to_contain_text("ніхто не слухає")
        page.locator("#set-llm-url").fill("api.example.com")
        page.locator("#set-llm-save").click()
        expect(page.locator("#set-llm-msg")).to_contain_text("почніть адресу з http://")
        page.locator("#set-llm-url").fill("https://api.example.com/v1")
        expect(page.locator("#set-llm-where")).to_contain_text("Віддалений сервер")
        page.locator("#set-llm-save").click()
        expect(page.locator("#set-llm-confirm")).to_contain_text("Це не модель на вашому комп’ютері")
        check_screen(page, "settings, remote confirmation")
        page.locator("#set-llm-confirm-no").click()
        page.locator("#set-llmkey").fill("two words")
        page.locator("#set-llmkey-save").click()
        expect(page.locator("#set-llmkey-msg")).to_contain_text("лише видимі символи ASCII")
        page.locator("#set-audit").click()
        expect(page.locator("#set-audit-msg")).to_contain_text("записів")
        page.locator("#set-maxmem").fill("0.1")
        page.locator("#set-save").click()
        expect(page.locator("#set-msg")).to_contain_text("ліміт пам’яті (ГБ) має бути від 0.5 до 1024")
        check_screen(page, "settings, messages")
    finally:
        fake.close()


def test_the_review_screens_in_ukrainian_and_data_is_never_translated(ukpage, app):
    from playwright.sync_api import expect
    page = ukpage
    page.get_by_role("link", name="Перевірка зіставлень").click()
    expect(page.get_by_role("heading", name="Пропозиції зіставлення")).to_be_visible()
    check_screen(page, "review list")
    page.goto(f"http://127.0.0.1:{app.port}/#/p/{app.good}")
    expect(page.get_by_role("button", name="Схвалити й створити схему")).to_be_visible()
    check_screen(page, "proposal, undecided")
    card = page.locator('[data-target="order_date"]')
    expect(card.locator("[data-role=undecided]")).to_contain_text("Ще не вирішено")
    card.get_by_role("radio", name="Включити").click()
    expect(page.locator("#why")).to_contain_text("Щоб схвалити")
    check_screen(page, "proposal, a decision made")
    names = page.locator("[data-role=source]").all_inner_texts()
    assert "Order No" in names
    page.locator("#more > summary").click()
    page.locator("#legend > summary").click()
    check_screen(page, "proposal, help open")
    page.locator("#reviewer").fill("Богдан")
    page.locator("#note").fill("перевірено")
    page.locator("#approve").click()
    expect(page.locator("#result")).to_contain_text("Схвалено. Схему створено")
    check_screen(page, "proposal, approved")
    page.get_by_role("button", name="Назад до пропозицій").click()
    expect(page.locator("body")).to_contain_text("Схвалено")
    # file and column names are data: they stay as they are even when they equal a phrase of the interface
    page.goto(f"http://127.0.0.1:{app.port}/#/p/{app.named}")
    expect(page.get_by_role("heading", name="Run a file.csv")).to_be_visible()
    assert sorted(page.locator("[data-role=source]").all_inner_texts()) == ["Approved", "Include", "Settings"]      # the cards come in the order of what needs a decision
    assert page.locator('[data-target="order_id"] [data-role=source]').inner_text() == "Include"


def test_a_manual_mapping_and_a_refused_attempt_in_ukrainian(ukpage, app):
    from playwright.sync_api import expect
    page = ukpage
    page.goto(f"http://127.0.0.1:{app.port}/#/p/{app.remap}")
    card = page.locator('[data-target="paid"]')
    expect(card).to_contain_text(re.compile("не зіставлен", re.I))
    sel = card.locator("select")
    page.locator("#reviewer").fill("Богдан")
    sel.select_option(label="Paid?")
    expect(card.locator(".badge.b-manual")).to_contain_text("Вручну (вами)")
    expect(page.locator("#why")).to_contain_text("додайте примітку")
    check_screen(page, "proposal, manual mapping")
    page.locator("#note").fill("так")
    page.locator("#approve").click()
    expect(page.locator("#result")).to_contain_text("Схвалено")
    page.goto(f"http://127.0.0.1:{app.port}/#/p/{app.remap}")
    page.reload()
    expect(page.locator(".banner.ok")).to_contain_text("Схвалено: Богдан")
    check_screen(page, "proposal, already decided")


def test_the_create_a_proposal_form_in_ukrainian(ukpage, app):
    from datapipe.sample import generate
    from playwright.sync_api import expect
    page = ukpage
    page.get_by_role("link", name="Перевірка зіставлень").click()
    expect(page.get_by_role("heading", name="Пропозиції зіставлення")).to_be_visible()
    page.locator("#prop-box > summary").click()                                                 # proposals exist, so the form is folded: one click
    expect(page.get_by_role("button", name="Створити пропозицію")).to_be_disabled()
    expect(page.locator("#prop-why")).to_have_text("Щоб створити пропозицію, потрібні: файл даних та схема.")
    expect(page.locator("#prop-actor")).to_have_value("olena")                                  # the name saved in Settings
    check_screen(page, "review list, create form")
    page.locator("#prop-cmd-box > summary").click()
    expect(page.locator("#prop-cmd")).to_contain_text("--workdir")
    check_screen(page, "review list, create form, command line")
    generate(app.data / "big.csv", mb=2)
    mine = {"language": "uk", "actor": "olena"}                                                   # saving settings replaces all of them: send the language and name again
    Client(app).login().json("POST", "/api/settings", {**mine, "max_file_mb": 1})
    page.reload()
    page.locator("#prop-box > summary").click()
    page.select_option("#prop-file", label=page.locator("#prop-file option", has_text="big.csv").first.inner_text())
    page.select_option("#prop-schema", index=1)
    page.locator("#prop-go").click()
    expect(page.locator("#prop-msg")).to_contain_text("ліміт політики")                           # a refusal of the core, translated
    check_screen(page, "review list, create form, a refusal")
    Client(app).login().json("POST", "/api/settings", {**mine, "max_file_mb": None})
    page.reload()
    page.locator("#prop-box > summary").click()
    page.select_option("#prop-file", label=page.locator("#prop-file option", has_text="sales_renamed.csv").first.inner_text())
    page.select_option("#prop-schema", label=page.locator("#prop-schema option", has_text="schema_sales.json").first.inner_text())
    page.locator("#prop-go").click()
    expect(page.locator("#prop-flash")).to_contain_text("Пропозицію для sales_renamed.csv створено. Підтверджено: 5 · Потребують перевірки: 1 · Відхилено: 0.")
    expect(page.locator("#prop-flash")).to_contain_text("Схвалити її має інша людина, не olena")
    check_screen(page, "review list, a proposal was created")


def test_the_empty_review_screen_in_ukrainian(tmp_path):
    pw = pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect
    data = tmp_path / "data"
    data.mkdir()
    for name in ("sales.csv", "schema_sales.json"):
        shutil.copy(EX / name, data)
    server = make_server(tmp_path / "work", port=0, token=TOKEN, data_dirs=[data])
    threading.Thread(target=lambda: server.serve_forever(0.05), daemon=True).start()
    server.runner._can_browse = True
    Client(server).login().json("POST", "/api/settings", {"language": "uk"})
    try:
        with pw.sync_playwright() as p:
            try:
                browser = p.chromium.launch(args=["--no-sandbox"])
            except Exception as exc:
                pytest.skip(f"Chromium not available: {exc}")
            page = browser.new_page(viewport={"width": 1100, "height": 1000})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{server.port}/?t={TOKEN}")
            page.goto(f"http://127.0.0.1:{server.port}/#/")
            expect(page.locator("#prop-empty")).to_contain_text("Пропозицій не знайдено.")
            expect(page.locator("#prop-empty")).to_contain_text("Пошук у:")
            expect(page.get_by_role("button", name="Створити пропозицію")).to_be_visible()
            check_screen(page, "review list, nothing yet")
            browser.close()
            assert errors == []
    finally:
        server.shutdown()
        server.server_close()


def test_system_follows_the_browsers_first_language_and_english_stays_english(app):
    pw = pytest.importorskip("playwright.sync_api")
    Client(app).login().json("POST", "/api/settings", {"language": "system"})
    with pw.sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:
            pytest.skip(f"Chromium not available: {exc}")
        for locale, lang, heading in (("uk-UA", "uk", "Запуск файлу"), ("uk", "uk", "Запуск файлу"), ("en-US", "en", "Run a file"), ("de-DE", "en", "Run a file")):
            ctx = browser.new_context(locale=locale)
            page = ctx.new_page()
            page.goto(f"http://127.0.0.1:{app.port}/?t={TOKEN}&go=run")
            page.wait_for_selector("#run-file")
            assert page.evaluate("document.documentElement.lang") == lang, locale
            assert page.locator("h2").first.inner_text() == heading, locale
            ctx.close()
        Client(app).login().json("POST", "/api/settings", {"language": "en"})            # an explicit choice beats the browser's language
        ctx = browser.new_context(locale="uk-UA")
        page = ctx.new_page()
        page.goto(f"http://127.0.0.1:{app.port}/?t={TOKEN}&go=run")
        page.wait_for_selector("#run-file")
        assert page.locator("h2").first.inner_text() == "Run a file" and page.evaluate("document.documentElement.lang") == "en"
        browser.close()


def test_choosing_the_language_on_the_settings_page_switches_the_whole_page(app):
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
        page.wait_for_selector("#set-language")
        assert [o.inner_text() for o in page.locator("#set-language option").all()] == ["Follow my computer", "English", "Українська"]
        page.select_option("#set-language", "uk")
        page.locator("#set-save").click()
        expect(page.locator("html")).to_have_attribute("lang", "uk")
        page.wait_for_selector("#set-language")
        assert page.get_by_role("heading", name="Налаштування").is_visible() and page.url.endswith("#/settings")
        assert [o.inner_text() for o in page.locator("#set-language option").all()] == ["Як на моєму комп’ютері", "English", "Українська"]
        page.select_option("#set-language", "en")
        page.locator("#set-save").click()
        expect(page.locator("html")).to_have_attribute("lang", "en")
        page.wait_for_selector("#set-language")
        assert page.get_by_role("heading", name="Settings").is_visible()
        browser.close()
        assert errors == []
