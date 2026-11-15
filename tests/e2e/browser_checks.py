"""Browser checks run in CI (`.github/workflows/ui.yml`), against a running instance.

Playwright is not a dependency of the project: the workflow installs a pinned
version for this job only.

    python tests/e2e/browser_checks.py passkey --base http://localhost:8001
    python tests/e2e/browser_checks.py sso --base http://localhost:8001 --provider http://127.0.0.1:8002

No screenshots until the dedicated lot before the public release (docs/adr/0017).
`passkey` replays the sign-in with a virtual authenticator (Chromium's WebAuthn
emulation): it registers a passkey, then signs in through the conditional
(autofill) path and through the fallback button.
What no script can do, the real autofill pop-up of each browser, is listed in
docs/adr/0018. `sso` clicks the provider button of the sign-in page once and checks that
the browser lands on the provider, with no confirmation page of the application in between.
`in-place` (local only, not in the workflow) checks that Done and Undo on Today leave the
page where it was, keep the focus on the row and show the banner above the bar. The
banner of a stock shortage (a long message, French, 360 and 320 px) must still leave the
last button of the page reachable, with script and without: with script the page keeps the
banner's real height, without it the banner is a sticky box that takes its own room.

    python tests/e2e/browser_checks.py in-place --base http://localhost:8001

`item-contract` (local only) measures the item form at 375 and 1280 px, in English and in French,
with and without script: the provider of the maintenance contract is a list of the contractors
whose last choice adds one in place, and the sheet of the item links to the contractor.

    python tests/e2e/browser_checks.py item-contract --base http://localhost:8001

`home-archive` (local only) archives a home and brings it back at 375 and 1280 px, in English and
in French, with and without script (docs/adr/0079): the confirmation pages, the **Archived** mark on
the home and the list, no sideways scroll, touch targets. Needs the account `demo@example.com`,
owner of one home.

    python tests/e2e/browser_checks.py home-archive --base http://localhost:8001

`about-links` (local only) measures the links of the About screen at 375 and 1280 px, in English
and in French, with and without script: the six links carry a safe `rel`, the iOS and Android icons
are marked as coming soon and are not links, every tile is a 44 px touch target inside the width,
and the page asks nothing of another host. It needs no account.

    python tests/e2e/browser_checks.py about-links --base http://localhost:8001

`task-form` and `task-lines` (local only) measure the task form at 320, 360, 375 and 1280 px,
in French, with and without script: the repetition, the duration and the contractor, then the
room, item and stock item added in place and the lines of the materials.

`form-errors` (CI) refuses a task form with two errors, one of them in a folded section, and
checks, with script, with HTMX swapping the page in place, and without script, that the summary
of the errors is on screen and holds the focus, that the section is unfolded and that a line of
the summary puts the focus in its field. `instance-tabs` (CI) measures the tabs of the instance
administration at 360, 375, 768 and 1280 px, in English and in French: no tab text runs into its
neighbour and no word of a label is cut over two lines. `form-errors` signs in as
`forms@example.com`, `instance-tabs` as the administrator `admin@example.com`: both are accounts
of the CI instance.

`demo-accounts` needs an instance with `HOUSECAIRN_DEMO` on. It measures the box of the public
accounts on the sign-in page at 320, 375 and 1280 px, in English and French: the sentence, the
accounts and the password are stacked, and each address and the password are drawn on one line,
inside the box.

    python tests/e2e/browser_checks.py demo-accounts --base http://localhost:8005

`background-calls` (local only) measures the link being read and the test of a channel at 320,
360, 375 and 1280 px, in English and French, with and without script (docs/adr/0070).
"""

import argparse
import sys
from itertools import pairwise
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import Browser, BrowserContext, Locator, Page, sync_playwright

EMAIL = "demo@example.com"
PASSWORD = "correct-horse-battery-staple"  # throwaway account of the CI instance
# The account of the form checks: the passkey check leaves a passkey on `EMAIL`, which then asks
# for it at every sign-in.
FORMS_EMAIL = "forms@example.com"


def sign_in(page: Page, base: str, email: str = EMAIL) -> None:
    page.goto(f"{base}/accounts/login/")
    page.fill("input[name=login]", email)
    page.fill("input[name=password]", PASSWORD)
    page.click("main form:has(input[name=login]) button[type=submit]")
    page.wait_for_url("**/today/")


def sign_out(page: Page, base: str) -> None:
    page.goto(f"{base}/more/")
    page.click("button:has-text('Sign out')")
    page.wait_for_url(f"{base}/")


def say(message: str) -> None:
    sys.stdout.write(message + "\n")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAILED: {message}")
    say(f"ok: {message}")


def virtual_authenticator(context: BrowserContext, page: Page) -> None:
    session = context.new_cdp_session(page)
    session.send("WebAuthn.enable")
    session.send(
        "WebAuthn.addVirtualAuthenticator",
        {
            "options": {
                "protocol": "ctap2",
                "transport": "internal",
                "hasResidentKey": True,
                "hasUserVerification": True,
                "isUserVerified": True,
                "automaticPresenceSimulation": True,
            }
        },
    )


def passkey(browser: Browser, base: str) -> None:
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    virtual_authenticator(context, page)

    # The page itself: no passkey button, the autofill token on the address field.
    page.goto(f"{base}/accounts/login/")
    check(page.locator("#passkey_login").count() == 0, "no element #passkey_login")
    check("Sign in with a passkey" not in page.content(), "no 'Sign in with a passkey' text")
    check(
        page.get_attribute("input[name=login]", "autocomplete") == "username webauthn",
        "address field has autocomplete='username webauthn'",
    )

    # Register a passkey (a discoverable credential) while signed in with the password.
    sign_in(page, base)
    page.goto(f"{base}/accounts/2fa/webauthn/add/")
    if "reauthenticate" in page.url:
        page.fill("input[name=password]", PASSWORD)
        page.click("main form button[type=submit]")
        page.wait_for_load_state()
    page.check("input[type=checkbox]")
    page.click("button:has-text('Add')")
    page.wait_for_url("**/recovery-codes/")
    check(True, "passkey registered")
    sign_out(page, base)

    # Conditional mediation: opening the sign-in page signs in with no click at all.
    page.goto(f"{base}/accounts/login/")
    page.wait_for_url("**/today/", timeout=15000)
    check(True, "conditional (autofill) passkey sign-in reached Today")
    sign_out(page, base)

    # Fallback: a browser without conditional mediation gets a button once an address is typed.
    page.add_init_script("PublicKeyCredential.isConditionalMediationAvailable = async () => false")
    page.goto(f"{base}/accounts/login/")
    offer = page.locator(".passkey-offer button")
    page.wait_for_function("document.querySelector('.passkey-offer')")
    check(not offer.is_visible(), "no offer before an address is typed")
    page.fill("input[name=login]", "someone@example.com")
    check(offer.is_visible(), "offer shown once an address is typed, whatever the address")
    offer.click()
    page.wait_for_url("**/today/", timeout=15000)
    check(True, "fallback button signs in with the passkey")
    context.close()


