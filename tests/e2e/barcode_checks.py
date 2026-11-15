"""Browser checks of a barcode typed by hand on the form of a stock item (docs/adr/0083), run in
CI (`.github/workflows/ui.yml`).

Needs an instance with `barcode@example.com`, a home, and the answer of the open databases for
the code 4006381333931 already in the cache (a `BarcodeLookup` row, with a photo): no request
leaves the machine, the online lookup stays off.

    python tests/e2e/barcode_checks.py --base http://localhost:8008

- The form offers **Scan** next to the barcode field; the code is typed by hand.
- An invalid code is said under the field.
- A valid code shows what was found, with the photo and its credit; **Use these values** fills
  the empty fields only, a field already typed keeps its value; nothing is saved before the
  button.
- Saved with **Keep this photo**, the stock item has the photo as its label, credited under it.
"""

import argparse
import re
import sys

from playwright.sync_api import Browser, Page, expect, sync_playwright

EMAIL = "barcode@example.com"
PASSWORD = "correct-horse-battery-staple"
CODE = "4006381333931"
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


def typed_by_hand(browser: Browser, base: str) -> None:
    context = browser.new_context(viewport={"width": 390, "height": 900})
    page = context.new_page()
    sign_in(page, base)
    home = home_path(page, base)
    page.goto(f"{home}stock/new/")
    field = page.locator("input[name=ean]")
    check(field.count() == 1, "the form has the barcode field")
    check(page.locator(".barcode-scan-button").count() == 1, "with its Scan button")

    field.fill("4006381333932")
    field.press("Tab")
    expect(page.locator(".barcode-lookup")).to_contain_text("not a valid barcode")
    check(True, "an invalid code is said under the field")

    page.fill("input[name=manufacturer_reference]", "Typed by me")
    field.fill(CODE)
    field.press("Tab")
    found = page.locator(".barcode-found")
    expect(found).to_be_visible()
    check("Open Products Facts" in found.inner_text(), "the code is found, with its source")
    check(found.locator("img").count() == 1, "with the photo of the product")
    check("CC BY-SA 3.0" in found.inner_text(), "and its licence")
    check(page.input_value("input[name=name]") == "", "nothing is filled before the button")

    found.get_by_role("button", name="Use these values").click()
    check(
        page.input_value("input[name=name]") == "Acme Dishwasher salt",
        f"the empty name is filled ({page.input_value('input[name=name]')!r})",
    )
    check(
        page.input_value("input[name=manufacturer_reference]") == "Typed by me",
        "a field already typed keeps its value",
    )
    check("Salts" in page.input_value("textarea[name=notes]"), "the category goes in the notes")

    found.get_by_label("Keep this photo, if the sheet has none").check()
    page.locator("form button[type=submit].button-block").click()
    page.wait_for_url(re.compile(r".*/stock/[0-9a-f-]+/$"))
    check(page.locator("img.stock-label").count() == 1, "the stock item has the photo as its label")
    credit = page.locator(".photo-attribution")
    check(
        credit.count() == 1 and "CC BY-SA 3.0" in credit.inner_text(),
        "credited under it, with its licence",
    )
    check(CODE in page.content(), "the code is on the sheet")
    context.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8008")
    args = parser.parse_args()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        typed_by_hand(browser, args.base.rstrip("/"))
        browser.close()
    if failures:
        print(f"{len(failures)} check(s) failed.")  # noqa: T201
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
