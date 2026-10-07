"""The jump buttons at the top of the (long) Settings page: they scroll to a card, move focus to its heading, work from the keyboard."""
import threading

import pytest

from datapipe.webui import make_server
from test_webui_run import TOKEN, Client

pw = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

CARDS = [("set-card-general", "General", "Общие"), ("set-llm-card", "Language model", "Мовна модель"),
         ("set-card-key", "Key", "Ключ"), ("set-card-about", "About", "Про програму")]


@pytest.fixture
def app(tmp_path):
    server = make_server(tmp_path / "work", port=0, token=TOKEN, data_dirs=[])
    threading.Thread(target=lambda: server.serve_forever(0.05), daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(args=["--no-sandbox"])
        except Exception as exc:
            pytest.skip(f"Chromium not available: {exc}")
        yield b
        b.close()


def open_settings(browser, app, **ctx):
    page = browser.new_context(**ctx).new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{app.port}/?t={TOKEN}&go=run")
    page.get_by_role("link", name="Settings" if "locale" not in ctx else "Налаштування").click()
    page.wait_for_selector("nav.settings-jump")
    return page


def test_each_jump_button_brings_its_card_into_view_and_focuses_its_heading(browser, app):
    page = open_settings(browser, app, viewport={"width": 1100, "height": 560})      # short window: the lower cards start below the fold
    nav = page.locator("nav.settings-jump")
    assert nav.get_attribute("aria-label") == "On this page"
    assert [b.inner_text() for b in nav.locator("button").all()] == ["General", "Language model", "Key", "About"]
    expect(page.locator("#set-card-about")).not_to_be_in_viewport()
    for card_id, label, _ in CARDS:
        page.locator(f'nav.settings-jump button[data-jump="{card_id}"]').click()
        expect(page.locator(f"#{card_id}")).to_be_in_viewport()
        focused = page.evaluate("document.activeElement.tagName + '|' + document.activeElement.closest('.card').id")
        assert focused == f"H3|{card_id}", (label, focused)
        top = page.locator(f"#{card_id}").bounding_box()["y"]
        assert top >= 60, f"{label}: the card must not be hidden behind the sticky header (top {top})"      # the last card cannot reach the top: the page ends
    assert page.url.endswith("#/settings"), "the page's own route must not change"
    assert page.errors == []


def test_the_jump_buttons_work_from_the_keyboard(browser, app):
    page = open_settings(browser, app, viewport={"width": 1100, "height": 560})
    page.locator('nav.settings-jump button[data-jump="set-card-general"]').focus()
    page.keyboard.press("Tab")
    page.keyboard.press("Tab")
    page.keyboard.press("Tab")                                                        # General, Language model, Key, About
    assert page.evaluate("document.activeElement.dataset.jump") == "set-card-about"
    page.keyboard.press("Enter")
    expect(page.locator("#set-card-about")).to_be_in_viewport()
    assert page.evaluate("document.activeElement.closest('.card').id") == "set-card-about"
    page.keyboard.press("Tab")                                                        # focus continues from the heading into the card
    assert page.evaluate("document.activeElement.closest('.card') && document.activeElement.closest('.card').id") == "set-card-about"


def test_the_jump_buttons_fit_a_phone_and_a_large_text_size(browser, app):
    page = open_settings(browser, app, viewport={"width": 360, "height": 640})
    assert page.evaluate("document.documentElement.scrollWidth - innerWidth") <= 1
    page.evaluate("document.documentElement.style.fontSize = '200%'")                # text enlarged to 200% (the page's CSP forbids injected style tags)
    assert page.evaluate("document.documentElement.scrollWidth - innerWidth") <= 1
    heights = [b.bounding_box()["height"] for b in page.locator("nav.settings-jump button").all()]
    assert min(heights) >= 36, heights                                                # still a comfortable thing to tap


def test_the_buttons_are_translated(browser, app):
    Client(app).login().json("POST", "/api/settings", {"language": "uk"})
    page = open_settings(browser, app, viewport={"width": 1100, "height": 560}, locale="uk-UA")
    nav = page.locator("nav.settings-jump")
    assert nav.get_attribute("aria-label") == "На цій сторінці"
    assert [b.inner_text() for b in nav.locator("button").all()] == ["Загальні", "Мовна модель", "Ключ", "Про програму"]
    nav.locator("button", has_text="Про програму").click()
    expect(page.locator("#set-card-about")).to_be_in_viewport()
    assert page.evaluate("window.__i18nMissing") == [] and page.errors == []