def sso(browser: Browser, base: str, provider: str) -> None:
    """One click from the sign-in page to the provider (a stub that serves the discovery)."""
    page = browser.new_page(viewport={"width": 390, "height": 844})
    navigations: list[str] = []
    page.on(
        "request",
        lambda request: (
            navigations.append(f"{request.method} {request.url}")
            if request.is_navigation_request()
            else None
        ),
    )
    page.goto(f"{base}/accounts/login/")
    button = page.locator("form button:has-text('Continue with Company SSO')")
    check(button.count() == 1, "one provider button on the sign-in page")
    navigations.clear()
    button.click()
    page.wait_for_url(f"{provider}/authorize*", timeout=15000)
    check(True, "one click reached the provider")
    check(
        navigations[0].startswith("POST ") and "/accounts/oidc/" in navigations[0],
        f"the click posts to the provider login ({navigations[0]})",
    )
    check(
        not any("/accounts/oidc/" in n and n.startswith("GET ") for n in navigations),
        "no confirmation page between the button and the provider",
    )
    check("state=" in page.url and "code_challenge=" in page.url, "state and PKCE are sent")
    page.close()


def reach_the_end_of_the_list(page: Page, row_id: str) -> None:
    """The banner of Undo must not cover the last button of the page (NAV-05).

    Mark a row done, go to the bottom, and compare the last button of `main` with the
    banner: not one pixel of the button may be under it (a click on its edge would miss).
    """
    page.locator(f"#{row_id} button.button-done").click()
    page.wait_for_selector("#undo-region .undo-banner button")
    last_button_is_reachable(page)
    page.locator("#undo-region .undo-banner button").click()
    page.wait_for_selector(f"#{row_id}:not(.task-row-closed) button.button-done")
    page.evaluate("window.scrollTo(0, 0)")


def last_button_is_reachable(page: Page) -> None:
    """With the banner shown, go to the bottom: nothing covers the last button of `main`."""
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    page.wait_for_timeout(200)
    covered = page.evaluate(
        """() => {
            const buttons = [...document.querySelectorAll('main a.button, main button')]
                .filter((el) => el.offsetParent !== null && !el.closest('#undo-region'));
            const last = buttons[buttons.length - 1];
            if (!last) return 'no button in main';
            const box = last.getBoundingClientRect();
            const banner = document.querySelector('#undo-region .undo-banner');
            if (!banner) return '';
            const over = banner.getBoundingClientRect();
            const hidden = Math.round(box.bottom - over.top);
            return hidden > 0 ? hidden + ' px of the button under the banner' : '';
        }"""
    )
    check(covered == "", f"the last button of the page is not covered by the banner ({covered})")
    outside = page.evaluate(
        """() => {
            const banner = document.querySelector('#undo-region .undo-banner');
            if (!banner) return '';
            const box = banner.getBoundingClientRect();
            const out = Math.max(-box.top, box.bottom - window.innerHeight);
            return out > 0 ? Math.round(out) + ' px of the banner out of the view' : '';
        }"""
    )
    check(outside == "", f"the banner stays in the view ({outside})")
    last = page.evaluate_handle(
        """() => {
            const buttons = [...document.querySelectorAll('main a.button, main button')]
                .filter((el) => el.offsetParent !== null && !el.closest('#undo-region'));
            return buttons[buttons.length - 1];
        }"""
    ).as_element()
    if last is not None:
        # A trial click runs every check of a real click (visible, stable, receives the
        # events at its centre) without pressing: a banner over the button refuses it.
        last.click(trial=True, timeout=2000)
        check(True, "the last button of the page takes the click")


def in_place(browser: Browser, base: str) -> None:
    """Done and Undo keep the person where they are (NAV-05). Local only, not in CI.

    Needs the demo account with a home holding at least 15 open tasks due today:

        uv run python manage.py shell -c "
        from housecairn.accounts.models import User
        from housecairn.homes import services as homes
        from housecairn.tasks import services as tasks
        user = User.objects.create_user(email='demo@example.com', password='...')
        home = homes.create_home(user, name='Main house')
        for n in range(20):
            tasks.create_task(user, home, title=f'Task {n + 1}', due_on=tasks.today())
        article = stock.create_article(user, home, name='Rien en stock', unit='piece')
        task = tasks.create_task(user, home, title='Stock task', due_on=tasks.today())
        materials.add_material(user, task, article, Decimal('2'))
        "

    (with `from decimal import Decimal`, `from housecairn.stock import services as stock` and
    `from housecairn.tasks import materials` in the imports above.)
    """
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    errors: list[str] = []
    page.on(
        "console", lambda message: errors.append(message.text) if message.type == "error" else None
    )
    sign_in(page, base)
    rows = page.locator("li.task-row:has(button.button-done)")
    check(rows.count() >= 15, "Today lists at least 15 open tasks")
    row = rows.nth(12)
    row_id = row.get_attribute("id") or ""
    check(row_id.startswith("occ-"), "a row is named after its occurrence")
    row.scroll_into_view_if_needed()
    page.mouse.wheel(0, 120)
    page.wait_for_timeout(200)
    before = page.evaluate("window.scrollY")
    check(before > 300, f"the page is scrolled down ({before} px)")
    address = page.url
    row.locator("button.button-done").click()
    page.wait_for_selector(f"#{row_id}.task-row-closed")
    page.wait_for_selector("#undo-region .undo-banner button")
    after = page.evaluate("window.scrollY")
    check(abs(after - before) <= 1, f"Done does not move the page ({before} -> {after} px)")
    check(page.url == address, "Done leaves the address as it was")
    focused = page.evaluate(
        "document.activeElement.closest('li') && document.activeElement.closest('li').id"
    )
    check(focused == row_id, "the focus stays on the row")
    banner = page.locator("#undo-region .undo-banner").bounding_box()
    bar = page.locator("nav.nav").bounding_box()
    check(
        banner is not None and bar is not None and banner["y"] + banner["height"] <= bar["y"],
        "the banner sits above the bar at the bottom",
    )
    check(
        "Marked done." in (page.locator("#undo-region").text_content() or ""),
        "the live region says what happened",
    )
    page.locator("#undo-region .undo-banner button").click()
    page.wait_for_selector(f"#{row_id}:not(.task-row-closed) button.button-done")
    check(abs(page.evaluate("window.scrollY") - before) <= 1, "Undo does not move the page")
    check(
        page.evaluate("document.activeElement.classList.contains('button-done')")
        and page.evaluate("document.activeElement.closest('li').id") == row_id,
        "after Undo the focus is on the Done button of the row",
    )
    check("Undone." in (page.locator("#undo-region").text_content() or ""), "Undo is announced")
    check(page.url == address, "Undo leaves the address as it was")
    reach_the_end_of_the_list(page, row_id)
    check(not errors, f"no error in the console (CSP included): {errors}")
    context.close()
    for width in (360, 320):
        stock_shortage_banner(browser, base, width)
    for width in (360, 320):
        stock_shortage_banner(browser, base, width, script=False)


