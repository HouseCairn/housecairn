"""The sign-in page sends nothing with the session cookie that could outlive a sign-in.

    python tests/e2e/sign_in_race_checks.py --base http://localhost:8001

Run by `.github/workflows/ui.yml`. The sign-in changes the session key. A request that left
the browser before, with the old session cookie, and that the server handles after, answers
with a session cookie of its own: an empty one (that session no longer exists) or a new
anonymous session. Arriving after the answer of the sign-in, it replaces the new cookie and the
person is signed out at once, under "Successfully signed in".

The sign-in page starts two requests by itself, which a slow machine may still be handling at
the sign-in:

- the challenge of the passkey waiting in the autofill list (static/js/passkey-login.js): a
  sign-in with the password cancels it, so the browser drops its answer;
- the pages the service worker keeps when it installs (templates/core/service-worker.js):
  they are asked for without cookies, so their answer sets none.
"""

import argparse
import sys
from typing import Any

from browser_checks import check
from playwright.sync_api import Browser, Route, sync_playwright

ADMIN_EMAIL = "admin@example.com"
PASSWORD = "correct-horse-battery-staple"  # throwaway account of the CI instance
CHALLENGE = "**/accounts/2fa/webauthn/login/"


def passkey_challenge_cancelled(browser: Browser, base: str) -> None:
    context = browser.new_context()
    page = context.new_page()
    held: list[Route] = []
    failed: list[str] = []
    # The challenge stays on its way: the instance never gets it.
    page.route(CHALLENGE, lambda route: held.append(route))
    page.on(
        "requestfailed",
        lambda request: (
            failed.append(request.failure or "")
            if "/accounts/2fa/webauthn/login/" in request.url
            else None
        ),
    )
    page.goto(f"{base}/accounts/login/")
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(50)
    check(bool(held), "the sign-in page asks for the passkey challenge")
    page.fill("input[name=login]", ADMIN_EMAIL)
    page.fill("input[name=password]", PASSWORD)
    page.click("main form:has(input[name=login]) button[type=submit]")
    check(
        bool(failed),
        f"the sign-in with the password cancels the passkey challenge still on its way {failed}",
    )
    check("/accounts/login/" not in page.url, f"signed in ({page.url})")
    context.close()


def kept_pages_without_cookies(browser: Browser, base: str) -> None:
    context = browser.new_context()
    page = context.new_page()
    kept: list[Any] = []
    context.on(
        "request",
        lambda request: (
            kept.append(request) if request.service_worker and "/offline/" in request.url else None
        ),
    )
    page.goto(f"{base}/accounts/login/")
    # The page is controlled once the service worker has installed, so has kept its pages.
    for _ in range(300):
        if page.evaluate("navigator.serviceWorker.controller !== null"):
            break
        page.wait_for_timeout(50)
    check(len(kept) >= 1, f"the service worker keeps the offline pages ({len(kept)} asked for)")
    for request in kept:
        cookie = request.all_headers().get("cookie", "")
        check("sessionid" not in cookie, f"{request.url} is asked for without the session cookie")
    context.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, help="address of the running instance")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        passkey_challenge_cancelled(browser, base)
        kept_pages_without_cookies(browser, base)
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
