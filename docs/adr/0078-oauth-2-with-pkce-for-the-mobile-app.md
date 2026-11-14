# 0078. The mobile app signs in with OAuth 2 and PKCE: one connection per phone, revocable

- Status: proposed. OAuth 2 with PKCE in the browser, revocation per phone, homes chosen at
  sign-in with a right to create homes, HTTPS with plain HTTP only on a local address, and the
  phone acting as its account are the maintainer's decisions of 2026-11-14, and so are the
  answers to the questions of
  [ADR 0079](0079-lots-of-the-server-mode-of-the-mobile-app.md#decisions-of-the-maintainer-2026-11-14):
  the client is on by default with an instance setting to turn it off, and refresh tokens are
  hashed and rotated, a reuse revokes the connection, with no grace period. The rest is what
  this record proposes.
- Date: 2026-11-14
- Amends: [ADR 0007](0007-authentication-and-api-tokens.md), section "OAuth 2 for the iOS app:
  not now", which this record replaces, and [ADR 0001](0001-record-architecture-decisions-and-dependencies.md),
  whose table of runtime dependencies gains django-oauth-toolkit and jwcrypto in the lot that
  adds them.
- Related: [ADR 0077](0077-sync-api-for-the-mobile-app.md) (the sync API the connection opens),
  [ADR 0079](0079-lots-of-the-server-mode-of-the-mobile-app.md) (lots).

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

Its settings (`OAUTH2_PROVIDER`), named as in its release 3.4.1, each one for a rule of this
record:

| Setting | Value | Rule |
| --- | --- | --- |
| `PKCE_REQUIRED` | `True` | PKCE on every authorization |
| `COMPLIANT_BCP_RFC9700_PKCE_METHOD` | `True` | `plain` refused, `S256` only |
| `COMPLIANT_BCP_RFC9700_PKCE_REQUIRED` | `True` | `check --deploy` fails if PKCE is ever turned off |
| `COMPLIANT_BCP_RFC9700_TOKEN_STORAGE` | `True` | only the hash of a token is stored |
| `ROTATE_REFRESH_TOKEN` | `True` | a new refresh token at every use |
| `REFRESH_TOKEN_REUSE_PROTECTION` | `True` | a refresh token used twice revokes its family |
| `COMPLIANT_BCP_RFC9700_REFRESH_TOKEN` | `True` | `check --deploy` fails without reuse protection |
| `REFRESH_TOKEN_GRACE_PERIOD_SECONDS` | `0` | no grace period (required by hashed storage) |
| `ALLOWED_REDIRECT_URI_SCHEMES` | `["app.housecairn.mobile"]` | the app's private-use scheme only; neither `http` nor `https` |
| `COMPLIANT_BCP_RFC9700_REDIRECT_URI_SCHEME` | `True` | `check --deploy` fails if `http` comes back |
| `COMPLIANT_BCP_RFC9700_REDIRECT_URI_MATCHING` | `True` | no wildcard in a redirect URI |
| `COMPLIANT_BCP_RFC9700_IMPLICIT_GRANT`, `..._PASSWORD_GRANT` | `True` | those grants refused |
| `COMPLIANT_BCP_RFC9700_ACCESS_TOKEN_TRANSPORT` | `True` | an access token in the query string is refused |
| `COMPLIANT_BCP_RFC9700_AUTHZ_RESPONSE_ISS` | `True` | `iss` in the authorization response (RFC 9207) |
| `AUTHORIZATION_CODE_EXPIRE_SECONDS` | `60` | [Lifetimes](#lifetimes) |
| `ACCESS_TOKEN_EXPIRE_SECONDS` | `3600` | [Lifetimes](#lifetimes) |
| `REFRESH_TOKEN_EXPIRE_SECONDS` | 90 days | [Lifetimes](#lifetimes) |
| `REQUEST_APPROVAL_PROMPT` | `"force"` | the consent page is never skipped |
| `ACCESS_TOKEN_GENERATOR`, `REFRESH_TOKEN_GENERATOR` | functions of `apitokens` | the prefixes `hca_` and `hcr_` |
| `OAUTH2_BACKEND_CLASS` | a subclass of `OAuthLibCore` | [the transport rule](#https-and-plain-http-between-local-addresses) |
| `OIDC_ENABLED` | `False` | no identity token |

A test of the OAuth lot reads these values from the settings, and runs `check --deploy` with
no error from django-oauth-toolkit.

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
- **PKCE required, `S256` only**: `PKCE_REQUIRED` and `COMPLIANT_BCP_RFC9700_PKCE_METHOD` (the
  table above). **`state`**: django-oauth-toolkit has no setting to require it; the app always
  sends a random `state` and checks it on return, and the server gives it back unchanged. PKCE
  already defeats, for this client, the forged authorization response that `state` protects
  against (RFC 9700 section 2.1).
- **Redirect URI** `app.housecairn.mobile:/oauth/callback`: a private-use scheme in the
  reverse-domain form of RFC 8252 section 7.1 (`app.housecairn.mobile` is the app's identifier
  on both stores), matched exactly. The app opens the system browser session
  (`ASWebAuthenticationSession`, Android Custom Tabs), never a web view, so the person signs in
  on the server's own page with their passkey, second factor or identity provider. The scheme
  is the only one in `ALLOWED_REDIRECT_URI_SCHEMES`, and the URI has the single-slash form RFC
  8252 asks for.
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
| `name` | the phone's name, sent by the app at sign-in, editable on the web; **untrusted**: whoever starts the flow chooses it, so it is cut to 60 characters after control and format characters are removed, escaped as every value of a template, and shown only as a label ([Consent](#consent)) |
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

- `sync`: the routes of `/api/v1/sync/` ([ADR 0077](0077-sync-api-for-the-mobile-app.md)) and
  nothing else. Every other route of the API refuses an access token of a connection with 403,
  token management included, by the rule of the next section.
- `homes.create`: `POST /api/v1/sync/homes/`. The app asks for it only when the person chose to
  send the phone's homes to the server.

Within those, the rights are those of the account in each home: a read-only member's phone
reads. Home scoping ([ADR 0006](0006-home-scoping.md)) narrows by the homes of the connection,
as `homes_for` already does for API tokens. A member removed from a home loses it in their
connections too.

### A connection opens the sync API only, closed by default

The API of today tells a person from a tool by one test, `isinstance(request.auth, ApiToken)`:
anything that is not an API token is treated as a person signed in. An access token of a
connection is not an `ApiToken`, so without the rule below it would pass every one of those
tests with the full rights of the account (create or restore a home, mint an API token of
scope `write`, change roles). The rule is therefore built so that forgetting a route refuses
the connection, never lets it through.

**What `request.auth` is.** Three credentials reach a view of the API, and only three:

| Credential | `request.user` | `request.auth` |
| --- | --- | --- |
| a session (a person signed in) | the account | `None` |
| an API token | its account | an `apitokens.ApiToken` |
| an access token of a connection | its account | the access token model of django-oauth-toolkit (an `AbstractAccessToken`), whose `connection` is the `apitokens.AppConnection` |

A helper of `apitokens`, `credential_of(request)`, gives `"session"`, `"api_token"` or
`"connection"` from these types, and raises for anything else, so that a fourth kind added
later fails loudly instead of being read as a person.

**Three locks, any one of which refuses the connection outside the sync API:**

1. **The authentication class** (`apitokens.authentication.ConnectionAuthentication`, a subclass
   of django-oauth-toolkit's `OAuth2Authentication`, first in `DEFAULT_AUTHENTICATION_CLASSES`)
   reads the view from `request.parser_context["view"]` and raises 403 `connection_outside_sync`
   unless the view declares `sync_api = True`. It holds whatever `permission_classes` a view
   declares, since authentication runs before them.
2. **A global permission**, `apitokens.permissions.CredentialFitsView`, added to
   `DEFAULT_PERMISSION_CLASSES` and to `SERVE_PERMISSIONS` of the schema: it refuses a
   connection on a view without `sync_api`, and an API token or a session on a view with
   `sync_api` (403 `sync_needs_connection`; `info` without any credential is the one open
   route). A view that sets its own `permission_classes` replaces the default list, so the
   class is added to each of those on `main` at `650336b`, and a test (below) fails on a view
   whose permissions lack it: `accounts/api.py:116`; `apitokens/api.py:96`, `:139`;
   `calendar/api.py:154`, `:176`, `:191`; `homes/api.py:631`, and the `get_permissions` of
   `accounts/api.py:35`, `homes/api.py:480`, `:560`; `instance/api.py:58`, `:117`, `:127`,
   `:150`, `:306`, `:335`, `:423`, `:453`, `:502`, `:524`; `notifications/api.py:37`
   (`PERMISSIONS`, used at `:100`, `:168`, `:199`, `:258`).
3. **The tests of a person become "is a session"**, never "is not an API token". Every site of
   `main` at `650336b` (`git grep "isinstance(.*ApiToken)" src/`, twenty sites) is rewritten on
   `credential_of`:

   | Site | Today | After M2 |
   | --- | --- | --- |
   | `accounts/api.py:24` `SignedInPerson` | not an API token | a session |
   | `apitokens/permissions.py:34` `TokenScopePermission` | not an API token: allowed | a session: allowed; an API token: its scopes; a connection: its scope `sync` |
   | `apitokens/permissions.py:45` `SessionOnly` | not an API token | a session |
   | `calendar/api.py:33` `SessionOnly` | not an API token | a session |
   | `calendar/api.py:106` entries `can_write` | an API token without `write`: read only | neither a session nor an API token with `write`: read only |
   | `events/api.py:35` | the token, or `None` | the API token, or `None` for a session; a connection never gets here (lock 1) |
   | `homes/api.py:55` `NoTokenForHousehold` | not an API token | a session |
   | `homes/api.py:169` create a home | an API token: refused | not a session: refused |
   | `homes/api.py:189` sign-in requirement | an API token: refused | not a session: refused |
   | `homes/api.py:213` leave, `:227` audit, `:248` export, `:269` export job | an API token: refused | not a session: refused |
   | `homes/api.py:380` restore | an API token: refused | not a session: refused |
   | `homes/api.py:411` delete, `:425` unlock | an API token: refused | not a session: refused |
   | `homes/scoping.py:36` `homes_for_request` | an API token: its homes; else every home of the account | a session: every home; an API token: its homes; a connection: its homes |
   | `homes/sso.py:48` `signed_in_providers` | an API token: its provider | an API token or a connection: its `signed_in_provider`; a session: the session's |
   | `instance/api.py:36` `InstanceAdminOnly` | an API token: refused | not a session: refused |
   | `notifications/api.py:33` `WriteTokenOnly` | not an API token, or one with `write` | a session, or an API token with `write` |

   The OAuth lot greps again on its own base and adds any new site to this table.

**The test of the boundary** (`tests/test_credential_boundary.py`, in the OAuth lot):

- it lists every route of the API from the URL resolver (`/api/v1/` and `/api/schema/`), with
  every method its view allows, and fails if it finds none or if a route under
  `/api/v1/sync/` lacks `sync_api`;
- with an access token of a connection that has both scopes and is bound to the test home, every
  route outside `/api/v1/sync/` and every method answers 403 with the code
  `connection_outside_sync` (the code proves the refusal came before the view, so a 404 or a
  400 does not count as a pass);
- with an API token of each scope (`read`, `write`, `events`) and with a session, every route
  of `/api/v1/sync/` answers 403 `sync_needs_connection`, `info` without a credential answers
  200;
- it checks that every view of the API carries `CredentialFitsView` in `get_permissions()`;
- it checks `credential_of` on the three credentials and that it raises on any other value of
  `request.auth`.

The pages of the site (Django views, not the API) never read a bearer token, so a connection
opens none of them.

### Consent

The app opens `/o/authorize/` with `client_id`, `redirect_uri`, `state`, `code_challenge`,
`scope` and the phone's name. The person signs in by the usual page if needed, then sees a page
of Housecairn (`REQUEST_APPROVAL_PROMPT = "force"`, never skipped):

- the name of the app and the address of the server, as typed;
- "This phone will see and change everything in the homes you choose, as you can";
- the homes, with the same rules as the token form (a home that requires a sign-in provider can
  be ticked only after signing in through it); one home ticked when it is the only one;
- "May create homes", shown and ticked only when the app asked for `homes.create`;
- the name the phone gave, as a label under a heading that says it was given by the app
  ("Name given by the app"), never as the app's identity: the identity is "Housecairn mobile
  app", the only client. Another app can start the same flow with the same `client_id` (a
  private-use scheme is not owned, section above); what the person sees is this server's own
  page and the homes it grants, and PKCE keeps the code from any app but the one that started;
- **Connect** and **Cancel**; Cancel returns `access_denied` to the app.

**On by default** (the maintainer's decision): the client exists on every instance and does
nothing until a person consents. An instance setting
([ADR 0035](0035-instance-settings-in-the-interface.md)), **Mobile app**, on by default, turns
it off: `/o/authorize/` then shows that the instance does not allow the mobile app, `info`
answers `"oauth": null`, and every access token is refused with 403 `mobile_app_disabled`. The
connections stay listed and are not revoked, so turning the setting on again brings them back.

A new connection sends the account the security email of
[ADR 0023](0023-audit-log-export-erasure-and-email-alerts.md) ("A phone was connected to your
account"), and is recorded in the audit log.

### Lifetimes

- authorization code: 60 seconds, once;
- access token: 1 hour;
- refresh token (the maintainer's decision): stored hashed
  (`COMPLIANT_BCP_RFC9700_TOKEN_STORAGE`), rotated at every use (`ROTATE_REFRESH_TOKEN`), with
  reuse detection (`REFRESH_TOKEN_REUSE_PROTECTION`): a refresh token used twice revokes the
  whole connection, since one of the two users is not the phone. No grace period
  (`REFRESH_TOKEN_GRACE_PERIOD_SECONDS = 0`), which hashed storage requires: a phone that loses
  the answer of a refresh, the only common case, signs in again in the browser;
- a connection not used for 90 days expires (`REFRESH_TOKEN_EXPIRE_SECONDS`, idle-based, the
  default lifetime of API tokens, `HOUSECAIRN_API_TOKEN_DEFAULT_LIFETIME_DAYS`); a phone in use
  stays connected until it is revoked.

The tokens carry prefixes, `hca_` for an access token and `hcr_` for a refresh token (made by
`ACCESS_TOKEN_GENERATOR` and `REFRESH_TOKEN_GENERATOR`), so that each authentication class
recognises its own and the scanning of leaked secrets can tell them apart.
`ConnectionAuthentication` comes first and answers every bearer token that starts with `hca_`:
the connection, or 401 when the token is unknown, expired or revoked. It never returns
"not mine" for an `hca_` token (django-oauth-toolkit's own class returns `None` for an invalid
token, which would let the next class decide), so an expired `hca_` token is a 401, never a
fall back to the session cookie of the same browser. Any other bearer token goes on to
`ApiTokenAuthentication`, which **does not change**: `hc_` tokens as today, 401 for anything it
does not know, as today. Tests: an expired, a revoked and an unknown `hca_` token each answer
401, with and without a valid session cookie; an `hc_` token and an unknown bearer answer as on
`main`.

### Revocation per phone

The access screen of the account (`/me/tokens/`, today **API tokens**) gets a second list,
**Connected apps**: name, homes, may create homes, connected on, last use; **Edit** (name,
homes, the right to create) and **Revoke** with a confirmation page. A revoked connection
fails at its next request; its homes stay on the phone, which says it was disconnected
(mobile ADR 0010). The app's own "Disconnect" calls `/o/revoke_token/` and then forgets its
tokens. Deleting the account deletes its connections.

### HTTPS, and plain HTTP between local addresses

`/o/authorize/`, `/o/token/`, `/o/revoke_token/` and every route of `/api/v1/sync/` answer over
HTTPS. Plain HTTP is accepted only when **both** of these hold, and the rule is written once
here; the app applies the same rule to what it can see (mobile ADR 0010):

1. **The peer is local.** The address is `REMOTE_ADDR` as `core.proxy.ForwardedHeadersMiddleware`
   leaves it: the client address a trusted proxy reports in `X-Forwarded-For` when the peer is
   in `HOUSECAIRN_FORWARDED_ALLOW_IPS`, and the peer itself otherwise, since that middleware
   removes every forwarding header of any other peer
   ([reverse proxy](../administration/reverse-proxy.md)). It is local when
   `core.netguard.is_public` is false for it, the rule the instance already uses for what is on
   the open web ([ADR 0059](0059-notifications-refuse-private-networks.md)); there is no second
   list. So loopback, private networks (RFC 1918), unique local IPv6 (RFC 4193), link-local,
   the shared address space 100.64.0.0/10 (a VPN of the household such as Tailscale, or a
   carrier's NAT) and the documentation ranges are local; an IPv6 address that carries an IPv4
   one is judged by the IPv4 address (`netguard.embedded_ipv4`).
2. **The address the phone typed is a local IP literal.** The `Host` header, as Django reads it
   (`get_host()`, after `ALLOWED_HOSTS`; `USE_X_FORWARDED_HOST` stays off), must be an IP
   address, with or without a port, for which `is_public` is false. **A name is refused on
   plain HTTP, `.local` and `.home.arpa` included**: a name says nothing of where the request
   went, and a `.local` answer is multicast DNS, which anyone on the network can forge, so the
   phone would send its tokens to whoever answered first. A household that wants a name uses
   HTTPS.

The `Host` header alone proves nothing, since the client writes it; the peer is what counts,
and the `Host` rule keeps the app and the server on the same rule. A client on the Internet that
sends `Host: 192.0.2.10` to an instance whose port is open still has a public peer address and
is refused. `X-Forwarded-Proto: https` counts only from a trusted proxy (settings
`SECURE_PROXY_SSL_HEADER` with `HOUSECAIRN_BEHIND_TLS_PROXY`, and the middleware above that
removes the header of any other peer), so a client cannot claim HTTPS; a request that
`request.is_secure()` calls secure needs neither condition.

Any other plain HTTP request gets 403 `insecure_transport` with a reason. This covers the Home
Assistant add-on on its port on the household's network, reached by its IP address.
oauthlib refuses plain HTTP unless `OAUTHLIB_INSECURE_TRANSPORT` is set for the whole process;
that variable is never set: the subclass of `OAuthLibCore` named by `OAUTH2_BACKEND_CLASS`
applies the rule above, then gives oauthlib the request's address with the `https` scheme, so
oauthlib's own check passes only for a request the rule accepted. On plain HTTP, the consent
page says "This connection is not encrypted: anyone on this network can read what the phone
sends". Tests: each condition alone fails; a public peer with a private `Host` fails; a
`.local` host fails; `X-Forwarded-Proto` from an untrusted peer is ignored; a peer in
100.64.0.0/10 with an IP literal passes.

### Living with API tokens

`ConnectionAuthentication`, the subclass of django-oauth-toolkit's `OAuth2Authentication` that
resolves the connection, holds the boundary of the sync API and activates the account's
language as `ApiTokenAuthentication` does
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
| A connection does more than the sync | Three locks ([A connection opens the sync API only](#a-connection-opens-the-sync-api-only-closed-by-default)) and a test over every route; the chosen homes only; the account's role; no token management, no home deletion, no account change |
| Another app starts the flow under the same client and a misleading phone name | The name is a label marked as given by the app, cut and escaped; the page is the server's own, with its address and the homes; PKCE keeps the code with the app that started; a security email for every new connection |
| A client off the household's network gets plain HTTP by sending a private `Host` | The peer address decides, as resolved from trusted proxies only; the `Host` must be a local IP literal too; names are refused on plain HTTP |
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
