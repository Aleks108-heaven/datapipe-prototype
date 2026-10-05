"""Real-browser tests (headless Chromium via Playwright). Skipped automatically if Playwright/Chromium is missing."""
import csv
import io
import json
import os
import threading
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from datapipe.audit import AuditLog  # noqa: E402
from datapipe.ingest import parse_csv  # noqa: E402
from datapipe.webui import make_server  # noqa: E402
from datapipe.ingest import read_source  # noqa: E402
from datapipe.pipeline import run_pipeline  # noqa: E402
from helpers import EX, GOOD, m, make_proposal  # noqa: E402
import shutil  # noqa: E402

TOKEN = "e2e-token-abcdefghijklmnopqrstuvwxyz"
NEEDS_REVIEW = GOOD[:4] + [m("Ordered On", "order_date", 0.6, "dates look similar"), m("Paid?", "paid")]
NO_PAID = GOOD[:5]
SHOTS = os.environ.get("DATAPIPE_SCREENSHOT_DIR")

EVIL_HEADERS = ['<img src=x onerror="window.__xss=1">', "</script><script>window.__xss=2</script>",
                "javascript:alert(1)", '"><svg onload=window.__xss=3>']
EVIL_FILE = '"><img src=x onerror=window.__xss=6>.csv'
EVIL_RATIONALE = '<b onmouseover="window.__xss=4">trust me</b><script>window.__xss=5</script>'


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:                      # pragma: no cover - environment dependent
            pytest.skip(f"Chromium not available: {exc}")
        yield b
        b.close()


@pytest.fixture
def ui(wd):
    good, _ = make_proposal(wd, NEEDS_REVIEW)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(EVIL_HEADERS)
    w.writerow(["1", "EU", "5.00", "2026-01-01"])
    w.writerow(["2", "US", "6.00", "2026-01-02"])
    tbl = parse_csv(buf.getvalue())
    tbl.source_sha256 = "0" * 64
    evil_map = [m(EVIL_HEADERS[0], "order_id", 0.99, EVIL_RATIONALE), m(EVIL_HEADERS[1], "region", 0.9, EVIL_RATIONALE),
                m(EVIL_HEADERS[2], "amount", 0.99, EVIL_RATIONALE), m(EVIL_HEADERS[3], "order_date", 0.9, EVIL_RATIONALE)]
    evil, _ = make_proposal(wd, evil_map, tbl=tbl, actor="mallory", name=EVIL_FILE)

    data_dir = wd.parent / "data"
    data_dir.mkdir()
    shutil.copy(EX / "sales_renamed.csv", data_dir / "sales_renamed.csv")
    shutil.copy(EX / "sales_renamed.csv", data_dir / "sales_apr.csv")          # same content, another name
    tbl_apr = read_source(EX / "sales_renamed.csv", max_bytes=10 ** 8)
    remap, _ = make_proposal(wd, NO_PAID, tbl=tbl_apr, name="sales_apr.csv")    # 'paid' left unmapped
    hostile_csv = io.StringIO()
    hw = csv.writer(hostile_csv)
    hw.writerow(EVIL_HEADERS)
    hw.writerows([["1", "EU", "5.00", "2026-01-01"], ["2", "US", "6.00", "2026-01-02"]])
    (data_dir / "hostile_headers.csv").write_text(hostile_csv.getvalue())
    hostile_tbl = read_source(data_dir / "hostile_headers.csv", max_bytes=10 ** 8)
    hostile, _ = make_proposal(wd, [m(EVIL_HEADERS[0], "order_id", 0.99)], tbl=hostile_tbl, name="hostile_headers.csv")

    class UI:
        pass
    u = UI()
    u.wd, u.data, u.good, u.evil = wd, data_dir, good["proposal_sha256"], evil["proposal_sha256"]
    u.remap, u.hostile = remap["proposal_sha256"], hostile["proposal_sha256"]
    u.server = make_server(wd, port=0, token=TOKEN, data_dirs=[data_dir])
    threading.Thread(target=lambda: u.server.serve_forever(0.05), daemon=True).start()
    u.url = f"http://127.0.0.1:{u.server.port}/?t={TOKEN}"
    u.base = f"http://127.0.0.1:{u.server.port}/"
    yield u
    u.server.shutdown()
    u.server.server_close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1100, "height": 900})
    pg = ctx.new_page()
    pg.errors, pg.dialogs, pg.confirm_answer = [], [], False

    def on_dialog(d):
        if d.type == "beforeunload":                    # test setup navigates freely; the guard itself is tested via confirm()
            d.accept()
            return
        pg.dialogs.append(d.message)
        d.accept() if pg.confirm_answer else d.dismiss()
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.on("dialog", on_dialog)
    pg.add_init_script("window.__csp = []; document.addEventListener('securitypolicyviolation', e => window.__csp.push(e.violatedDirective));")
    yield pg
    assert pg.errors == [], pg.errors
    ctx.close()


