"""The offer to receive the reminders on this device, in Chromium (docs/adr/0072).

Run in CI by `.github/workflows/ui.yml`, against a running instance with notifications and
Web Push on and an account with a home:

    python tests/e2e/device_offer_checks.py --base http://localhost:8006

`PushManager` and `Notification` are replaced by simulated ones (no push service is reached),
so what is checked is the logic of static/js/webpush.js and its dialogue with the API of the
instance: when the offer shows, what the tap does and in which order, the automatic name of a
device, "Later" and "Do not ask again". What only a real phone can show is listed in
docs/adr/0072. No screenshots (docs/adr/0017).
"""

import argparse
import json
import sys
import time
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from playwright.sync_api import Browser, BrowserContext, Page, Route, sync_playwright

ACCOUNT = ("offer@example.com", "correct-horse-battery-staple")
ENDPOINT = "https://fcm.googleapis.com/fcm/send/e2e-fixture-not-an-endpoint"
API = "/api/v1/me/push-devices/"

CHROME_LINUX = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
CHROME_ANDROID = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Mobile Safari/537.36"
)
FIREFOX_WINDOWS = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0"
EDGE_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
)
SAFARI_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.5 Safari/605.1.15"
)
IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.5 Mobile/15E148 Safari/604.1"
)
IPAD = (
    "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.5 Mobile/15E148 Safari/604.1"
)


def say(message: str) -> None:
    sys.stdout.write(message + "\n")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAILED: {message}")
    say(f"ok: {message}")


