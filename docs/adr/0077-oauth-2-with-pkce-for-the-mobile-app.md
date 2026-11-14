# 0077. The mobile app signs in with OAuth 2 and PKCE: one connection per phone, revocable

- Status: proposed. OAuth 2 with PKCE in the browser, revocation per phone, homes chosen at
  sign-in with a right to create homes, HTTPS with plain HTTP only on a local address, and the
  phone acting as its account are the maintainer's decisions of 2026-11-14; the rest is what
  this record proposes.
- Date: 2026-11-14
- Amends: [ADR 0007](0007-authentication-and-api-tokens.md), section "OAuth 2 for the iOS app:
  not now", which this record replaces, and [ADR 0001](0001-record-architecture-decisions-and-dependencies.md),
  whose table of runtime dependencies gains django-oauth-toolkit and jwcrypto in the lot that
  adds them.
- Related: [ADR 0076](0076-sync-api-for-the-mobile-app.md) (the sync API the connection opens),
  [ADR 0078](0078-lots-of-the-server-mode-of-the-mobile-app.md) (lots).

## Context

The mobile app's third way, "My Housecairn server" (mobile ADR 0010,
[housecairn-mobile#11](https://github.com/HouseCairn/housecairn-mobile/pull/11)), needs a
credential that a person can give a phone without copying a secret by hand, that reaches only
the homes they choose, that lets the phone create a home, that lasts as long as the phone is in
use, and that they can take back from the web for one phone without touching the others.

API tokens ([ADR 0007](0007-authentication-and-api-tokens.md)) are made for tools: copied once
from the web, scopes `read`, `write` and `events`, at most a year, no renewal, no home creation.
ADR 0007 planned django-oauth-toolkit for "the iOS app", with the authorization code flow, PKCE
and a public client, mounted at `/o/`.

## Decision

### Library: django-oauth-toolkit

**django-oauth-toolkit** (3.4.x, BSD-2-Clause, Django 5.2 supported, release 3.4.1 of
2026-08-21), as ADR 0007 planned, mounted at `/o/`. It implements the authorization code
grant with PKCE on oauthlib, refresh token rotation with reuse detection, revocation
(RFC 7009), opaque tokens checked in the database (so a revocation takes effect at the next
request), tokens stored hashed (`COMPLIANT_BCP_RFC9700_TOKEN_STORAGE`), private-use URI schemes
for native apps (RFC 8252), and a DRF authentication class. It adds two packages to the lock:
itself and **jwcrypto** (LGPL-3.0-or-later, compatible with AGPL-3.0-only); oauthlib and
requests are already there. OpenID Connect is not enabled: the app needs an access token, not
an identity token.

Considered and not chosen:

- **django-allauth's own provider, `allauth.idp.oidc`**: already in the lock (65.21.1), and its
  `idp-oidc` extra needs only oauthlib and PyJWT, already locked, so it would add no package.
  Its documentation, read on 2026-11-14, says nothing of PKCE being required for public
  clients nor of redirect URIs with a private-use scheme, and its token models cannot carry the
  homes of a connection. It is the first thing to look at again if django-oauth-toolkit stops
  being maintained.
- **Authlib**: a general OAuth library whose Django server side needs the models, the views
  and the grant storage written here.
- **An implementation of our own**: no ("do not reinvent").

### The client of the app

- **One public client**, `client_id` `housecairn-mobile`, no secret, created by a data
  migration on every instance. Grants: `authorization_code` and `refresh_token` only.
  Dynamic client registration is off; the admin site shows the client read only, and no other
  client can be created from the interface in this lot.
- **PKCE required, `S256` only**: `PKCE_REQUIRED` is on and the validator refuses `plain`.
  `state` is required.
- **Redirect URI** `app.housecairn.mobile:/oauth/callback`: a private-use scheme in the
  reverse-domain form of RFC 8252 section 7.1 (`app.housecairn.mobile` is the app's identifier
  on both stores), matched exactly. The app opens the system browser session
  (`ASWebAuthenticationSession`, Android Custom Tabs), never a web view, so the person signs in
  on the server's own page with their passkey, second factor or identity provider.
- **Not a universal link.** A universal link needs a domain of the publisher with its
  association files, and when the app is not installed, the browser would open that domain with
  the code in the address: the code would reach a server the person never typed, against
  "the server the person typed, and nothing else" of mobile ADR 0010. Another app can claim the
  same private-use scheme on Android; PKCE makes a stolen code useless to it.

### What a connection is

A **connection** (`apitokens.AppConnection`) is one phone of one account:

| Field | Meaning |
| --- | --- |
| `user` | the account; the phone acts as it ("the phone represents the account") |
| `name` | the phone's name, sent by the app at sign-in, editable on the web |
| `homes` | the homes it opens, chosen at consent; a home the phone creates is added |
| `may_create_homes` | the right to create homes, given at consent |
| `device_id` | the phone's device id of the sync protocol, set by its first push, then fixed |
| `signed_in_provider` | as for API tokens ([ADR 0057](0057-api-tokens-of-a-home-that-requires-a-provider.md)) |
| `created_at`, `last_used_at`, `revoked_at` | as for API tokens; last use recorded at most once a minute |

The tokens of django-oauth-toolkit belong to exactly one connection: the consent creates the
connection and the grant points to it, the token exchange carries it to the access and refresh
tokens, and every rotation keeps it. Whether this goes through swapped models of
django-oauth-toolkit (possible since its tables do not exist yet) or through its validator
hooks is the OAuth lot's choice, with one test: revoking a connection revokes all its tokens in
the same transaction, and no token resolves to no connection.

### Scopes and what a connection opens

Two scopes, unrelated to those of API tokens:

- `sync`: the routes of `/api/v1/sync/` ([ADR 0076](0076-sync-api-for-the-mobile-app.md)) and
  nothing else. Every other route of the API refuses an access token of a connection with 403,
  token management included (`SessionOnly` refuses it as it refuses API tokens).
- `homes.create`: `POST /api/v1/sync/homes/`. The app asks for it only when the person chose to
  send the phone's homes to the server.

Within those, the rights are those of the account in each home: a read-only member's phone
reads. Home scoping ([ADR 0006](0006-home-scoping.md)) narrows by the homes of the connection,
as `homes_for` already does for API tokens. A member removed from a home loses it in their
connections too.

### Consent

The app opens `/o/authorize/` with `client_id`, `redirect_uri`, `state`, `code_challenge`,
`scope` and the phone's name. The person signs in by the usual page if needed, then sees a page
of Housecairn (`REQUEST_APPROVAL_PROMPT = "force"`, never skipped):

- the name of the app and the address of the server, as typed;
- "This phone will see and change everything in the homes you choose, as you can";
- the homes, with the same rules as the token form (a home that requires a sign-in provider can
  be ticked only after signing in through it); one home ticked when it is the only one;
- "May create homes", shown and ticked only when the app asked for `homes.create`;
- **Connect** and **Cancel**; Cancel returns `access_denied` to the app.

A new connection sends the account the security email of
[ADR 0023](0023-audit-log-export-erasure-and-email-alerts.md) ("A phone was connected to your
account"), and is recorded in the audit log.

### Lifetimes

- authorization code: 60 seconds, once;
- access token: 1 hour;
- refresh token: rotated at every use, with reuse detection
  (`REFRESH_TOKEN_REUSE_PROTECTION`): a refresh token used twice revokes the whole connection,
  since one of the two users is not the phone. No grace period, because django-oauth-toolkit
  forbids one with hashed storage: a phone that loses the answer of a refresh, the only
  common case, signs in again in the browser;
- a connection not used for 90 days expires (`REFRESH_TOKEN_EXPIRE_SECONDS`, idle-based, the
  default lifetime of API tokens, `HOUSECAIRN_API_TOKEN_DEFAULT_LIFETIME_DAYS`); a phone in use
  stays connected until it is revoked.

The tokens carry prefixes, `hca_` for an access token and `hcr_` for a refresh token, so that
each authentication class recognises its own: `ApiTokenAuthentication` keeps `hc_` and ignores
the others instead of answering 401, and the scanning of leaked secrets can tell them apart.

### Revocation per phone

The access screen of the account (`/me/tokens/`, today **API tokens**) gets a second list,
**Connected apps**: name, homes, may create homes, connected on, last use; **Edit** (name,
homes, the right to create) and **Revoke** with a confirmation page. A revoked connection
fails at its next request; its homes stay on the phone, which says it was disconnected
(mobile ADR 0010). The app's own "Disconnect" calls `/o/revoke_token/` and then forgets its
tokens. Deleting the account deletes its connections.

### HTTPS, and plain HTTP on a local address

`/o/authorize/`, `/o/token/` and `/api/v1/sync/` answer over HTTPS, or over plain HTTP only when
the address the request reached is local: an IP literal of loopback, RFC 1918, RFC 4193 or
link-local ranges, or a name under `.local` or `.home.arpa`. Any other plain HTTP request
gets 403 with a reason. This covers the Home Assistant add-on on its port on the household's
network. oauthlib refuses plain HTTP unless `OAUTHLIB_INSECURE_TRANSPORT` is set for the whole
process; the OAuth lot applies the rule above itself rather than set that variable for every
request. On plain HTTP, the consent page says "This connection is not encrypted: anyone on
this network can read what the phone sends". The app applies the same rule before it opens
anything (mobile ADR 0010).

### Living with API tokens

`OAuth2Authentication` of django-oauth-toolkit, subclassed to resolve the connection and to
activate the account's language as `ApiTokenAuthentication` does
([ADR 0074](0074-one-language-per-person-everywhere.md)), comes first in
`DEFAULT_AUTHENTICATION_CLASSES`, then `ApiTokenAuthentication`, then the session. API tokens,
their screen, their scopes and their routes do not change. Home Assistant keeps its `events`
token; nothing it uses requires OAuth.

## Threat model

| Threat | What limits it |
| --- | --- |
| Another app on the phone takes the code (same scheme on Android) | PKCE `S256`: the code is useless without the verifier, which never leaves the app; code valid 60 seconds, once; `state` checked by the app |
| A fake server: the person types the wrong address | The consent page and the app show the address typed; HTTPS with the system's checks. A server the person chose sees what they send: that is the third way, said on the app's screen (mobile ADR 0010) |
| The refresh token is stolen (backup, malware) | Kept in the Keychain or the Android Keystore by the app; rotation, and a reuse revokes the connection; 90 days idle; revocation from the web |
| The phone is lost | **Revoke** in Connected apps, effective at the next request; the homes on the phone stay readable by whoever unlocks it, as in the first way |
| An access token is read on the way | HTTPS; plain HTTP only on a local address, with a warning; 1 hour of life |
| A connection does more than the sync | Scope `sync` opens `/api/v1/sync/` only; the chosen homes only; the account's role; no token management, no home deletion, no account change |
| The database leaks | Tokens stored hashed (`COMPLIANT_BCP_RFC9700_TOKEN_STORAGE`); the client has no secret to leak |
| A page tricks the person into consenting | Consent is a POST with Django's CSRF check, no framing by another site (`X-Frame-Options` and CSP `frame-ancestors`, `DENY` unless the instance allows its own origin), the server's address and the homes shown; a security email for every new connection |
| Guessing at `/o/token/` | Rate limits of the instance ([ADR 0042](0042-rate-limits-quotas-language-and-instance-name.md)) on the token endpoint, as on the sign-in |

## Consequences

- The section "OAuth 2 for the iOS app: not now" of ADR 0007 is replaced: the scopes are `sync`
  and `homes.create`, not `read`, `write` and `events`, and an OAuth token opens only the sync
  API.
- Two packages and four tables of django-oauth-toolkit, plus the connections, join the
  application. Its `cleartokens` command runs in the nightly background tasks
  ([ADR 0008](0008-background-tasks.md)).
- A phone that loses the answer of a refresh signs in again: the price of hashed tokens and
  reuse detection. The app says why ("Sign in again to keep syncing").
- An instance behind a reverse proxy must pass the original scheme and host
  ([reverse proxy](../administration/reverse-proxy.md)), or the HTTPS rule refuses the phone.