def open_detail(page, ui, pid, viewport=None):
    if viewport:
        page.set_viewport_size(viewport)
    page.goto(ui.url)
    page.goto(f"{ui.base}#/p/{pid}")
    page.wait_for_selector("#items .card")


def shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=False)


def card(page, target):
    return page.locator(f'.card[data-target="{target}"]').first


# ---------------------------------------------------------------- navigation & display
def test_list_shows_proposals_with_counts_and_opens_detail(page, ui):
    page.goto(ui.url)
    expect(page.locator("h2").first).to_have_text("Mapping proposals")
    expect(page.locator(".card.click")).to_have_count(4)
    row = page.locator(".card.click", has_text="sales_renamed.csv")
    expect(row).to_contain_text("5 verified")
    expect(row).to_contain_text("1 need review")
    expect(row).to_contain_text("Pending review")
    shot(page, "1-list-desktop")
    row.click()
    page.wait_for_selector("#items .card")
    assert page.locator("#items .card").count() == 6
    for label in ("Include", "Exclude"):                                      # nothing is chosen yet for an item that needs review (QA-013)
        expect(card(page, "order_date").get_by_role("radio", name=label)).to_have_attribute("aria-checked", "false")
    expect(card(page, "order_date")).to_contain_text("Not decided yet")
    expect(card(page, "amount").get_by_role("radio", name="Include")).to_have_attribute("aria-checked", "true")
    page.locator("#more > summary").click()                                   # one folded "Details and help" section...
    page.locator("summary", has_text="Exactly what was sent").click()       # ...holding the payload: cloud provider, so it is inspectable
    expect(page.locator("#payload")).to_contain_text('"mode": "shapes"')
    assert "anna@example.com" not in page.locator("#payload").text_content()
    shot(page, "2-detail-desktop")


def test_keyboard_opens_a_proposal(page, ui):
    page.goto(ui.url)
    page.locator(".card.click", has_text="sales_renamed.csv").focus()
    page.keyboard.press("Enter")
    page.wait_for_selector("#items .card")
    assert page.url.endswith(f"#/p/{ui.good}")


# ---------------------------------------------------------------- review flow
def test_full_review_flow_override_four_eyes_and_lock_after_approval(page, ui):
    open_detail(page, ui, ui.good)
    approve, why = page.locator("#approve"), page.locator("#why")
    expect(approve).to_be_disabled()
    expect(why).to_contain_text("required column(s) not mapped: order_date")

    card(page, "order_date").get_by_role("radio", name="Include").click()
    expect(card(page, "order_date").get_by_role("radio", name="Include")).to_have_attribute("aria-checked", "true")
    page.locator("#reviewer").fill("alice")
    expect(why).to_contain_text("add a note")
    page.locator("#note").fill("Checked the export spec: 'Ordered On' is the order date.")
    expect(why).to_contain_text("must be a different person")
    expect(approve).to_be_enabled()
    shot(page, "3-ready-to-approve")

    approve.click()                                                        # server enforces four-eyes
    expect(page.locator("#errbox")).to_contain_text("four-eyes")
    expect(page.locator("#result")).to_have_count(0)

    page.locator("#reviewer").fill("bob")
    approve.click()
    expect(page.locator("#result")).to_contain_text("Schema created: schemas/")
    shot(page, "4-approved")

    files = list((ui.wd / "schemas").glob("*.json"))
    assert len(files) == 1
    prov = json.loads(files[0].read_text())["provenance"]
    assert prov["approved_by"] == "bob" and prov["included_needs_review"] == ["order_date"]
    assert "Checked the export spec" in prov["review_note"]
    ev = AuditLog(ui.wd / "audit.jsonl").records()[-1]
    assert ev["event"] == "mapping_approved" and ev["actor"] == "bob"

    page.reload()
    page.wait_for_selector("#items .card")
    expect(page.locator(".actionbar")).to_contain_text("Approved by bob")
    assert page.locator("#approve").count() == 0
    assert all(b.is_disabled() for b in page.locator(".seg button").all())