def b64url(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def subscription_keys() -> tuple[str, str]:
    """A real P-256 public key and a 16-octet secret: the API checks both."""
    public = ec.generate_private_key(ec.SECP256R1()).public_key()
    point = public.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return b64url(point), b64url(b"0123456789abcdef")


# What the browser has, simulated. `state` is read back by the checks as `window.__push`; the
# subscription survives a reload (localStorage), as a real one does.
FAKE = """
(() => {
  const config = %s;
  const stored = window.localStorage.getItem("fake:sub");
  const state = {
    permission: window.localStorage.getItem("fake:permission") || config.permission,
    result: config.result,
    sub: stored ? JSON.parse(stored) : config.sub,
    calls: [],
  };
  window.__push = state;
  const subscription = (data) => ({
    endpoint: data.endpoint,
    toJSON: () => ({ endpoint: data.endpoint, keys: { p256dh: data.p256dh, auth: data.auth } }),
    unsubscribe: async () => {
      state.calls.push("unsubscribe");
      state.sub = null;
      window.localStorage.removeItem("fake:sub");
      return true;
    },
  });
  // Notification is simulated for every browser, so that "unsupported" is PushManager alone.
  class FakeNotification {
    static get permission() { return state.permission; }
    static requestPermission() {
      state.calls.push("request");
      state.permission = state.result;
      window.localStorage.setItem("fake:permission", state.result);
      return Promise.resolve(state.result);
    }
  }
  window.Notification = FakeNotification;
  if (!config.supported) {
    delete window.PushManager;
  } else {
    const manager = {
      getSubscription: async () => (state.sub ? subscription(state.sub) : null),
      subscribe: async () => {
        state.calls.push("subscribe");
        state.sub = { endpoint: config.endpoint, p256dh: config.p256dh, auth: config.auth };
        window.localStorage.setItem("fake:sub", JSON.stringify(state.sub));
        return subscription(state.sub);
      },
    };
    Object.defineProperty(ServiceWorkerRegistration.prototype, "pushManager", {
      get: () => manager,
      configurable: true,
    });
  }
  if (config.installed) {
    Object.defineProperty(navigator, "standalone", { get: () => true });
  }
})();
"""


class Case:
    """One browser context with the simulated push, signed in by the cookies of `cookies`."""

    def __init__(
        self,
        browser: Browser,
        base: str,
        cookies: list[Any],
        *,
        agent: str = CHROME_LINUX,
        touch: bool = False,
        **options: Any,
    ) -> None:
        public, auth = subscription_keys()
        config = {
            "supported": True,
            "permission": "default",
            "result": "granted",
            "sub": None,
            "installed": False,
            "endpoint": ENDPOINT,
            "p256dh": public,
            "auth": auth,
            **options,
        }
        self.base = base
        self.context: BrowserContext = browser.new_context(user_agent=agent, has_touch=touch)
        self.context.add_cookies(cookies)
        self.context.add_init_script(FAKE % json.dumps(config))
        self.page: Page = self.context.new_page()
        self.posted: list[dict[str, Any]] = []

    def capture(self, status: int = 201) -> None:
        """Answer the API ourselves and keep what the script sent."""

        def handle(route: Route) -> None:
            if route.request.method != "POST":
                route.continue_()
                return
            self.posted.append(json.loads(route.request.post_data or "{}"))
            route.fulfill(status=status, content_type="application/json", body="{}")

        self.page.route(f"**{API}", handle)

    def open(self, path: str = "/") -> None:
        self.page.goto(self.base + path)
        self.page.wait_for_load_state("load")

    def offer_shown(self) -> bool:
        # The script reveals the offer after it looked at the browser: give it a moment.
        try:
            self.page.wait_for_selector("[data-webpush-offer]:not([hidden])", timeout=3000)
            return True
        except Exception:
            return False

    def settle(self) -> None:
        """Wait for the script to have decided when it is not going to show the offer."""
        self.page.wait_for_function("navigator.serviceWorker.ready.then(() => true)", timeout=15000)
        self.page.wait_for_timeout(500)

    def state(self) -> dict[str, Any]:
        return dict(self.page.evaluate("window.__push"))

    def visible(self, hook: str) -> bool:
        return bool(self.page.locator(f"[data-webpush-offer] [{hook}]").first.is_visible())

    def close(self) -> None:
        self.context.close()


def sign_in(browser: Browser, base: str) -> list[Any]:
    context = browser.new_context()
    page = context.new_page()
    page.goto(f"{base}/accounts/login/")
    page.fill("input[name=login]", ACCOUNT[0])
    page.fill("input[name=password]", ACCOUNT[1])
    page.click("main form:has(input[name=login]) button[type=submit]")
    page.wait_for_url("**/today/")
    cookies = context.cookies()
    context.close()
    return cookies


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    base = parser.parse_args().base.rstrip("/")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        cookies = sign_in(browser, base)

        # 1. Offered, and nothing asked on load.
        case = Case(browser, base, cookies)
        case.open()
        check(case.offer_shown(), "the offer shows on Today for a browser that can receive")
        check(case.visible("data-offer-enable"), "it has a button to turn on")
        check(case.visible("data-offer-later"), "it has a button for later")
        check(case.visible("data-offer-never"), "it has a button to never ask again")
        check(
            case.state()["calls"] == [],
            "the permission is not asked, nor anything subscribed, on load",
        )
        check(not case.visible("data-offer-install"), "no install advice on a browser that can")

        # 2. One tap: permission, subscription, the device, with a name of its own.
        case.page.click("[data-offer-enable]")
        case.page.wait_for_selector("[data-offer-done]:not([hidden])", timeout=10000)
        check(
            case.state()["calls"] == ["request", "subscribe"],
            "the tap asks the permission, then subscribes, once each",
        )
        check(not case.visible("data-offer-enable"), "the buttons are gone once it is done")
        listed = case.context.request.get(base + API).json()
        names = [d["name"] for d in (listed["results"] if isinstance(listed, dict) else listed)]
        check(names == ["Chrome on Linux"], f"the device is added with its own name ({names})")
        case.open()
        case.settle()
        check(
            not case.offer_shown(),
            "once this browser is a device of the person, the offer does not come back",
        )
        case.open("/me/notifications/")
        check(
            case.page.locator(".row-title", has_text="Chrome on Linux").count() == 1,
            "the device is in Notifications, where it can be renamed",
        )
        case.close()

        # 2b. A browser still subscribed whose device was deleted is offered again.
        case = Case(
            browser,
            base,
            cookies,
            sub={
                "endpoint": ENDPOINT + "-gone",
                **dict(zip(("p256dh", "auth"), subscription_keys(), strict=True)),
            },
        )
        case.open()
        check(case.offer_shown(), "a subscribed browser the server does not know is offered")
        case.close()

        # 3. The name, from the browser.
        for agent, installed, expected in (
            (CHROME_ANDROID, False, "Chrome on Android"),
            (FIREFOX_WINDOWS, False, "Firefox on Windows"),
            (EDGE_WINDOWS, False, "Edge on Windows"),
            (SAFARI_MAC, False, "Safari on macOS"),
            (IPHONE, True, "iPhone"),
            (IPAD, True, "iPad"),
        ):
            case = Case(browser, base, cookies, agent=agent, installed=installed)
            case.capture()
            case.open()
            check(case.offer_shown(), f"offered to {expected}")
            case.page.click("[data-offer-enable]")
            case.page.wait_for_selector("[data-offer-done]:not([hidden])", timeout=10000)
            check(
                [p["name"] for p in case.posted] == [expected],
                f"the automatic name is {expected!r}",
            )
            check(
                sorted(case.posted[0]) == ["auth", "endpoint", "name", "p256dh"],
                "the subscription is sent as the API wants it",
            )
            case.close()

        # 4. Refused permission: a clear message, nothing saved.
        case = Case(browser, base, cookies, result="denied")
        case.capture()
        case.open()
        check(case.offer_shown(), "offered before the answer")
        case.page.click("[data-offer-enable]")
        case.page.wait_for_selector("[data-offer-denied]:not([hidden])", timeout=10000)
        check(case.posted == [], "a refused permission saves nothing")
        check("subscribe" not in case.state()["calls"], "and subscribes to nothing")
        check(case.visible("data-offer-enable"), "the offer stays, to try again")
        case.close()

        # 5. The server refuses: a clear message, nothing saved, no subscription left.
        case = Case(browser, base, cookies)
        case.capture(status=500)
        case.open()
        case.page.click("[data-offer-enable]")
        case.page.wait_for_selector("[data-offer-failed]:not([hidden])", timeout=10000)
        check(
            case.state()["calls"] == ["request", "subscribe", "unsubscribe"],
            "a failure gives the subscription back",
        )
        check(not case.visible("data-offer-done"), "and does not say it worked")
        case.close()

        # 5b. The server refuses and the browser already had a subscription (a device that
        # works, or one of another account): it is not ours to give back.
        known = {
            "endpoint": ENDPOINT + "-other",
            **dict(zip(("p256dh", "auth"), subscription_keys(), strict=True)),
        }
        case = Case(browser, base, cookies, sub=known)
        case.capture(status=500)
        case.open()
        check(case.offer_shown(), "a subscribed browser the server does not know is offered")
        case.page.click("[data-offer-enable]")
        case.page.wait_for_selector("[data-offer-failed]:not([hidden])", timeout=10000)
        check(
            "unsubscribe" not in case.state()["calls"],
            "on Today, a refusal does not give back a subscription that was already there",
        )
        check(case.state()["sub"] is not None, "on Today, the earlier subscription is kept")
        case.close()

        case = Case(browser, base, cookies, sub=known)
        case.capture(status=500)
        case.open("/me/notifications/")
        case.page.fill("#webpush-name", "My phone")
        case.page.click("form[data-webpush] button[type=submit]")
        case.page.wait_for_selector("[data-webpush-failed]:not([hidden])", timeout=10000)
        check(
            "unsubscribe" not in case.state()["calls"],
            "in Notifications, a refusal does not give back a subscription that was already there",
        )
        check(case.state()["sub"] is not None, "in Notifications, the earlier subscription is kept")
        case.close()

        # 5c. The same refusal without an earlier subscription gives back the one just made.
        case = Case(browser, base, cookies)
        case.capture(status=500)
        case.open("/me/notifications/")
        case.page.fill("#webpush-name", "My phone")
        case.page.click("form[data-webpush] button[type=submit]")
        case.page.wait_for_selector("[data-webpush-failed]:not([hidden])", timeout=10000)
        check(
            case.state()["calls"] == ["request", "subscribe", "unsubscribe"],
            "in Notifications, a refusal gives back the subscription this tap created",
        )
        case.close()

        # 6. Permission already refused: no offer; Notifications says how to unblock.
        case = Case(browser, base, cookies, permission="denied")
        case.open()
        case.settle()
        check(not case.offer_shown(), "no offer when the browser blocks notifications")
        case.open("/me/notifications/")
        case.page.wait_for_selector("[data-webpush-blocked]:not([hidden])", timeout=5000)
        check(True, "Notifications says how to turn them back on")
        case.close()
        case = Case(browser, base, cookies)
        case.open("/me/notifications/")
        case.settle()
        check(
            not case.page.locator("[data-webpush-blocked]").first.is_visible(),
            "that line is not shown when nothing is blocked",
        )
        case.close()

        # 7. A browser that cannot receive: no offer.
        case = Case(browser, base, cookies, supported=False)
        case.open()
        case.settle()
        check(not case.offer_shown(), "no offer on a browser without Web Push")
        case.close()

        # 8. iPhone or iPad in Safari, not installed: how to install, no button to turn on.
        case = Case(browser, base, cookies, agent=IPHONE, supported=False, touch=True)
        case.open()
        check(case.offer_shown(), "an iPhone in Safari is told something")
        check(case.visible("data-offer-install"), "it is how to install the application")
        check(not case.visible("data-offer-enable"), "without a button to turn on")
        check(
            "Add to Home Screen" in case.page.inner_text("[data-offer-install]"),
            "Share, Add to Home Screen",
        )
        case.page.click("[data-offer-install] [data-offer-never]")
        case.open()
        case.settle()
        check(not case.offer_shown(), "and it can be silenced")
        case.close()

        # 9. Later: 7 days.
        case = Case(browser, base, cookies)
        case.open()
        before = time.time() * 1000
        case.page.click("[data-offer-enable] ~ [data-offer-later]")
        check(not case.offer_shown(), "Later hides the offer")
        later = case.page.evaluate(
            "Object.entries(localStorage)"
            ".filter(([k]) => k.startsWith('housecairn:webpush:later'))"
            ".map(([, v]) => Number(v))"
        )
        week = 7 * 24 * 3600 * 1000
        check(
            len(later) == 1 and week - 5000 < later[0] - before < week + 60000,
            "and postpones it by seven days",
        )
        case.open()
        case.settle()
        check(not case.offer_shown(), "the offer does not come back on the next page")
        case.page.evaluate(
            "Object.keys(localStorage)"
            ".filter((k) => k.startsWith('housecairn:webpush:later'))"
            ".forEach((k) => localStorage.setItem(k, String(Date.now() - 1000)))"
        )
        case.open()
        check(case.offer_shown(), "it comes back once the seven days have passed")
        case.close()

        # 10. Do not ask again: this browser only.
        case = Case(browser, base, cookies)
        case.open()
        case.page.click("[data-offer-ask] [data-offer-never]")
        check(not case.offer_shown(), "Do not ask again hides the offer")
        case.open()
        case.settle()
        check(not case.offer_shown(), "and it never comes back in this browser")
        other = Case(browser, base, cookies)
        other.open()
        check(other.offer_shown(), "another browser is still offered")
        other.close()
        case.close()

        browser.close()
    say("all checks passed")


if __name__ == "__main__":
    main()