def stock_shortage_banner(browser: Browser, base: str, width: int, script: bool = True) -> None:
    """The banner of a stock shortage, in French, must not hide the end of Today (NAV-05).

    The message is long: on a narrow screen the banner is three or four lines high. With
    script the room kept for it follows its real height; without script Done is a form
    post and a redirect, the session gives the banner back on the next page, and the banner
    takes its own room (a sticky box at the end of the page).
    """
    label = f"{width} px{'' if script else ', no script'}"
    context = browser.new_context(
        viewport={"width": width, "height": 812}, locale="fr-FR", java_script_enabled=script
    )
    page = context.new_page()
    sign_in(page, base)
    row = page.locator("li.task-row:has-text('Stock task')")
    check(row.count() == 1, f"{label}: Today lists the task that needs an empty stock item")
    row_id = row.get_attribute("id") or ""
    page.locator(f"#{row_id} button.button-done").click()
    if not script:
        page.wait_for_url("**/today/**")
    page.wait_for_selector("#undo-region .undo-banner button")
    text = page.locator("#undo-region").text_content() or ""
    check("Stock insuffisant" in text, f"{label}: the banner says the stock is short")
    box = page.locator("#undo-region .undo-banner").bounding_box()
    say(f"{label}: the banner is {box['height'] if box else 0:.0f} px high")
    last_button_is_reachable(page)
    page.locator("#undo-region .undo-banner button").click()
    if not script:
        page.wait_for_url("**/today/**")
    page.wait_for_selector(f"#{row_id}:not(.task-row-closed) button.button-done")
    context.close()


TASK_FORM_WIDTHS = (320, 360, 375, 1280)


def _open_task_form(page: Page, base: str) -> None:
    page.goto(f"{base}/tasks/")
    page.locator("main a:has-text('Purger les radiateurs')").first.click()
    page.locator("main a[href*='/edit/']").first.click()
    page.wait_for_selector("form[data-task-form]")
    section = page.locator("details.form-section:has(#id_repeats)")
    if section.get_attribute("open") is None:
        section.locator("summary").click()


def _shown(page: Page, selector: str) -> bool:
    return page.locator(selector).first.is_visible()


def task_form(browser: Browser, base: str) -> None:
    """The repetition of the task form in one sentence, in French. Local only, not in CI.

    Needs the demo account with a home holding a task titled "Purger les radiateurs et
    vérifier la pression de la chaudière", repeating every 3 months from 31 January 2027,
    with custom months from November to February. At each width, with script: no
    horizontal scroll, the date field only when counting from the due date, the months only
    with Custom months, the summary that follows the fields. Without script: every field.
    """
    for width in TASK_FORM_WIDTHS:
        for script in (True, False):
            label = f"{width} px{'' if script else ', no script'}"
            context = browser.new_context(
                viewport={"width": width, "height": 900}, locale="fr-FR", java_script_enabled=script
            )
            page = context.new_page()
            errors: list[str] = []
            page.on(
                "console",
                lambda message, errors=errors: (
                    errors.append(message.text) if message.type == "error" else None
                ),
            )
            sign_in(page, base)
            _open_task_form(page, base)
            page_width = page.evaluate("document.documentElement.scrollWidth")
            check(page_width <= width, f"{label}: no horizontal scroll ({page_width} px)")
            summary = page.locator("#repeat-summary").text_content() or ""
            check("Le 31 de chaque trimestre" in summary, f"{label}: the summary: {summary}")
            check(_shown(page, "#id_repeat_start"), f"{label}: the date field, counting from it")
            check(page.locator("#id_repeat_start").get_attribute("type") == "date", "a calendar")
            check(_shown(page, "#id_season_start_month"), f"{label}: the months, custom season")
            if not script:
                check(
                    _shown(page, "text=compté depuis le jour où la tâche est marquée faite"),
                    f"{label}: every field shows, the explanation of after the last time too",
                )
                context.close()
                continue
            check(
                not _shown(page, "text=compté depuis le jour où la tâche est marquée faite"),
                f"{label}: the explanation of after the last time is hidden",
            )
            page.locator("#id_repeat_from_1").check()
            page.wait_for_selector("#repeat-summary:has-text('Prochaine fois')")
            check(not _shown(page, "#id_repeat_start"), f"{label}: no date field after the last")
            check(
                _shown(page, "text=compté depuis le jour où la tâche est marquée faite"),
                f"{label}: the period is said",
            )
            say(f"{label}: {page.locator('#repeat-summary').text_content()}")
            page.select_option("#id_season", "spring")
            check(not _shown(page, "#id_season_start_month"), f"{label}: no months for spring")
            page.locator("#id_repeats").uncheck()
            check(not _shown(page, "#id_repeat_interval"), f"{label}: nothing more, once")
            page.wait_for_selector("#repeat-summary:has-text('Pas de répétition')")
            page.locator("#id_repeats").check()
            page.locator("#id_repeat_from_0").check()
            page.fill("#id_repeat_start", "2028-02-29")
            check(page.input_value("#id_due_on") == "2028-02-29", f"{label}: the due date follows")
            page_width = page.evaluate("document.documentElement.scrollWidth")
            check(page_width <= width, f"{label}: still no horizontal scroll ({page_width} px)")
            check(not errors, f"{label}: no error in the console (CSP included): {errors}")
            context.close()
    duration_and_contractor(browser, base)
    new_records_and_materials(browser, base)


def _open_section(page: Page, field: str) -> None:
    section = page.locator(f"details.form-section:has({field})")
    if section.get_attribute("open") is None:
        section.locator("summary").click()


def _box(page: Page, selector: str) -> dict[str, float]:
    box = page.locator(selector).bounding_box()
    check(box is not None, f"{selector} is drawn")
    assert box is not None
    return box