def test_excluding_a_required_mapping_disables_approval(page, ui):
    open_detail(page, ui, ui.good)
    card(page, "amount").get_by_role("radio", name="Exclude").click()
    page.locator("#reviewer").fill("bob")
    page.locator("#note").fill("testing")
    expect(page.locator("#approve")).to_be_disabled()
    expect(page.locator("#why")).to_contain_text("amount")


def test_reject_needs_a_note_and_updates_list(page, ui):
    open_detail(page, ui, ui.good)
    page.locator("#reviewer").fill("bob")
    expect(page.locator("#reject")).to_be_disabled()
    page.locator("#note").fill("Wrong source file.")
    page.locator("#reject").click()
    expect(page.locator(".actionbar")).to_contain_text("Rejected by bob")
    page.goto(f"{ui.base}#/")
    expect(page.locator(".card.click", has_text="sales_renamed.csv")).to_contain_text("Rejected")


def test_rejected_verification_items_have_no_controls(page, ui):
    p, _ = make_proposal(ui.wd, GOOD[:5] + [m("Area", "paid", 0.99)])
    open_detail(page, ui, p["proposal_sha256"])
    rejected = page.locator('.card[data-status="rejected"]')
    expect(rejected).to_have_count(1)
    expect(rejected).to_contain_text("cannot be included")
    assert rejected.get_by_role("radio").count() == 0


def test_stale_second_reviewer_gets_a_conflict_message(browser, ui):
    ctx = browser.new_context()
    a, b = ctx.new_page(), ctx.new_page()
    for pg in (a, b):
        pg.goto(ui.url)
        pg.goto(f"{ui.base}#/p/{ui.good}")
        pg.wait_for_selector("#items .card")
        card(pg, "order_date").get_by_role("radio", name="Include").click()
        pg.locator("#note").fill("ok")
    a.locator("#reviewer").fill("bob")
    b.locator("#reviewer").fill("carol")
    a.locator("#approve").click()
    expect(a.locator("#result")).to_be_visible()
    b.locator("#approve").click()
    expect(b.locator("#errbox")).to_contain_text("already approved by bob")
    ctx.close()


def test_locked_reviewer_mode(browser, wd):
    make_proposal(wd)
    server = make_server(wd, port=0, token=TOKEN, reviewer="carol")
    threading.Thread(target=lambda: server.serve_forever(0.05), daemon=True).start()
    ctx = browser.new_context()
    pg = ctx.new_page()
    try:
        pg.goto(f"http://127.0.0.1:{server.port}/?t={TOKEN}")
        pg.locator(".card.click").first.click()
        pg.wait_for_selector("#items .card")
        expect(pg.locator("#reviewer")).to_have_value("carol")
        assert pg.locator("#reviewer").evaluate("e => e.readOnly")
        expect(pg.locator("#whoami")).to_have_text("reviewing as carol")
        pg.locator("#approve").click()
        expect(pg.locator("#result")).to_be_visible()
        assert json.loads(next((wd / "schemas").glob("*.json")).read_text())["provenance"]["approved_by"] == "carol"
    finally:
        ctx.close()
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------- safety
def test_hostile_names_and_rationale_are_rendered_as_inert_text(page, ui):
    page.goto(ui.url)
    page.wait_for_selector(".card.click")
    expect(page.locator(".card.click h3", has_text="onerror=window.__xss=6")).to_have_count(1)
    page.goto(f"{ui.base}#/p/{ui.evil}")
    page.wait_for_selector("#items .card")
    sources = page.locator('[data-role="source"]').all_text_contents()
    assert sorted(sources) == sorted(EVIL_HEADERS)                             # shown verbatim, as text
    expect(page.locator("h2").nth(0)).to_have_text(EVIL_FILE)
    expect(page.locator(".quote").first).to_contain_text("<script>window.__xss=5</script>")   # visible as characters
    assert page.evaluate("typeof window.__xss") == "undefined"
    assert page.dialogs == []
    for selector in ("#app img", "#app script", "#app svg", "#app b", "#app iframe", "#app a[href]"):
        assert page.locator(selector).count() == 0, selector
    assert page.evaluate("window.__csp") == []                                  # nothing even tried to break the CSP
    shot(page, "5-hostile-content-inert")


def test_tampered_proposal_blocks_approval_in_the_ui(page, ui):
    path = next(p for p in (ui.wd / "mappings").glob("*.json") if ui.good[:10] in p.name)
    doc = json.loads(path.read_text())
    doc["items"][0]["confidence"] = 0.5
    path.write_text(json.dumps(doc))
    open_detail(page, ui, ui.good)
    expect(page.locator(".banner.bad")).to_contain_text("INTEGRITY CHECK FAILED")
    page.locator("#reviewer").fill("bob")
    expect(page.locator("#approve")).to_be_disabled()
    page.goto(f"{ui.base}#/")
    expect(page.locator(".card.click", has_text="sales_renamed.csv")).to_contain_text("INTEGRITY FAILED")


