"""Installable application, offline reading and a change of language, in Chromium
(docs/adr/0065, 0072).

Run in CI by `.github/workflows/ui.yml`, against a running instance with two accounts,
each with a home (the first one has a task due today):

    python tests/e2e/pwa_checks.py --base http://localhost:8003
    python tests/e2e/pwa_checks.py --base http://localhost:8004/housecairn

What no script can do, installing on a real iPhone or Android phone, is listed in
docs/adr/0065. No screenshots (docs/adr/0017).
"""

import argparse
import contextlib
import json
import sys
from urllib.parse import urlsplit

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

FIRST = ("demo@example.com", "correct-horse-battery-staple")
SECOND = ("second@example.com", "correct-horse-battery-staple")


def say(message: str) -> None:
    sys.stdout.write(message + "\n")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAILED: {message}")
    say(f"ok: {message}")


def sign_in(page: Page, base: str, account: tuple[str, str]) -> None:
    page.goto(f"{base}/accounts/login/")
    page.fill("input[name=login]", account[0])
    page.fill("input[name=password]", account[1])
    page.click("main form:has(input[name=login]) button[type=submit]")
    page.wait_for_url("**/today/")


def sign_out(page: Page, base: str) -> None:
    page.goto(f"{base}/more/")
    page.click("button:has-text('Sign out')")
    page.wait_for_url(lambda url: "/more/" not in url)


def scope_of(page: Page) -> str:
    return str(
        page.evaluate("navigator.serviceWorker.ready.then((registration) => registration.scope)")
    )


def controlled(page: Page) -> None:
    page.wait_for_function("navigator.serviceWorker.controller !== null", timeout=15000)


def cache_names(page: Page) -> list[str]:
    return list(page.evaluate("caches.keys()"))


def page_caches(page: Page) -> list[str]:
    return [name for name in cache_names(page) if ":pages:" in name]


def wait_for_page_caches(page: Page, count: int) -> None:
    page.wait_for_function(
        "caches.keys().then((names) => names.filter((n) => n.includes(':pages:')).length"
        f" === {count})",
        timeout=10000,
    )


def heading(page: Page, text: str) -> str:
    """The first heading, once it says `text` or after 5 s."""
    with contextlib.suppress(PlaywrightTimeout):
        page.wait_for_selector(f"h1:text-is('{text}')", timeout=5000)
    return page.locator("h1").first.inner_text()


def manifest(page: Page, base: str) -> None:
    path = urlsplit(base).path
    scope = f"{path}/"
    href = page.get_attribute("link[rel=manifest]", "href")
    check(
        page.get_attribute("link[rel=manifest]", "crossorigin") == "use-credentials",
        "the manifest is fetched with the cookie",
    )
    response = page.request.get(f"{urlsplit(base).scheme}://{urlsplit(base).netloc}{href}")
    data = json.loads(response.body())
    for field in ("id", "name", "short_name", "start_url", "scope", "display", "icons"):
        check(field in data, f"the manifest has {field}")
    check(data["display"] == "standalone", "display: standalone")
    check(data["start_url"] == scope and data["scope"] == scope, f"start_url and scope {scope}")
    sizes = {(icon["sizes"], icon.get("purpose", "any")) for icon in data["icons"]}
    check(("192x192", "any") in sizes and ("512x512", "any") in sizes, "icons of 192 and 512")
    check(("512x512", "maskable") in sizes, "a maskable icon")
    for icon in data["icons"]:
        status = page.request.get(f"{urlsplit(base).scheme}://{urlsplit(base).netloc}{icon['src']}")
        check(status.ok, f"the icon {icon['src']} is served")


