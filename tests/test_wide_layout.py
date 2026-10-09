"""A wide window gets a wide page, but never a long line. The page grows to a cap (--page-max) and is centred beyond it; boxes, form fields and
tables use the room; running text keeps a readable measure (--measure); review cards sit two to a row when there is space; the fixed action
bar lines up with the cards; and nothing makes the page scroll sideways at any width."""
import shutil
import threading

import pytest

from conftest import EX
from helpers import GOOD, m, make_proposal
from datapipe.webui import make_server
from test_webui_run import TOKEN, Client, run_and_wait

pw = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

NEEDS_REVIEW = GOOD[:4] + [m("Ordered On", "order_date", 0.6, "dates look similar"), m("Paid?", "paid")]
PAGE_MAX = 1440                                                  # --page-max in the stylesheet

# The number of characters on each line of an element's text (a paragraph's last line is short by nature).
LINES_JS = """(sel) => {
  const walker = document.createTreeWalker(document.querySelector(sel), NodeFilter.SHOW_TEXT);
  const lines = []; let top = null, count = 0, n;
  while ((n = walker.nextNode())) {
    for (let i = 0; i < n.nodeValue.length; i++) {
      const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + 1);
      const rects = r.getClientRects(); if (!rects.length) continue;
      const t = Math.round(rects[0].top);
      if (top === null || Math.abs(t - top) > 4) { if (count) lines.push(count); top = t; count = 0; }
      count++;
    }
  }
  if (count) lines.push(count);
  return lines;
}"""


@pytest.fixture
def app(tmp_path):
    work, data = tmp_path / "work", tmp_path / "data"
    data.mkdir()
    for name in ("sales.csv", "sales_renamed.csv", "schema_sales.json", "analysis_sales.json"):
        shutil.copy(EX / name, data)
    first, _ = make_proposal(work, NEEDS_REVIEW, name="sales_renamed.csv")
    make_proposal(work, NEEDS_REVIEW, name="sales_renamed_b.csv")                      # a second one, so the list has something to put beside the first
    server = make_server(work, port=0, token=TOKEN, data_dirs=[data])
    threading.Thread(target=lambda: server.serve_forever(0.05), daemon=True).start()
    c = Client(server).login()
    o = c.json("GET", "/api/run/options")[1]
    pick = lambda items, name: next(x["id"] for x in items if x["name"] == name)
    done = run_and_wait(c, o, file=pick(o["files"], "sales.csv"), schema=pick(o["schemas"], "schema_sales.json"), analysis=pick(o["analyses"], "analysis_sales.json"))
    server.proposal, server.run_id = first["proposal_sha256"], done["run_id"]
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:
            pytest.skip(f"Chromium not available: {exc}")
        yield b
        b.close()


@pytest.fixture
def open_page(browser, app):
    pages = []

    def opener(route, width, height=1000):
        page = browser.new_context(viewport={"width": width, "height": height}).new_page()
        page.errors = []
        page.on("pageerror", lambda e: page.errors.append(str(e)))
        base = f"http://127.0.0.1:{app.port}/"
        page.goto(f"{base}?t={TOKEN}")
        page.goto(f"{base}#{route}")
        pages.append(page)
        return page

    yield opener
    for page in pages:
        assert page.errors == [], page.errors
        page.context.close()


def box(page, selector):
    return page.locator(selector).first.bounding_box()


def test_the_page_grows_to_its_cap_and_is_centred_beyond_it(open_page):
    page = open_page("/run", 1100)
    page.wait_for_selector("#run-go")
    assert box(page, "main")["width"] > 1000                                           # below the cap it uses the whole window (it used to stop at 920)
    page = open_page("/run", 2560)
    page.wait_for_selector("#run-go")
    main = box(page, "main")
    assert abs(main["width"] - PAGE_MAX) <= 1, main
    room = page.evaluate("document.documentElement.clientWidth")
    assert abs(main["x"] - (room - PAGE_MAX) / 2) <= 1, main                           # centred, equal margins


def test_the_run_form_puts_three_lists_in_a_row_and_its_text_keeps_a_readable_line(open_page):
    page = open_page("/run", 1920)
    page.wait_for_selector("#run-go")
    file, schema, metrics, policy = (box(page, f"#run-{x}") for x in ("file", "schema", "analysis", "policy"))
    assert abs(file["y"] - schema["y"]) <= 1 and abs(file["y"] - metrics["y"]) <= 1, (file, schema, metrics)
    assert file["x"] < schema["x"] < metrics["x"]
    assert policy["y"] > file["y"] and abs(policy["x"] - file["x"]) <= 1               # the next row starts under the first column
    assert min(file["width"], schema["width"], metrics["width"]) > 380                 # room for a whole file name in each list
    lines = page.evaluate(LINES_JS, "main .card > p.small.muted")                      # the card's introduction
    assert len(lines) >= 2 and max(lines[:-1]) <= 90, lines                            # 1,400 px of card, but not 190 characters per line