def duration_and_contractor(browser: Browser, base: str) -> None:
    """The duration on one line from 360 px, the contractor added in place. Local only.

    At each width, with and without script, in French: the number of the duration is wider
    than its unit and, from 360 px, on the same line; the units go up to years. With script,
    "Ajouter un intervenant…" shows the fields of a new one, hidden before; without script
    they are always there. Prints the measures.
    """
    for width in TASK_FORM_WIDTHS:
        for script in (True, False):
            label = f"{width} px{'' if script else ', no script'}"
            context = browser.new_context(
                viewport={"width": width, "height": 900}, locale="fr-FR", java_script_enabled=script
            )
            page = context.new_page()
            sign_in(page, base)
            page.goto(f"{base}/tasks/")
            page.locator("main a[href*='/tasks/new/']").first.click()
            page.wait_for_selector("form[data-task-form]")
            _open_section(page, "#id_duration_value")
            number, unit = _box(page, "#id_duration_value"), _box(page, "#id_duration_unit")
            one_line = abs(number["y"] - unit["y"]) < 2
            say(
                f"{label}: duration number {number['width']:.0f} px, unit {unit['width']:.0f} px,"
                f" {'one line' if one_line else 'two lines'}"
            )
            check(number["width"] > unit["width"], f"{label}: the number is wider than the unit")
            if width >= 360:
                check(one_line, f"{label}: number and unit on one line")
            units = page.locator("#id_duration_unit option").all_text_contents()
            check(units[-3:] == ["semaines", "mois", "ans"], f"{label}: the units: {units}")
            _open_section(page, "#id_contractor")
            options = page.locator("#id_contractor option").all_text_contents()
            check(options[-1] == "Ajouter un intervenant…", f"{label}: the last choice: {options}")
            check(
                _shown(page, "#id_new_contractor_name") is not script,
                f"{label}: the fields of a new contractor {'hidden' if script else 'shown'}",
            )
            if script:
                page.select_option("#id_contractor", "new")
                check(_shown(page, "#id_new_contractor_name"), f"{label}: shown once chosen")
                check(not _shown(page, "#id_new_contractor_trade_other"), f"{label}: no Other")
                page.select_option("#id_new_contractor_trade", "other")
                check(_shown(page, "#id_new_contractor_trade_other"), f"{label}: Which trade")
            page_width = page.evaluate("document.documentElement.scrollWidth")
            check(page_width <= width, f"{label}: no horizontal scroll ({page_width} px)")
            context.close()


def _new_task_page(page: Page, base: str) -> None:
    page.goto(f"{base}/tasks/")
    page.locator("main a[href*='/tasks/new/']").first.click()
    page.wait_for_selector("form[data-task-form]")


def _lines(page: Page) -> int:
    return page.locator("[data-material-line]").count()


def new_records_and_materials(browser: Browser, base: str) -> None:
    """A room, an item and a stock item added in place; the lines of the materials. Local only.

    At each width, with and without script, in French. The last choice of the room, item and
    stock item lists adds one: its fields are hidden until chosen with script (each line has
    its own), always there without. With script, + adds a line (renumbered, up to 50, the limit
    announced in a live region) and - removes one; without, "Ajouter des lignes" sends the
    form back with three more lines and what was typed. At 375 px, with and without script, a
    task is created with the three new records. Prints the measures. Run alone, with any
    demo account that has a home:

        python tests/e2e/browser_checks.py task-lines --base http://localhost:8001
    """
    for width in TASK_FORM_WIDTHS:
        for script in (True, False):
            label = f"{width} px{'' if script else ', no script'}"
            context = browser.new_context(
                viewport={"width": width, "height": 900}, locale="fr-FR", java_script_enabled=script
            )
            page = context.new_page()
            errors: list[str] = []
            page.on(
                "console",
                lambda message, errors=errors: (
                    errors.append(message.text) if message.type == "error" else None
                ),
            )
            sign_in(page, base)
            _new_task_page(page, base)
            rooms = page.locator("#id_room option").all_text_contents()
            items = page.locator("#id_item option").all_text_contents()
            check(rooms[-1] == "Ajouter une pièce…", f"{label}: the last room choice: {rooms}")
            check(items[-1] == "Ajouter un élément…", f"{label}: the last item choice: {items}")
            kinds = page.locator("#id_new_room_kind option").all_text_contents()
            check("Cuisine" in kinds, f"{label}: the types of room, translated: {kinds}")
            for field in ("#id_new_room_name", "#id_new_item_name"):
                check(_shown(page, field) is not script, f"{label}: {field} hidden iff script")
            if script:
                page.select_option("#id_room", "new")
                check(_shown(page, "#id_new_room_name"), f"{label}: the new room's fields shown")
                check(not _shown(page, "#id_new_item_name"), f"{label}: not the item's")
                page.select_option("#id_item", "new")
                check(_shown(page, "#id_new_item_name"), f"{label}: the new item's fields shown")
            name_box = _box(page, "#id_new_room_name")
            say(f"{label}: new room name {name_box['width']:.0f} px wide")
            _open_section(page, "#id_materials-0-article")
            check(_lines(page) == 3, f"{label}: three lines to begin with")
            check(
                _shown(page, "#id_materials-0-new_article_name") is not script,
                f"{label}: the new stock item's fields hidden iff script",
            )
            select = _box(page, "#id_materials-0-article")
            say(f"{label}: stock item select {select['width']:.0f} px wide")
            if script:
                check(not _shown(page, "[data-material-more]"), f"{label}: no Add lines button")
                check(_shown(page, "[data-material-add]"), f"{label}: the + button")
                check(_shown(page, "[data-material-remove]"), f"{label}: the - button")
                page.click("[data-material-add]")
                check(_lines(page) == 4, f"{label}: + adds a line")
                check(
                    page.locator("#id_materials-3-article").count() == 1,
                    f"{label}: the new line is numbered 3",
                )
                page.select_option("#id_materials-3-article", "new")
                check(_shown(page, "#id_materials-3-new_article_name"), f"{label}: its own fields")
                check(
                    not _shown(page, "#id_materials-0-new_article_name"),
                    f"{label}: not those of the first line",
                )
                page.select_option("#id_materials-3-new_article_unit", "other")
                check(
                    _shown(page, "#id_materials-3-new_article_unit_other"), f"{label}: Which unit"
                )
                page.locator("[data-material-remove]").nth(1).click()
                check(_lines(page) == 3, f"{label}: - removes a line")
                check(
                    page.locator("#id_materials-3-article").count() == 0
                    and page.input_value("[name=materials-TOTAL_FORMS]") == "3",
                    f"{label}: renumbered, 3 forms",
                )
                check(
                    page.locator("#id_materials-2-article").input_value() == "new"
                    and _shown(page, "#id_materials-2-new_article_name"),
                    f"{label}: the new stock item moved up with its fields",
                )
                while _lines(page) < 50:
                    page.click("[data-material-add]")
                check(page.locator("[data-material-add]").is_disabled(), f"{label}: + off at 50")
                limit = page.locator("[data-material-limit]")
                check(
                    "50 articles au plus" in (limit.text_content() or "")
                    and limit.get_attribute("aria-live") == "polite",
                    f"{label}: the limit is said in a live region: {limit.text_content()}",
                )
                page.locator("[data-material-remove]").last.click()
                check(
                    page.locator("[data-material-add]").is_enabled()
                    and not (limit.text_content() or "").strip(),
                    f"{label}: - frees a place and clears the notice",
                )
            else:
                check(_shown(page, "[data-material-more]"), f"{label}: the Add lines button")
                check(not _shown(page, "[data-material-add]"), f"{label}: no + without script")
                page.fill("#id_title", "Essai lignes")
                page.select_option("#id_materials-1-article", "new")
                page.fill("#id_materials-1-new_article_name", "Ruban")
                page.click("[data-material-more]")
                page.wait_for_selector("form[data-task-form]")
                check(_lines(page) == 6, f"{label}: three more lines after Add lines")
                check(
                    page.input_value("#id_title") == "Essai lignes"
                    and page.input_value("#id_materials-1-new_article_name") == "Ruban",
                    f"{label}: what was typed is kept",
                )
                check(page.locator(".errorlist").count() == 0, f"{label}: no error shown")
            page_width = page.evaluate("document.documentElement.scrollWidth")
            check(page_width <= width, f"{label}: no horizontal scroll ({page_width} px)")
            check(not errors, f"{label}: no error in the console (CSP included): {errors}")
            if width == 375:
                suffix = "js" if script else "nojs"
                _new_task_page(page, base)
                _open_section(page, "#id_materials-0-article")
                page.fill("#id_title", f"Peindre le mur {suffix}")
                page.select_option("#id_room", "new")
                page.fill("#id_new_room_name", f"Cellier {suffix}")
                page.select_option("#id_new_room_kind", "cellar")
                page.select_option("#id_item", "new")
                page.fill("#id_new_item_name", f"Congélateur {suffix}")
                page.select_option("#id_materials-0-article", "new")
                page.fill("#id_materials-0-new_article_name", f"Pinceau {suffix}")
                page.select_option("#id_materials-0-new_article_unit", "piece")
                page.fill("#id_materials-0-quantity", "2")
                page.click("form[data-task-form] > button.button-block[type=submit] >> nth=-1")
                page.wait_for_url(lambda url: "/tasks/new/" not in url)
                if page.locator("h1").first.text_content() != f"Peindre le mur {suffix}":
                    page.locator(f"main a:has-text('Peindre le mur {suffix}')").first.click()
                body = page.locator("main").inner_text()
                check(
                    f"Cellier {suffix}" in body
                    and f"Congélateur {suffix}" in body
                    and f"Pinceau {suffix}" in body,
                    f"{label}: the task shows its new room, item and stock item",
                )
            context.close()