def test_browser_without_the_secret_link_sees_nothing(browser, ui):
    ctx = browser.new_context()
    pg = ctx.new_page()
    resp = pg.goto(ui.base)
    assert resp.status == 401 and "Open the link" in pg.content()
    assert ctx.request.get(f"{ui.base}api/proposals").status == 401        # (page fetch is blocked by CSP anyway)
    ctx.close()


def test_cross_site_page_cannot_drive_the_api(browser, ui):
    """A different origin (another local server) with a logged-in browser must not be able to read or approve."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Evil(BaseHTTPRequestHandler):
        def do_GET(self):
            body = (f"<script>window.result = null; fetch('http://127.0.0.1:{ui.server.port}/api/proposals',"
                    "{credentials:'include'}).then(r=>r.text()).then(t=>window.result='read:'+t).catch(e=>window.result='blocked');"
                    f"fetch('http://127.0.0.1:{ui.server.port}/api/proposals/{ui.good}/approve',"
                    "{method:'POST',credentials:'include',headers:{'Content-Type':'application/json'},"
                    "body:JSON.stringify({reviewer:'evil'})}).then(r=>window.post=r.status).catch(e=>window.post='blocked');"
                    "</script>ok").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    evil = ThreadingHTTPServer(("127.0.0.1", 0), Evil)
    threading.Thread(target=lambda: evil.serve_forever(0.05), daemon=True).start()
    ctx = browser.new_context()
    pg = ctx.new_page()
    try:
        pg.goto(ui.url)                                          # victim is logged in
        pg.goto(f"http://localhost:{evil.server_port}/")         # different origin (localhost vs 127.0.0.1)
        pg.wait_for_function("window.result !== null && window.post !== undefined")
        assert pg.evaluate("window.result") == "blocked" or "read:" not in str(pg.evaluate("window.result"))
        assert pg.evaluate("window.post") != 200
    finally:
        ctx.close()
        evil.shutdown()
        evil.server_close()
    assert AuditLog(ui.wd / "audit.jsonl").records() == []      # nothing was approved


# ---------------------------------------------------------------- layout
def test_no_horizontal_scroll_on_a_phone_even_with_hostile_long_names(page, ui):
    for pid, name in ((ui.good, "6-detail-phone"), (ui.evil, "7-hostile-phone")):
        open_detail(page, ui, pid, viewport={"width": 390, "height": 844})
        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        assert overflow <= 1, f"horizontal overflow of {overflow}px"
        shot(page, name)
    page.goto(f"{ui.base}#/")
    page.wait_for_selector(".card.click")
    assert page.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 1
    shot(page, "8-list-phone")


def test_dark_mode_changes_the_palette(page, ui):
    page.goto(ui.url)
    page.wait_for_selector(".card.click")
    page.emulate_media(color_scheme="light")
    light = page.evaluate("getComputedStyle(document.body).backgroundColor")
    page.emulate_media(color_scheme="dark")
    dark = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert light != dark
    page.goto(f"{ui.base}#/p/{ui.good}")
    page.wait_for_selector("#items .card")
    shot(page, "9-detail-dark")


# ---------------------------------------------------------------- manual remapping
def remap_select(page, target):
    return page.locator(f'.card[data-target="{target}"] select')


def test_manual_remap_of_an_unmapped_required_column_end_to_end(page, ui):
    open_detail(page, ui, ui.remap)
    expect(page.locator("#req-banner")).to_contain_text("paid")
    expect(page.locator("#approve")).to_be_disabled()
    expect(page.locator(".card[data-target=\"paid\"] [data-role=current]")).to_have_text("Not mapped")
    shot(page, "10-remap-before")

    remap_select(page, "paid").select_option(label="Paid?")
    manual = page.locator('.card[data-status="manual"]')
    expect(manual).to_have_count(1)
    expect(manual).to_contain_text("Manual (by you)")
    expect(manual).to_contain_text("Values fit target type: 100% of 8")
    expect(page.locator(".card[data-target=\"paid\"] [data-role=current]")).to_have_text("Manual: ← Paid?")
    expect(page.locator("#req-banner")).to_be_hidden()                       # nothing required is unmapped any more
    expect(page.locator("#why")).to_contain_text("add a note")               # note required for manual mappings

    page.locator("#reviewer").fill("bob")
    page.locator("#note").fill("The provider missed it; 'Paid?' is the payment flag.")
    expect(page.locator("#approve")).to_be_enabled()
    shot(page, "11-remap-manual-mapped")
    page.locator("#approve").click()
    expect(page.locator("#result")).to_contain_text("Schema created")

    schema_file = next((ui.wd / "schemas").glob("*.json"))
    doc = json.loads(schema_file.read_text())
    assert {c["name"]: c.get("source") for c in doc["columns"]}["paid"] == "Paid?"
    assert doc["provenance"]["manual_mappings"][0]["evidence"]["parse_rate"] == 1.0
    ev = AuditLog(ui.wd / "audit.jsonl").records()[-1]
    assert ev["data"]["manual_mappings"][0]["source"] == "Paid?"

    run = run_pipeline(ui.data / "sales_apr.csv", workdir=ui.wd, policy_name="regulated", schema_path=schema_file,
                       analysis_path=EX / "analysis_sales.json", actor="alice")
    base = run_pipeline(EX / "sales.csv", workdir=ui.wd, policy_name="regulated", schema_path=EX / "schema_sales.json",
                        analysis_path=EX / "analysis_sales.json", actor="alice")
    assert run.document["results_sha256"] == base.document["results_sha256"]


def test_a_manual_choice_the_data_contradicts_is_refused_with_a_reason(page, ui):
    open_detail(page, ui, ui.hostile)                                          # column 4 holds dates
    remap_select(page, "amount").select_option(index=4)
    err = page.locator('.card[data-target="amount"] [data-role=remap-error]')
    expect(err).to_contain_text("only 0% of its values fit")
    expect(page.locator('.card[data-status="manual"]')).to_have_count(0)
    expect(remap_select(page, "amount")).to_have_value("")
    shot(page, "12-remap-refused")


def test_columns_used_elsewhere_are_disabled_and_removal_works(page, ui):
    open_detail(page, ui, ui.remap)
    opt = remap_select(page, "region").locator("option", has_text="Order No")
    expect(opt).to_have_text(__import__("re").compile(r"Order No\s+\(used for order_id\)"))
    assert opt.is_disabled()
    remap_select(page, "paid").select_option(label="Paid?")
    expect(page.locator('.card[data-status="manual"]')).to_have_count(1)
    # 'Paid?' is now taken, so it is disabled for other targets
    assert remap_select(page, "customer_email").locator("option", has_text="Paid?").is_disabled()
    page.locator('.card[data-status="manual"] button', has_text="Remove manual mapping").click()
    expect(page.locator('.card[data-status="manual"]')).to_have_count(0)
    expect(page.locator(".card[data-target=\"paid\"] [data-role=current]")).to_have_text("Not mapped")
    remap_select(page, "paid").select_option(label="Paid?")
    remap_select(page, "paid").select_option(value="")                     # 'Remove my manual mapping'
    expect(page.locator('.card[data-status="manual"]')).to_have_count(0)


def test_manual_mapping_supersedes_a_proposed_item_in_the_display(page, ui):
    open_detail(page, ui, ui.remap)
    remap_select(page, "amount").select_option(label="Total (EUR)")            # same source the provider proposed
    merged = page.locator('.card[data-target="amount"]')
    expect(merged).to_have_count(1)                                            # one card per column, never a proposal plus a manual twin
    expect(merged).to_have_attribute("data-status", "manual")
    expect(merged).to_contain_text("replaces the proposed mapping from Total (EUR)")
    assert merged.get_by_role("radio").count() == 0


def test_remap_is_explained_when_the_source_file_is_not_available(page, ui):
    open_detail(page, ui, ui.evil)                                              # made from an in-memory table
    expect(page.locator("#remap-unavailable")).to_contain_text("needs the original data file")
    expect(page.locator("#remap-unavailable")).to_contain_text("source file not found")
    assert page.locator("[data-role=remap-select]").count() == 0


def test_hostile_column_names_stay_inert_inside_the_column_picker(page, ui):
    open_detail(page, ui, ui.hostile)
    options = remap_select(page, "amount").locator("option").all_text_contents()
    for h_ in EVIL_HEADERS:
        assert any(o.strip().startswith(h_) for o in options), h_
    remap_select(page, "amount").select_option(label=EVIL_HEADERS[2])          # 'javascript:alert(1)' holds 5.00/6.00
    expect(page.locator('.card[data-status="manual"] [data-role=source]')).to_have_text(EVIL_HEADERS[2])
    assert page.evaluate("typeof window.__xss") == "undefined" and page.dialogs == []
    for selector in ("#app img", "#app script", "#app svg", "#app iframe", "#app a[href]"):
        assert page.locator(selector).count() == 0, selector
    assert page.evaluate("window.__csp") == []


def test_stale_reviewer_gets_a_clear_message_when_checking_after_a_decision(browser, ui):
    ctx = browser.new_context()
    a, b = ctx.new_page(), ctx.new_page()
    for pg in (a, b):
        pg.goto(ui.url)
        pg.goto(f"{ui.base}#/p/{ui.remap}")
        pg.wait_for_selector("#items .card")
    a.locator("#reviewer").fill("bob")
    remap_select(a, "paid").select_option(label="Paid?")
    a.locator("#note").fill("ok")
    a.locator("#approve").click()
    expect(a.locator("#result")).to_be_visible()
    remap_select(b, "paid").select_option(label="Paid?")
    expect(b.locator('.card[data-target="paid"] [data-role=remap-error]')).to_contain_text("already approved by bob")
    ctx.close()


def test_decided_proposals_hide_the_remap_controls(page, ui):
    open_detail(page, ui, ui.remap)
    remap_select(page, "paid").select_option(label="Paid?")
    page.locator("#reviewer").fill("bob")
    page.locator("#note").fill("done")
    page.locator("#approve").click()
    expect(page.locator("#result")).to_be_visible()
    page.reload()
    page.wait_for_selector("#items .card")
    assert page.locator("[data-role=remap-select]").count() == 0
    assert page.locator("h2", has_text="Map columns yourself").count() == 0


def test_phone_layout_with_the_remap_section(page, ui):
    open_detail(page, ui, ui.remap, viewport={"width": 390, "height": 844})
    remap_select(page, "paid").select_option(label="Paid?")
    expect(page.locator('.card[data-status="manual"]')).to_have_count(1)
    assert page.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 1
    page.locator(".change").first.scroll_into_view_if_needed()
    shot(page, "13-remap-phone")
    open_detail(page, ui, ui.hostile, viewport={"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 1


def test_leaving_with_unsaved_decisions_asks_first(page, ui):
    open_detail(page, ui, ui.remap)
    page.get_by_role("button", name="← All proposals").click()      # nothing decided yet: no prompt expected
    expect(page.get_by_role("heading", name="Mapping proposals")).to_be_visible()
    assert page.dialogs == []

    open_detail(page, ui, ui.remap)
    remap_select(page, "paid").select_option(label="Paid?")
    expect(page.locator('.card[data-status="manual"]')).to_have_count(1)
    page.get_by_role("button", name="← All proposals").click()      # unsaved manual mapping: asked, answer no -> stay
    for _ in range(50):
        if page.dialogs:
            break
        page.wait_for_timeout(100)
    assert len(page.dialogs) == 1 and "not saved" in page.dialogs[0]
    expect(page.locator('.card[data-status="manual"]')).to_have_count(1)
    expect(page.get_by_role("button", name="← All proposals")).to_be_visible()
    page.confirm_answer = True
    page.get_by_role("button", name="← All proposals").click()      # answer yes -> leave
    expect(page.get_by_role("heading", name="Mapping proposals")).to_be_visible()


def test_status_strip_stays_visible_and_phone_can_jump_to_the_action_bar(page, ui):
    open_detail(page, ui, ui.remap, viewport={"width": 390, "height": 844})
    expect(page.locator("#summary")).to_contain_text("need your decision")
    assert page.locator("#more").evaluate("e => e.open") is False            # provenance details are folded away...
    expect(page.get_by_text("Data sent out")).to_be_visible()                 # ...but what left the machine is not
    page.mouse.wheel(0, 700)
    page.wait_for_timeout(200)
    expect(page.locator("#summary")).to_be_in_viewport()                      # sticky: the to-do count follows the reviewer
    page.get_by_role("button", name="Go to approve / reject").click()
    expect(page.locator("#reviewer")).to_be_in_viewport()
    expect(page.locator("#reviewer")).to_be_focused()
    page.set_viewport_size({"width": 1100, "height": 900})
    expect(page.get_by_role("button", name="Go to approve / reject")).to_be_hidden()   # desktop already has the fixed bar


def test_evidence_legend_and_one_notation(page, ui):
    open_detail(page, ui, ui.remap)
    expect(page.locator("#legend summary")).to_have_text("How to read this page")
    chips = page.locator(".evidence .chip").all_inner_texts()
    assert any(c.startswith("Distinct values: ") and c.endswith("%") for c in chips)
    assert all(c.endswith("%") for c in chips if c.startswith("Name similarity"))


# ---------------------------------------------------------------- review fixes (QA-007 .. QA-021)
def test_tampered_proposal_explains_why_nothing_can_be_clicked(page, ui):                       # QA-012
    path = next(p for p in (ui.wd / "mappings").glob("*.json") if ui.good[:10] in p.name)
    doc = json.loads(path.read_text())
    doc["items"][0]["confidence"] = 0.5
    path.write_text(json.dumps(doc))
    open_detail(page, ui, ui.good)
    page.locator("#reviewer").fill("bob")
    page.locator("#note").fill("why not")
    expect(page.locator("#reject")).to_be_disabled()
    expect(page.locator("#why")).to_contain_text("neither approved nor rejected")
    expect(page.locator("#summary")).to_contain_text("Integrity check failed")
    assert "verified" not in page.locator("#summary").text_content()                          # no "6 verified" under a red banner


def test_arrow_keys_move_focus_with_the_selection_and_the_ring_is_visible(page, ui):          # QA-008
    open_detail(page, ui, ui.good)
    seg = card(page, "order_date").locator(".seg button")
    seg.first.focus()
    for key, want in (("ArrowRight", "Exclude"), ("ArrowLeft", "Include"), ("ArrowDown", "Exclude"), ("ArrowUp", "Include")):
        page.keyboard.press(key)
        expect(card(page, "order_date").get_by_role("radio", name=want)).to_have_attribute("aria-checked", "true")
        expect(card(page, "order_date").get_by_role("radio", name=want)).to_be_focused()
    ring = page.evaluate("""() => { const s = getComputedStyle(document.activeElement); return [s.outlineColor, s.backgroundColor, s.outlineOffset]; }""")
    assert ring[0] != ring[1] and ring[2].startswith("-"), ring         # not blue-on-blue, and inside the box so overflow:hidden cannot clip it


def test_undecided_items_show_no_choice_until_the_reviewer_makes_one(page, ui):                # QA-013
    open_detail(page, ui, ui.good)
    c = card(page, "order_date")
    expect(c.locator("[data-role=undecided]")).to_be_visible()
    expect(page.locator("#summary")).to_contain_text("1 need your decision")
    c.get_by_role("radio", name="Exclude").click()                                            # an explicit "no" is a decision too
    expect(c.get_by_role("radio", name="Exclude")).to_have_attribute("aria-checked", "true")
    expect(c.locator("[data-role=undecided]")).to_have_count(0)
    expect(page.locator("#summary")).to_contain_text("0 need your decision")


@pytest.mark.parametrize("viewport,bar_fixed", [({"width": 390, "height": 844}, False), ({"width": 1366, "height": 650}, True)])
@pytest.mark.parametrize("text_px", [None, 18])             # 18 px ~ the taller default fonts of Linux: the layout must not fit by a hair on one platform only
def test_first_decision_is_on_the_first_screen(page, ui, viewport, bar_fixed, text_px):        # QA-009
    open_detail(page, ui, ui.good, viewport=viewport)
    if text_px:
        page.evaluate("px => { document.body.style.fontSize = px + 'px'; }", text_px)
    box = card(page, "order_date").locator(".seg").bounding_box()
    limit = viewport["height"]
    if bar_fixed:
        limit = page.locator(".actionbar").bounding_box()["y"]
        assert page.locator(".actionbar").bounding_box()["height"] < 130                      # one row, not a 150px block
    assert box["y"] + box["height"] <= limit, (box, limit)


def test_after_approving_focus_lands_on_the_outcome_and_the_command_works_from_anywhere(page, ui):   # QA-020, QA-010
    open_detail(page, ui, ui.good)
    card(page, "order_date").get_by_role("radio", name="Include").click()
    page.locator("#reviewer").fill("bob")
    page.locator("#note").fill("checked")
    page.locator("#approve").click()
    expect(page.locator("#result")).to_be_focused()
    cmd = page.locator("#result .cmd").inner_text()
    import re
    schema = re.search(r'--schema (\S+)', cmd).group(1)
    assert Path(schema).is_absolute() and Path(schema).exists(), cmd
    assert f"--workdir {ui.wd.resolve()}" in cmd or f'--workdir "{ui.wd.resolve()}"' in cmd, cmd


def test_long_file_names_do_not_widen_the_page(page, ui):                                      # QA-014
    long_name = "a_very_long_file_name_" + "x" * 90 + ".csv"
    p, _ = make_proposal(ui.wd, NEEDS_REVIEW, name=long_name)
    for width in (320, 360):
        open_detail(page, ui, p["proposal_sha256"], viewport={"width": width, "height": 700})
        assert page.evaluate("document.documentElement.scrollWidth - innerWidth") <= 1, width
        page.goto(f"{ui.base}#/")
        page.wait_for_selector(".card.click")
        assert page.evaluate("document.documentElement.scrollWidth - innerWidth") <= 1, width


def test_text_scales_with_the_reader_text_size_setting(page, ui):                              # QA-016
    open_detail(page, ui, ui.good)
    page.evaluate("document.documentElement.style.fontSize = '32px'")                         # what a reader who doubled the default sees
    sizes = page.evaluate("""() => [parseFloat(getComputedStyle(document.querySelector('.metaline')).fontSize),
                                    parseFloat(getComputedStyle(document.querySelector('#approve')).fontSize)]""")
    assert sizes[0] < sizes[1] <= 32.5, sizes                                                  # helper text stays smaller than buttons


def test_each_column_card_is_a_named_section_with_a_heading(page, ui):                         # QA-018
    open_detail(page, ui, ui.good)
    cards = page.locator("#items section.card")
    assert cards.count() == 6
    headings = page.locator("#items section.card h3").all_inner_texts()
    assert sorted(headings) == sorted(["order_id", "customer_email", "region", "amount", "order_date", "paid"])
    for i in range(cards.count()):
        labelled = cards.nth(i).get_attribute("aria-labelledby")
        assert labelled and page.locator(f"#{labelled}").count() == 1


def test_hidden_direction_characters_in_a_column_name_are_made_visible(page, ui):              # QA-021
    tbl = parse_csv("Order No,Buyer Email,Area,Total (EUR),Ordered On,pa\u202edi\n1,a@b.co,N,1.0,2024-01-01,yes\n")
    p, _ = make_proposal(ui.wd, GOOD[:5] + [m("pa\u202edi", "paid", 0.5)], tbl=tbl, name="bidi.csv")
    open_detail(page, ui, p["proposal_sha256"])
    shown = page.locator('.card[data-target="paid"] [data-role=source]').inner_text()
    assert "\u202e" not in shown and "[U+202E]" in shown, repr(shown)


def test_a_dead_server_gives_a_next_step_and_keeps_the_decisions(page, ui):                    # QA-011
    open_detail(page, ui, ui.good)
    card(page, "order_date").get_by_role("radio", name="Include").click()
    page.locator("#reviewer").fill("bob")
    page.locator("#note").fill("checked")
    page.route("**/api/**", lambda route: route.abort())
    page.locator("#approve").click()
    expect(page.locator("#errbox")).to_contain_text("datapipe review")
    expect(page.locator("#errbox")).to_contain_text("Nothing you entered")
    expect(card(page, "order_date").get_by_role("radio", name="Include")).to_have_attribute("aria-checked", "true")
    expect(page.locator("#note")).to_have_value("checked")


def test_a_slow_list_answer_cannot_paint_over_the_proposal_that_was_opened(browser, ui):       # the flaky-test race
    import time
    ctx = browser.new_context()
    pg = ctx.new_page()
    pg.route("**/api/proposals", lambda route: (time.sleep(1.0), route.continue_()))           # only the LIST is slow
    pg.goto(ui.url, wait_until="commit")
    pg.goto(f"{ui.base}#/p/{ui.good}", wait_until="commit")
    pg.wait_for_selector("#items .card")
    pg.wait_for_timeout(1800)                                                                  # the late list answer has arrived by now
    expect(pg.locator("#items .card").first).to_be_visible()
    assert pg.locator("h2", has_text="Mapping proposals").count() == 0
    ctx.close()


@pytest.mark.parametrize("font,text_px", [(None, None), (None, 18), ("Verdana, sans-serif", 20)])    # the last one imitates the wide, tall default fonts of Linux
def test_the_header_stays_one_row_on_a_phone_so_it_cannot_push_the_first_decision_down(page, ui, font, text_px):
    open_detail(page, ui, ui.good, viewport={"width": 390, "height": 844})
    page.evaluate("([f, px]) => { if (f) document.body.style.fontFamily = f; if (px) document.body.style.fontSize = px + 'px'; }", [font, text_px])
    assert page.locator("header.top").bounding_box()["height"] < 64, page.locator("header.top").bounding_box()
    assert page.get_by_role("link", name="Settings").is_visible()                           # the gear is still there, just without its label