def test_settings_use_the_same_three_columns_and_their_long_hint_stays_narrow(open_page):
    page = open_page("/settings", 1920)
    page.wait_for_selector("#set-limit-hint")
    first_row = [box(page, f"#set-{x}") for x in ("actor", "policy", "maxfile")]
    second_row = [box(page, f"#set-{x}") for x in ("maxmem", "language", "theme")]
    assert len({round(b["y"]) for b in first_row}) == 1 and len({round(b["y"]) for b in second_row}) == 1
    assert second_row[0]["y"] > first_row[0]["y"] and abs(second_row[0]["x"] - first_row[0]["x"]) <= 1
    lines = page.evaluate(LINES_JS, "#set-limit-hint")
    assert len(lines) >= 3 and max(lines[:-1]) <= 90, lines


def test_review_cards_sit_two_to_a_row_when_there_is_room_and_keep_their_reading_order(open_page, app):
    for width, columns in ((1920, 2), (1100, 1), (390, 1)):
        page = open_page(f"/p/{app.proposal}", width)
        page.wait_for_selector("#items .card")
        boxes = [c.bounding_box() for c in page.locator("#items > .card").all()]
        assert len(boxes) == 6
        assert len({round(b["x"]) for b in boxes}) == columns, (width, boxes)
        order = sorted(range(len(boxes)), key=lambda i: (round(boxes[i]["y"]), round(boxes[i]["x"])))
        assert order == list(range(len(boxes))), (width, order)                        # what you see left to right, top to bottom is the order a keyboard or screen reader takes
        items = box(page, "#items")
        assert all(b["x"] >= items["x"] - 1 and b["x"] + b["width"] <= items["x"] + items["width"] + 1 for b in boxes), width


def test_the_fixed_action_bar_lines_up_with_the_cards(open_page, app):
    page = open_page(f"/p/{app.proposal}", 1920)
    page.wait_for_selector("#items .card")
    items, inner = box(page, "#items"), box(page, ".actionbar .inner")
    assert abs(items["x"] - inner["x"]) <= 1 and abs(items["x"] + items["width"] - inner["x"] - inner["width"]) <= 1, (items, inner)
    assert abs(items["width"] - (PAGE_MAX - 32)) <= 1                                  # the cap, less the page's side padding


def test_the_proposal_list_goes_two_wide_too(open_page):
    page = open_page("/", 1920)
    page.wait_for_selector(".card.click")
    a, b = (c.bounding_box() for c in page.locator(".card.click").all())
    assert abs(a["y"] - b["y"]) <= 1 and a["x"] < b["x"]
    page = open_page("/", 390)
    page.wait_for_selector(".card.click")
    a, b = (c.bounding_box() for c in page.locator(".card.click").all())
    assert abs(a["x"] - b["x"]) <= 1 and a["y"] < b["y"]                               # one column on a phone


def test_metric_tables_stay_close_to_their_labels_on_a_wide_page(open_page, app):
    page = open_page(f"/run/{app.run_id}", 1920)
    page.wait_for_selector("table.metric:not(#run-preview-table)")
    cards = page.locator(".tablecard:has(table.metric:not(#run-preview-table))")
    assert cards.count() == 3
    for i in range(cards.count()):
        card, table = cards.nth(i).bounding_box(), cards.nth(i).locator("table").bounding_box()
        assert table["width"] < 0.6 * card["width"], (i, table, card)                  # not a value 1,300 px away from its row label


@pytest.mark.parametrize("width", [320, 768, 1100, 1920, 2560])
def test_no_page_scrolls_sideways_at_any_width(open_page, app, width):
    for route, wait in (("/run", "#run-go"), ("/", ".card.click"), ("/settings", "#set-card-about"), (f"/p/{app.proposal}", "#items .card"), (f"/run/{app.run_id}", "#run-preview-table")):
        page = open_page(route, width)
        page.wait_for_selector(wait)
        assert page.evaluate("document.documentElement.scrollWidth - innerWidth") <= 1, (route, width)