ITEM_CONTRACT_WIDTHS = (375, 1280)
ITEM_CONTRACT_LANGUAGES = (
    ("en-GB", "Add a contractor…", "Maintenance contract"),
    ("fr-FR", "Ajouter un intervenant…", "Contrat d'entretien"),
)


def item_contract(browser: Browser, base: str) -> None:
    """The provider of the contract of an item: a list, adding one in place. Local only.

    At 375 and 1280 px, in English and in French, with and without script: the last choice
    of the list adds a contractor; with script its fields are hidden until the choice, without
    script they are always there; the list is a touch target; the page does not scroll
    sideways; once saved, the sheet of the item links to the sheet of the contractor.
    Prints the measures.
    """
    serial = 0
    for width in ITEM_CONTRACT_WIDTHS:
        for locale, last, heading in ITEM_CONTRACT_LANGUAGES:
            for script in (True, False):
                serial += 1
                label = f"{width} px, {locale}{'' if script else ', no script'}"
                context = browser.new_context(
                    viewport={"width": width, "height": 900},
                    locale=locale,
                    java_script_enabled=script,
                )
                page = context.new_page()
                sign_in(page, base)
                page.goto(f"{base}/rooms/")
                page.locator("main a.row[href*='/rooms/']").first.click()
                page.locator("main a[href*='/items/']:not([href*='/new/'])").first.click()
                page.locator("main a[href*='/edit/']").first.click()
                page.wait_for_selector("form[data-in-place-form]")
                options = page.locator("#id_contract_contractor option").all_text_contents()
                check(options[-1] == last, f"{label}: the last choice: {options}")
                height = _box(page, "#id_contract_contractor")["height"]
                say(f"{label}: the list is {height:.0f} px high")
                check(height >= 44, f"{label}: the list is a touch target")
                check(
                    _shown(page, "#id_new_contractor_name") is not script,
                    f"{label}: the fields of a new contractor {'hidden' if script else 'shown'}",
                )
                if script:
                    page.select_option("#id_contract_contractor", "new")
                    check(_shown(page, "#id_new_contractor_name"), f"{label}: shown once chosen")
                    check(not _shown(page, "#id_new_contractor_trade_other"), f"{label}: no Other")
                    page.select_option("#id_new_contractor_trade", "other")
                    check(_shown(page, "#id_new_contractor_trade_other"), f"{label}: which trade")
                    page.fill("#id_new_contractor_trade_other", "Pool")
                else:
                    page.select_option("#id_contract_contractor", "new")
                    page.select_option("#id_new_contractor_trade", "plumber")
                page.fill("#id_new_contractor_name", f"Pipes {serial}")
                page_width = page.evaluate("document.documentElement.scrollWidth")
                check(page_width <= width, f"{label}: no horizontal scroll ({page_width} px)")
                page.click("form[data-in-place-form] button[type=submit]")
                page.wait_for_selector("dl.facts")
                facts = page.locator("dl.facts div", has_text=heading)
                link = facts.locator("a[href*='/contractors/']")
                check(link.count() == 1, f"{label}: the sheet links to the contractor")
                check(link.inner_text() == f"Pipes {serial}", f"{label}: the contractor's name")
                page_width = page.evaluate("document.documentElement.scrollWidth")
                check(page_width <= width, f"{label}: the sheet does not scroll ({page_width} px)")
                link.click()
                page.wait_for_selector("h1")
                check(f"Pipes {serial}" in page.inner_text("h1"), f"{label}: the sheet opens")
                context.close()


HOME_ARCHIVE_WIDTHS = (375, 1280)
# Per language: the link to archive, the title of its confirmation, its button, the mark, the link
# to bring the home back, the button of that confirmation.
HOME_ARCHIVE_TEXTS = {
    "en-GB": (
        "Archive this home",
        "Archive this home?",
        "Archive",
        "Archived",
        "Bring this home back",
        "Bring back",
    ),
    "fr-FR": (
        "Archiver ce logement",
        "Archiver ce logement ?",
        "Archiver",
        "Archivé",
        "Remettre ce logement en service",
        "Remettre en service",
    ),
}


