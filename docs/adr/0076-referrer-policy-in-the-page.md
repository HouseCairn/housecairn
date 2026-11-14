# 0076. The referrer policy is also in the page

- Status: accepted
- Date: 2026-11-14
- Completes: [0012](0012-configuration-and-security-defaults.md) (its `same-origin` referrer
  policy)

## Context

HouseCairn sends `Referrer-Policy: same-origin` with every response (`SECURE_REFERRER_POLICY`,
ADR 0012). A reverse proxy can replace that header, and Home Assistant does, for every response
of its HTTP server, the ingress ones of the Home Assistant app included: its `setup_headers`
middleware sets `Referrer-Policy: no-referrer` with `response.headers.update`, which replaces
the value of the application (`homeassistant/components/http/headers.py`, lines 18 to 47, Home
Assistant 2026.11.1).

Under `no-referrer`, a browser posts every form with `Origin: null`: the Fetch standard sets
the serialized origin of a request that is not `GET` or `HEAD` to `null` for that policy
("append a request `Origin` header"). Django's CSRF check then refuses the form, "Origin
checking failed - null does not match any trusted origins.", and the person sees "Session
expired". Through the Home Assistant app, the first-account screen fails this way for every
user (issue #11 of `HouseCairn/home-assistant-addons`), and so would every other form.

Reproduced with Chromium 156 behind a test chain that copies the code of Home Assistant Core
2026.11.1 (headers middleware and ingress proxy) and of the Supervisor 2026.11.0 (ingress
proxy), in front of HouseCairn 0.3.1: the page of the form arrives with a single
`Referrer-Policy: no-referrer`, and its form is posted with `Origin: null` and no `Referer`,
over HTTP and over HTTPS.

## Decision

Every page states the policy itself, in its `<head>`:
`<meta name="referrer" content="same-origin">`, in `base.html` and in the administration
(`admin/base_site.html`, which extends Django's). The HTML standard lets this element set the
referrer policy of its document, whatever the header said; the forms and HTMX requests of the
page are then sent with the page's real origin. The CSRF check is unchanged: the origin must
still be HouseCairn's own (or one of `CSRF_TRUSTED_ORIGINS`), and the cookie and the token are
still required.

The value is the one of `SECURE_REFERRER_POLICY`, and a test checks that they stay the same.
Without a proxy that changes the header, nothing changes: the page and the header agree.

## Consequences

- Through Home Assistant, the browser sends `Referer` again for requests to the same origin,
  as HouseCairn meant; nothing to another origin, as before.
- A page that does not extend `base.html` or the administration's template (the 500 page, the
  offline page, the API reference) has no form and keeps the header's policy.
- The same test chain, with this change: the first-account form is accepted (302), over HTTP
  and HTTPS, with `Origin: http://...` or `https://...` of the page. A form posted from a page
  of another origin under `no-referrer` is still refused (`Origin: null`).

## Alternatives considered

- **Accepting `Origin: null` when `Sec-Fetch-Site` is `same-origin`**, in the app's web server
  or in Django, with the token still required. Rejected: browsers send `Sec-Fetch-*` only to a
  potentially trustworthy URL (Fetch Metadata, "append the Fetch metadata headers for a
  request", step 1), so over plain HTTP, the usual way to reach Home Assistant on a home
  network, there is nothing to tell a legitimate form from a forged one, and the forms still
  fail. Over HTTPS it works, but it loosens the check to fix what the page can fix itself.
- **Asking Home Assistant to keep the application's header** through ingress: not in
  HouseCairn's hands, and the forms fail until then.
- **Removing `Origin: null` before Django's check**: over HTTP, Django then checks the token
  alone, for a forged form as for a real one.
