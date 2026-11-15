"""Browser checks of the list of every item, the materials from the stock and the sheets saved
field by field (docs/adr/0081, 0082), run in CI (`.github/workflows/ui.yml`).

Needs an instance with the demo home in English for `feedback@example.com` (`load_demo_data`):
its espresso machine, descaled every 3 months with one filter at 7.90.

    python tests/e2e/upkeep_feedback_checks.py --base http://localhost:8007

- `items`: the tab **Equipment** of Rooms lists the items of every room, at 390 and 1280 px, with
  no sideways scroll; the room and the text filter it; the espresso machine shows its next task.
- `budget`: the descaling task plans 7.90 each time and 31.60 a year, under **Planned**, apart from
  **Real**; its form shows the materials calculated, with no field of the material budget.
- `autosave`: on the sheet of the machine, a field typed is saved with no button, **Saved** is
  announced in a live region, the value is there after a reload; an empty name shows its error on
  the field and keeps what was typed; without script the button saves.
"""

import argparse
import re
import sys

from playwright.sync_api import Browser, Page, expect, sync_playwright

EMAIL = "feedback@example.com"
PASSWORD = "correct-horse-battery-staple"  # noqa: S105 - an account of the CI instance only
failures: list[str] = []


def check(condition: bool, message: str) -> None:
    print(("ok   " if condition else "FAIL ") + message)  # noqa: T201
    if not condition:
        failures.append(message)


def sign_in(page: Page, base: str) -> None:
    page.goto(f"{base}/accounts/login/")
    page.fill("input[name=login]", EMAIL)
    page.fill("input[name=password]", PASSWORD)
    page.click("form button[type=submit]")
    page.wait_for_url(re.compile(r".*/(h/|today|$).*"))


def home_path(page: Page, base: str) -> str:
    page.goto(f"{base}/rooms/")
    match = re.search(r"/h/[0-9a-f-]+/", page.url)
    assert match, page.url
    return f"{base}{match.group(0)}"


def items(browser: Browser, base: str) -> None:
    for width in (390, 1280):
        context = browser.new_context(viewport={"width": width, "height": 900})
        page = context.new_page()
        sign_in(page, base)
        home = home_path(page, base)
        page.goto(f"{home}items/")
        label = f"items {width} px"
        tabs = page.locator(".tabs-list .tab").all_text_contents()
        check(tabs == ["Rooms", "Equipment", "Stock"], f"{label}: the tabs {tabs}")
        rows = page.locator("[data-item-list] li").count()
        check(rows >= 2, f"{label}: items of several rooms ({rows})")
        scroll = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        check(scroll <= 0, f"{label}: no sideways scroll ({scroll})")
        machine = page.locator("[data-item-list] li", has_text="Espresso machine")
        check(machine.count() == 1, f"{label}: the espresso machine is listed")
        check("Descal" in (machine.inner_text()), f"{label}: with its next task")
        page.fill("#items-q", "espresso")
        page.click(".search-form button[type=submit]")
        check(page.locator("[data-item-list] li").count() == 1, f"{label}: the text filters")
        page.goto(f"{home}items/")
        options = page.locator("#items-room option").all_text_contents()
        check(len(options) >= 3, f"{label}: every room to choose from")
        page.select_option("#items-room", index=1)
        page.click(".search-form button[type=submit]")
        check("room=" in page.url, f"{label}: the room filters")
        context.close()


def _descaling(page: Page, home: str) -> str:
    page.goto(f"{home}tasks/")
    link = page.locator("a", has_text=re.compile("Descal")).first
    return str(link.get_attribute("href")).split("?")[0]


def budget(browser: Browser, base: str) -> None:
    context = browser.new_context(viewport={"width": 390, "height": 900}, locale="en-GB")
    page = context.new_page()
    sign_in(page, base)
    home = home_path(page, base)
    task = _descaling(page, home)
    page.goto(f"{base}{task}" if task.startswith("/") else task)
    planned = page.locator("[data-planned]").inner_text()
    real = page.locator("[data-real]").inner_text()
    check("7.90" in planned, f"budget: 7.90 each time, planned: {planned!r}")
    check("31.60" in planned, f"budget: 31.60 a year, planned: {planned!r}")
    check("7.90" not in real and "Paid" in real, f"budget: real apart: {real!r}")
    check(page.locator("[data-materials-from-stock]").count() == 1, "budget: materials from stock")
    page.goto((f"{base}{task}" if task.startswith("/") else task).rstrip("/") + "/edit/")
    check(page.locator("[data-material-from-stock]").count() == 1, "budget: form says calculated")
    check(page.locator("input[type=number][name=material_budget]").count() == 0, "budget: no field")
    context.close()


def autosave(browser: Browser, base: str) -> None:
    context = browser.new_context(viewport={"width": 390, "height": 900})
    page = context.new_page()
    sign_in(page, base)
    home = home_path(page, base)
    page.goto(f"{home}items/")
    page.locator("[data-item-list] a", has_text="Espresso machine").click()
    page.locator("a", has_text="Edit the item").click()
    edit = page.url
    status = page.locator("#autosave-status")
    check(status.get_attribute("aria-live") == "polite", "autosave: a polite live region")
    page.fill("#id_serial_number", "SN-E2E-1")
    # The page forbids eval (CSP): wait from the outside, never with a function in the page.
    expect(status).to_have_text("Saved", timeout=5000)
    check(True, "autosave: Saved after a pause in typing")
    page.goto(edit)
    check(page.input_value("#id_serial_number") == "SN-E2E-1", "autosave: the value is stored")
    status = page.locator("#autosave-status")
    page.fill("#id_name", "")
    page.locator("#id_name").blur()
    expect(status).to_contain_text("Not saved", timeout=5000)
    error = page.locator("#autosave-error-name")
    check(error.is_visible() and bool(error.inner_text().strip()), "autosave: error on its field")
    check(page.input_value("#id_name") == "", "autosave: what was typed stays")
    page.fill("#id_name", "Espresso machine")
    page.locator("#id_name").blur()
    expect(status).to_have_text("Saved", timeout=5000)
    check(not error.is_visible(), "autosave: the error goes once the field is right")
    context.close()
    context = browser.new_context(viewport={"width": 390, "height": 900}, java_script_enabled=False)
    page = context.new_page()
    sign_in(page, base)
    page.goto(edit)
    check(page.locator("#autosave-status").is_hidden(), "autosave: no status without script")
    page.fill("#id_serial_number", "SN-E2E-2")
    page.click("button.button-block[type=submit]")
    page.goto(edit)
    check(page.input_value("#id_serial_number") == "SN-E2E-2", "autosave: the button saves")
    context.close()


CHECKS = {"items": items, "budget": budget, "autosave": autosave}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("checks", nargs="*", default=list(CHECKS))
    parser.add_argument("--base", required=True)
    args = parser.parse_args()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for name in args.checks:
            CHECKS[name](browser, args.base.rstrip("/"))
        browser.close()
    if failures:
        sys.exit(f"{len(failures)} check(s) failed")


if __name__ == "__main__":
    main()