def home_archive(browser: Browser, base: str) -> None:
    """Archive a home and bring it back, from the home's page. Local only (docs/adr/0079).

    At 375 and 1280 px, in English and in French, with and without script: the confirmation
    pages and the page of the home fit the width, the buttons are touch targets, the home is
    marked **Archived** and listed apart, then marked no more once brought back.
    Prints the measures.
    """
    for width in HOME_ARCHIVE_WIDTHS:
        for locale, (
            archive,
            title,
            button,
            mark,
            bring,
            back_button,
        ) in HOME_ARCHIVE_TEXTS.items():
            for script in (True, False):
                label = f"{width} px, {locale}{'' if script else ', no script'}"
                context = browser.new_context(
                    viewport={"width": width, "height": 900},
                    locale=locale,
                    java_script_enabled=script,
                )
                page = context.new_page()
                sign_in(page, base)
                page.goto(f"{base}/h/")
                page.locator("main a.row[href*='/h/']").first.click()
                page.wait_for_selector("h1")
                check(mark not in page.inner_text("h1"), f"{label}: not marked at the start")
                page.click(f"main a:has-text('{archive}')")
                page.wait_for_selector("h1")
                check(
                    page.inner_text("h1") == title,
                    f"{label}: the confirmation: {page.inner_text('h1')}",
                )
                submit = page.locator(f"main form button[type=submit]:has-text('{button}')")
                _fits(page, submit, width, f"{label}: the Archive button")
                check(
                    (submit.bounding_box() or {"height": 0})["height"] >= 44,
                    f"{label}: the button is a touch target",
                )
                submit.click()
                page.wait_for_selector("h1 .pill")
                check(mark in page.inner_text("h1"), f"{label}: the home is marked {mark}")
                _fits(page, page.locator("h1"), width, f"{label}: the title of the home")
                scroll = page.evaluate("document.documentElement.scrollWidth")
                check(scroll <= width, f"{label}: the page does not scroll ({scroll} px)")
                page.goto(f"{base}/h/")
                check(
                    page.locator("main h2", has_text=mark).count() == 1,
                    f"{label}: listed apart, under {mark}",
                )
                scroll = page.evaluate("document.documentElement.scrollWidth")
                check(scroll <= width, f"{label}: the list does not scroll ({scroll} px)")
                page.locator("main a.row[href*='/h/']").first.click()
                page.click(f"main a:has-text('{bring}')")
                page.wait_for_selector("h1")
                submit = page.locator(f"main form button[type=submit]:has-text('{back_button}')")
                _fits(page, submit, width, f"{label}: the Bring back button")
                submit.click()
                page.wait_for_selector("h1")
                check(mark not in page.inner_text("h1"), f"{label}: brought back, no mark")
                context.close()


ABOUT_WIDTHS = (375, 1280)
# The label of the first link and the mark of a store without an address, per language.
ABOUT_TEXTS = {"en": ("Website", "Coming soon"), "fr": ("Site web", "Bientôt")}


def _note_foreign(found: list[str], host: str) -> Any:
    def note(request: Any) -> None:
        if urlsplit(request.url).netloc not in {host, ""} and not request.url.startswith("data:"):
            found.append(request.url)

    return note


def about_links(browser: Browser, base: str) -> None:
    """The links of the About screen, measured. Local only.

    At 375 and 1280 px, in English and in French, with and without script: six links with a
    safe `rel`, two stores that are not links, tiles of 44 px at least inside the width, and no
    request to another host. Prints the measures.
    """
    host = urlsplit(base).netloc
    for width in ABOUT_WIDTHS:
        for locale, (first, soon) in ABOUT_TEXTS.items():
            for script in (True, False):
                label = f"{width} px, {locale}{'' if script else ', no script'}"
                context = browser.new_context(
                    viewport={"width": width, "height": 900},
                    locale=locale,
                    java_script_enabled=script,
                )
                page = context.new_page()
                foreign: list[str] = []
                page.on("request", _note_foreign(foreign, host))
                page.goto(f"{base}/about/")
                page.wait_for_selector(".about-links")
                links = page.locator("a.about-link")
                check(links.count() == 6, f"{label}: six links ({links.count()})")
                check(first in links.first.inner_text(), f"{label}: first link {first}")
                for index in range(links.count()):
                    rel = set((links.nth(index).get_attribute("rel") or "").split())
                    check(rel == {"noopener", "noreferrer"}, f"{label}: rel of link {index}")
                stores = page.locator("span.about-link-soon")
                check(stores.count() == 2, f"{label}: two stores without a link")
                check(page.locator("a.about-link-soon").count() == 0, f"{label}: no store link")
                for index in range(stores.count()):
                    check(soon in stores.nth(index).inner_text(), f"{label}: store marked {soon}")
                tiles = page.locator(".about-links .about-link")
                for index in range(tiles.count()):
                    box = tiles.nth(index).bounding_box()
                    check(box is not None, f"{label}: tile {index} shown")
                    assert box is not None
                    say(
                        f"{label}: tile {index} {box['x']:.0f}..{box['x'] + box['width']:.0f} px,"
                        f" {box['width']:.0f} x {box['height']:.0f}"
                    )
                    check(
                        box["height"] >= 44 and box["width"] >= 44,
                        f"{label}: tile {index} is 44 px",
                    )
                    check(
                        box["x"] >= 0 and box["x"] + box["width"] <= width,
                        f"{label}: tile {index} inside the width",
                    )
                scroll = page.evaluate("document.documentElement.scrollWidth")
                check(scroll <= width, f"{label}: the page does not scroll ({scroll} px)")
                check(foreign == [], f"{label}: nothing asked of another host: {foreign}")
                context.close()


BACKGROUND_WIDTHS = (320, 360, 375, 1280)
# Words of the screens, per language: the link being read, a link that could not be read, the
# test being sent, the test that failed (docs/adr/0070).
BACKGROUND_TEXTS = {
    "en": ("Reading the page", "could not be read", "is being sent", "could not be sent"),
    "fr": ("Lecture de la page", "n'a pas pu être lue", "en cours d'envoi", "pas pu être envoyé"),
}


def _fits(page: Page, element: Locator, width: int, label: str) -> None:
    """The element is on screen, inside the width, with a readable text and no sideways scroll."""
    box = element.bounding_box()
    check(box is not None, f"{label}: shown")
    assert box is not None
    size = element.evaluate("(e) => parseFloat(getComputedStyle(e).fontSize)")
    scroll = page.evaluate("document.documentElement.scrollWidth")
    say(f"{label}: box {box['x']:.0f}..{box['x'] + box['width']:.0f} px, text {size:.0f} px")
    check(box["x"] >= 0 and box["x"] + box["width"] <= width, f"{label}: inside the width")
    check(size >= 13, f"{label}: readable ({size} px)")
    check(scroll <= width, f"{label}: no horizontal scroll ({scroll} px)")