def offline_reading(browser: Browser, base: str) -> None:
    context: BrowserContext = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(f"{base}/accounts/login/")
    manifest(page, base)
    check(scope_of(page) == f"{base}/", f"the service worker is registered for {base}/")

    sign_in(page, base, FIRST)
    today = page.url
    controlled(page)
    # Kept at the navigation that succeeds: the first one under the service worker.
    page.goto(today)
    wait_for_page_caches(page, 1)
    tasks = today.replace("/today/", "/tasks/")
    page.goto(tasks)
    check(len(page_caches(page)) == 1, "one cache of pages, the person's")
    first_task = page.locator("main li.task-row").first.inner_text()

    context.set_offline(True)
    page.goto(today)
    banner = page.locator("#offline-banner")
    check(banner.is_visible(), "offline, Today comes from the cache with its banner")
    check("Offline: data from" in banner.inner_text(), f"the banner: {banner.inner_text()}")
    done = page.locator("main button.button-done")
    check(done.count() >= 1, "Today lists the task due today")
    check(all(done.nth(n).is_disabled() for n in range(done.count())), "Done is off offline")
    check(
        first_task.split("\n")[0] in page.locator("main").inner_text(),
        "the kept page holds the person's task",
    )
    page.goto(tasks)
    check(page.locator("#offline-banner").is_visible(), "offline, the task list too")
    page.goto(f"{base}/more/security/", wait_until="load")
    found = heading(page, "Offline")
    check(found == "Offline", f"a page that was not kept is the offline page ({found})")
    page.goto(f"{base}/")
    check(page.locator("#offline-banner").is_visible(), "the start address opens Today offline")
    context.set_offline(False)

    page.goto(today)
    check(not page.locator("#offline-banner").is_visible(), "online, no banner")
    check(page.locator("main button.button-done").first.is_enabled(), "online, Done is on")
    sign_out(page, base)
    wait_for_page_caches(page, 0)
    check(
        page_caches(page) == [],
        f"signing out empties the pages kept ({page.url}, {page_caches(page)})",
    )

    # A second person on the same browser sees nothing of the first.
    sign_in(page, base, SECOND)
    controlled(page)
    page.goto(page.url)
    wait_for_page_caches(page, 1)
    context.set_offline(True)
    page.goto(today, wait_until="load")
    found = heading(page, "Offline")
    check(found == "Offline", f"offline, the second person gets the offline page ({found})")
    check(
        first_task.split("\n")[0] not in page.content(),
        "and nothing of the first person's tasks",
    )
    context.set_offline(False)

    # A session that ends elsewhere (expired, revoked): the cookie is gone and the next
    # answer of the server is a 401. The API is opened as a page on purpose: unlike the
    # sign-in page, it runs no script of ours, so only the service worker can drop the pages.
    check(len(page_caches(page)) == 1, "the second person's pages are kept")
    context.clear_cookies()
    page.goto(f"{base}/api/v1/homes/")
    wait_for_page_caches(page, 0)
    check(
        page_caches(page) == [],
        f"an expired session (a 401) empties the pages kept ({page.url}, {page_caches(page)})",
    )
    expected = ("net::ERR_INTERNET_DISCONNECTED", "status of 401")
    errors = [e for e in errors if not any(text in e for text in expected)]
    check(not errors, f"no error in the console (CSP included): {errors}")
    context.close()


def language_change(browser: Browser, base: str) -> None:
    """A change of language applies at once, online and offline (docs/adr/0072)."""
    context: BrowserContext = browser.new_context(
        viewport={"width": 320, "height": 640}, locale="en-US"
    )
    page = context.new_page()
    sign_in(page, base, SECOND)
    today = page.url
    controlled(page)
    page.goto(today)
    wait_for_page_caches(page, 1)
    check(page.get_attribute("html", "lang") == "en", "the browser's language at first")

    page.goto(f"{base}/me/preferences/")
    page.select_option("select[name=language]", "fr")
    page.click("main form button[type=submit]")
    page.wait_for_load_state()
    check(page.get_attribute("html", "lang") == "fr", "the page after Save is in French")
    check("Préférences enregistrées." in page.content(), "and so is its message")
    # The page after the change purges the pages kept in English; Today is kept again.
    wait_for_page_caches(page, 0)
    page.goto(today)
    wait_for_page_caches(page, 1)

    context.set_offline(True)
    page.goto(today)
    check(page.locator("#offline-banner").is_visible(), "offline, Today comes from the cache")
    check(page.get_attribute("html", "lang") == "fr", "offline, the kept page is in French")
    page.goto(f"{base}/more/security/", wait_until="load")
    found = heading(page, "Hors ligne")
    check(found == "Hors ligne", f"the offline page is in French ({found})")
    context.set_offline(False)

    # Back to Automatic, for the next run against the same accounts.
    page.goto(f"{base}/me/preferences/")
    page.select_option("select[name=language]", "")
    page.click("main form button[type=submit]")
    page.wait_for_load_state()
    check(page.get_attribute("html", "lang") == "en", "Automatic gives the browser's again")
    context.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, help="address of the running instance")
    args = parser.parse_args()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        offline_reading(browser, args.base.rstrip("/"))
        language_change(browser, args.base.rstrip("/"))
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