def background_calls(browser: Browser, base: str) -> None:
    """Reading a link and testing a channel, in the background. Local only (docs/adr/0070).

    Needs an instance with its worker (`qcluster`), the preview of links on, notifications on,
    and the account `demo@example.com` with a project (the first of the list) and a channel
    named `Phone` that fails (an address that does not answer). At 320, 360, 375 and 1280 px,
    in English and in French, with and without script: a link being read and one that could not
    be read, then the test of the channel, fit the width; with script the screen moves on
    by itself (no reload) and never stays "in progress"; without script a reload shows the end.
    Prints the measures.
    """
    for width in BACKGROUND_WIDTHS:
        for locale in ("en", "fr"):
            reading, unread, sending, failed = BACKGROUND_TEXTS[locale]
            for script in (True, False):
                label = f"{width} px, {locale}{'' if script else ', no script'}"
                context = browser.new_context(
                    viewport={"width": width, "height": 900},
                    locale=locale,
                    java_script_enabled=script,
                )
                page = context.new_page()
                sign_in(page, base)
                page.goto(f"{base}/projects/")
                page.goto(f"{page.url}?view=ideas")
                page.locator("main a.row-body[href*='/projects/']").first.click()
                page.wait_for_selector("h1")
                project = page.url
                # A link being read, and one that could not be, seeded by the setup of the check.
                running = page.locator("li.row", has_text=reading).first
                check(running.count() == 1, f"{label}: a link is being read")
                _fits(page, running, width, f"{label}, being read")
                broken = page.locator("li.row", has_text=unread).first
                check(broken.count() == 1, f"{label}: a link could not be read")
                _fits(page, broken, width, f"{label}, not read")
                # The test of the channel.
                page.goto(f"{base}/me/notifications/")
                page.locator("main a.row", has_text="Phone").first.click()
                page.wait_for_selector("#channel-test", state="attached")
                page.locator("form[action$='/test/'] button[type=submit]").click()
                page.wait_for_selector("#channel-test p")
                text = page.inner_text("#channel-test")
                check(sending in text or failed in text, f"{label}: in progress or done: {text}")
                _fits(page, page.locator("#channel-test"), width, f"{label}, test")
                if script:
                    page.wait_for_function(
                        "(t) => document.querySelector('#channel-test').innerText.includes(t)",
                        arg=failed,
                        timeout=40000,
                    )
                    check(
                        page.locator("#channel-test[hx-get]").count() == 0,
                        f"{label}: it stops asking once done",
                    )
                else:
                    page.wait_for_timeout(12000)
                    page.reload()
                    check(failed in page.inner_text("#channel-test"), f"{label}: reload shows it")
                _fits(page, page.locator("#channel-test"), width, f"{label}, test done")
                page.goto(project)
                context.close()


ADMIN_EMAIL = "admin@example.com"
TAB_WIDTHS = (320, 360, 375, 768, 1280)

# The text of every tab of the bar, where its box is, and the words of its label that are drawn
# on more than one line. A range around the content gives the width the text really takes,
# whether or not the box that holds it is as wide; a range around one word has one rectangle per
# line it is drawn on.
TABS_JS = """() => [...document.querySelectorAll('.tabs .tab')].map((tab) => {
  const range = document.createRange();
  range.selectNodeContents(tab);
  const text = range.getBoundingClientRect();
  const box = tab.getBoundingClientRect();
  const cut = [];
  const walker = document.createTreeWalker(tab, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    for (const word of node.data.matchAll(/\\S+/g)) {
      const part = document.createRange();
      part.setStart(node, word.index);
      part.setEnd(node, word.index + word[0].length);
      const lines = new Set([...part.getClientRects()].map((r) => Math.round(r.top / 4)));
      if (lines.size > 1) cut.push(word[0]);
    }
  }
  return {
    label: tab.textContent.trim(),
    box: [box.left, box.right],
    text: [text.left, text.right],
    cut,
  };
})"""


def instance_tabs(browser: Browser, base: str) -> None:
    """The tabs of the instance administration share the width without running into each other."""
    for locale in ("en", "fr"):
        for width in TAB_WIDTHS:
            context = browser.new_context(viewport={"width": width, "height": 800}, locale=locale)
            page = context.new_page()
            page.goto(f"{base}/accounts/login/")
            page.fill("input[name=login]", ADMIN_EMAIL)
            page.fill("input[name=password]", PASSWORD)
            page.click("main form:has(input[name=login]) button[type=submit]")
            # Signed in once the sign-in page is left: the load state of the sign-in page
            # itself is already reached, and a next page opened too early cancels the post.
            page.wait_for_url(lambda url: "/accounts/login/" not in url)
            # The household bar of the administrator's own home is the longest in French.
            page.goto(f"{base}/tasks/")
            home = page.url.split("/h/")[1].split("/")[0]
            for path, count in (
                ("/instance/", 4),
                ("/instance/settings/", 4),
                ("/instance/providers/", 4),
                (f"/h/{home}/household/", 3),
            ):
                page.goto(f"{base}{path}")
                tabs = page.evaluate(TABS_JS)
                label = f"{locale} {width} px {path}"
                check(
                    len(tabs) == count, f"{label}: {count} tabs ({len(tabs)} found at {page.url})"
                )
                for tab in tabs:
                    inside = (
                        tab["box"][0] - 1 <= tab["text"][0] and tab["text"][1] <= tab["box"][1] + 1
                    )
                    check(inside, f"{label}: '{tab['label']}' stays in its own box {tab['box']}")
                    # Hyphenation needs a dictionary the headless browser may not have: a word
                    # drawn on two lines is a label to shorten, hyphen or not.
                    check(
                        not tab["cut"], f"{label}: '{tab['label']}' has no word cut: {tab['cut']}"
                    )
                for left, right in pairwise(tabs):
                    check(
                        left["text"][1] <= right["text"][0],
                        f"{label}: '{left['label']}' does not run into '{right['label']}'",
                    )
                check(
                    page.evaluate("document.documentElement.scrollWidth") <= width,
                    f"{label}: the page does not scroll sideways",
                )
            context.close()


DEMO_WIDTHS = (320, 375, 1280)
DEMO_BOX_JS = """() => {
  const box = document.querySelector('.demo-accounts');
  if (!box) return null;
  const rect = (e) => {
    const r = e.getBoundingClientRect();
    return [r.left, r.top, r.right, r.bottom];
  };
  return {
    box: rect(box),
    parts: [...box.children].map((e) => rect(e)),
    codes: [...box.querySelectorAll('code')].map((e) => ({
      text: e.textContent,
      lines: e.getClientRects().length,
      rect: rect(e),
      nowrap: getComputedStyle(e).whiteSpace,
    })),
  };
}"""


def demo_accounts(browser: Browser, base: str) -> None:
    """The public accounts of a demonstration instance: stacked, and never cut."""
    for locale in ("en", "fr"):
        for width in DEMO_WIDTHS:
            context = browser.new_context(viewport={"width": width, "height": 800}, locale=locale)
            page = context.new_page()
            page.goto(f"{base}/accounts/login/")
            label = f"{locale} {width} px"
            found = page.evaluate(DEMO_BOX_JS)
            check(found is not None, f"{label}: the box of the demonstration accounts is there")
            left, _top, right, _bottom = found["box"]
            say(f"{label}: box {left:.0f}..{right:.0f} px")
            check(left >= 0 and right <= width, f"{label}: the box is inside the width")
            check(len(found["parts"]) == 3, f"{label}: a sentence, the accounts, the password")
            tops = [part[1] for part in found["parts"]]
            check(
                tops == sorted(tops) and len(set(tops)) == 3,
                f"{label}: the three parts are stacked",
            )
            check(len(found["codes"]) == 3, f"{label}: two addresses and the password")
            for code in found["codes"]:
                inside = left <= code["rect"][0] and code["rect"][2] <= right
                check(code["lines"] == 1, f"{label}: '{code['text']}' is drawn on one line")
                check(code["nowrap"] == "nowrap", f"{label}: '{code['text']}' cannot be cut")
                check(inside, f"{label}: '{code['text']}' stays in the box {code['rect']}")
            check(
                found["codes"][-1]["text"] == "housecairn-demo", f"{label}: the password is whole"
            )
            check(
                page.evaluate("document.documentElement.scrollWidth") <= width,
                f"{label}: the page does not scroll sideways",
            )
            context.close()


# The task form of the CI instance: an empty title and an amount that is not one, the amount
# sitting in the folded section "Time and budget".
SUMMARY = "[data-form-errors]"


def _on_summary(page: Page, label: str, count: int = 2) -> None:
    check(page.locator(SUMMARY).count() == 1, f"{label}: one summary")
    check(
        page.evaluate("document.activeElement.hasAttribute('data-form-errors')"),
        f"{label}: the summary holds the focus",
    )
    top = page.locator(SUMMARY).bounding_box()
    check(
        top is not None and 0 <= top["y"] < (page.viewport_size or {"height": 0})["height"],
        f"{label}: the summary is on screen",
    )
    check(page.locator(f"{SUMMARY} li").count() == count, f"{label}: {count} lines in the summary")


def _refuse_task_form(page: Page, base: str, script: bool) -> None:
    page.goto(f"{base}/tasks/")
    home = page.url.split("/h/")[1].split("/")[0]
    page.goto(f"{base}/h/{home}/tasks/new/")
    if script:
        page.click("summary:has-text('Time and budget')")
    else:
        check(
            not page.locator("details:has-text('Time and budget')").evaluate("d => d.open"),
            "no script: the section is folded to begin with",
        )
        page.locator("details:has-text('Time and budget')").evaluate("d => { d.open = true; }")
    page.fill("#id_labour_budget", "not an amount")
    page.evaluate("document.querySelector('form[data-task-form]').noValidate = true")


def form_errors(browser: Browser, base: str) -> None:
    """A refused form puts its summary on screen and in focus; the folded section opens."""
    for width in (360, 1280):
        # With script, a full page: the summary, the unfolded section, a click on a line.
        context = browser.new_context(viewport={"width": width, "height": 700})
        page = context.new_page()
        sign_in(page, base, FORMS_EMAIL)
        _refuse_task_form(page, base, script=True)
        page.click("form[data-task-form] > button.button-block[type=submit]")
        page.wait_for_selector(SUMMARY)
        label = f"{width} px, script"
        _on_summary(page, label)
        check(
            page.locator("#id_labour_budget").is_visible(),
            f"{label}: the folded section holding a field in error is open",
        )
        check(
            page.get_attribute("#id_labour_budget", "aria-invalid") == "true",
            f"{label}: the field is aria-invalid",
        )
        page.locator(f"{SUMMARY} a", has_text="Labour budget").click()
        check(
            page.evaluate("document.activeElement.id") == "id_labour_budget",
            f"{label}: a line of the summary puts the focus in its field",
        )
        context.close()

        # With script, HTMX swapping the page in place (a partial response): same behaviour.
        context = browser.new_context(viewport={"width": width, "height": 700})
        page = context.new_page()
        sign_in(page, base, FORMS_EMAIL)
        _refuse_task_form(page, base, script=True)
        page.evaluate(
            """() => {
              window.__sameDocument = true;
              const form = document.querySelector('form[data-task-form]');
              form.setAttribute('hx-post', location.pathname);
              form.setAttribute('hx-select', '#main');
              form.setAttribute('hx-target', '#main');
              form.setAttribute('hx-swap', 'outerHTML');
              htmx.process(form);
            }"""
        )
        page.click("form[data-task-form] > button.button-block[type=submit]")
        page.wait_for_selector(SUMMARY)
        label = f"{width} px, HTMX"
        check(page.evaluate("window.__sameDocument === true"), f"{label}: the page was swapped")
        _on_summary(page, label)
        check(
            page.locator("#id_labour_budget").is_visible(),
            f"{label}: the folded section holding a field in error is open",
        )
        context.close()

    # Without script: the browser alone puts the summary in focus (autofocus).
    context = browser.new_context(viewport={"width": 360, "height": 700}, java_script_enabled=False)
    page = context.new_page()
    context.add_cookies(_session_cookies(browser, base, FORMS_EMAIL))
    _refuse_task_form(page, base, script=False)
    page.click("form[data-task-form] > button.button-block[type=submit]")
    page.wait_for_selector(SUMMARY)
    _on_summary(page, "360 px, no script")
    check(
        page.locator("#id_labour_budget").is_visible(),
        "360 px, no script: the folded section holding a field in error is open",
    )
    context.close()


def _session_cookies(browser: Browser, base: str, email: str = EMAIL) -> list[Any]:
    """The cookies of a signed-in session: signing in needs no script, but is simplest with it."""
    context = browser.new_context()
    page = context.new_page()
    sign_in(page, base, email)
    cookies = context.cookies()
    context.close()
    return cookies


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "mode",
        choices=[
            "passkey",
            "sso",
            "in-place",
            "task-form",
            "task-lines",
            "item-contract",
            "home-archive",
            "about-links",
            "form-errors",
            "instance-tabs",
            "background-calls",
            "demo-accounts",
        ],
    )
    parser.add_argument("--base", required=True, help="address of the running instance")
    parser.add_argument("--provider", help="address of the stub identity provider (sso mode)")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        if args.mode == "sso":
            sso(browser, base, args.provider.rstrip("/"))
        elif args.mode == "in-place":
            in_place(browser, base)
        elif args.mode == "task-form":
            task_form(browser, base)
        elif args.mode == "task-lines":
            new_records_and_materials(browser, base)
        elif args.mode == "item-contract":
            item_contract(browser, base)
        elif args.mode == "home-archive":
            home_archive(browser, base)
        elif args.mode == "about-links":
            about_links(browser, base)
        elif args.mode == "form-errors":
            form_errors(browser, base)
        elif args.mode == "instance-tabs":
            instance_tabs(browser, base)
        elif args.mode == "background-calls":
            background_calls(browser, base)
        elif args.mode == "demo-accounts":
            demo_accounts(browser, base)
        else:
            passkey(browser, base)
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
