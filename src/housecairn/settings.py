"""Django settings for HouseCairn.

Every setting comes from environment variables (see `.env.example` and
`docs/adr/0012-configuration-and-security-defaults.md`). The defaults are the
safe ones: `DEBUG` is off, cookies are secure, HTTPS is enforced, and nothing
is loaded from a third-party host.
"""

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import django_stubs_ext
import environ
from csp.constants import NONE, SELF
from django.core.exceptions import ImproperlyConfigured

from housecairn.core import hosthint
from housecairn.core import prefix as url_prefix
from housecairn.core.proxy import TrustedProxies
from housecairn.instance import registry
from housecairn.instance.ratelimits import RateLimits

# Allows generic annotations such as ListView[Home] at runtime.
django_stubs_ext.monkeypatch()

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = BASE_DIR.parents[1]

env = environ.Env()
# A local `.env` file is only a convenience for development: it fills in
# variables that are not already set, never overrides them.
if (REPO_DIR / ".env").is_file():
    env.read_env(str(REPO_DIR / ".env"), overwrite=False)

# Settings an administrator can also change in the interface (docs/adr/0035): the variable of
# each one is read here, once. A variable that is set locks its field; `get_setting` is the
# only way the rest of the code reads them.
_ENV = registry.read_environment()
HOUSECAIRN_ENV_LOCKED: frozenset[str] = _ENV.locked

DEBUG: bool = env.bool("DEBUG", default=False)

_DEV_SECRET_KEY = "dev-only-insecure-key-do-not-use-in-production"  # noqa: S105
SECRET_KEY: str = env.str("SECRET_KEY", default="")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("SECRET_KEY must be set when DEBUG is false.")
    SECRET_KEY = _DEV_SECRET_KEY
# Former keys, comma-separated, during a rotation (docs/administration/settings.md#secrets):
# sessions and signed links made with them stay valid, and so do the secrets encrypted in the
# database (instance.crypto), which a new key alone could no longer read.
# Spaces around each key are dropped: "<new>, <old>" would otherwise lose the former key.
SECRET_KEY_FALLBACKS: list[str] = [
    k.strip() for k in env.list("SECRET_KEY_FALLBACKS", default=[]) if k.strip()
]

ALLOWED_HOSTS: list[str] = env.list(
    "ALLOWED_HOSTS", default=["localhost", "127.0.0.1"] if DEBUG else []
)
CSRF_TRUSTED_ORIGINS: list[str] = env.list("CSRF_TRUSTED_ORIGINS", default=[])
# What the 400 page says when the host name is refused; set by whoever hosts the instance.
HOUSECAIRN_DISALLOWED_HOST_HINT: str = hosthint.parse_hint(
    env.str("HOUSECAIRN_DISALLOWED_HOST_HINT", default="")
)

# A public demonstration instance (docs/adr/0071): two public accounts, a reset every night,
# settings read only and nothing sent out. Off by default; off changes nothing.
HOUSECAIRN_DEMO: bool = registry.boolean_variable("HOUSECAIRN_DEMO", default=False)

DATA_DIR = Path(env.str("HOUSECAIRN_DATA_DIR", default=str(REPO_DIR / "data")))

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # allauth's security-key pages use its date filters.
    "django.contrib.humanize",
    "allauth",
    "allauth.account",
    "allauth.mfa",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.openid_connect",
    "rest_framework",
    "drf_spectacular",
    "drf_spectacular_sidecar",
    "django_q",
    "csp",
    # OAuth 2 with PKCE for the mobile app (docs/adr/0078).
    "oauth2_provider",
    "housecairn.core",
    "housecairn.accounts",
    "housecairn.homes",
    "housecairn.sync",
    "housecairn.apitokens",
    "housecairn.events",
    "housecairn.audit",
    "housecairn.instance",
    "housecairn.rooms",
    "housecairn.tasks",
    "housecairn.stock",
    "housecairn.projects",
    "housecairn.budget",
    "housecairn.notifications",
    "housecairn.calendar",
    "housecairn.labels",
    "housecairn.reports",
    "housecairn.demo",
]

MIDDLEWARE = [
    # First: forwarding headers count only from a trusted reverse proxy, before
    # the HTTPS redirect or anything else reads them (docs/adr/0012).
    "housecairn.core.proxy.ForwardedHeadersMiddleware",
    # Then the path prefix, before anything reads the path or sets a cookie (docs/adr/0055).
    "housecairn.core.prefix.UrlPrefixMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # Switches off the browser features the application never uses.
    "housecairn.core.middleware.PermissionsPolicyMiddleware",
    # Serves the collected static files, right after the security headers are
    # set (docs/adr/0015).
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "csp.middleware.CSPMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "housecairn.instance.middleware.InstanceLocaleMiddleware",
    "housecairn.instance.middleware.InstanceTimeZoneMiddleware",
    "django.middleware.common.CommonMiddleware",
    # Before the CSRF check, which reads a form first: a post too large for an upload
    # screen gets that screen and its reason, not a bare 400 page.
    "housecairn.core.middleware.RequestTooLargeMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Sees every response after the views and the middleware below: what the service worker
    # may keep for reading offline, and when it must forget it (docs/adr/0065).
    "housecairn.core.offline.OfflinePagesMiddleware",
    "housecairn.accounts.middleware.DeviceCookieMiddleware",
    "housecairn.accounts.middleware.MailDeliveryMiddleware",
    # After authentication: a person's saved language wins over the browser's (docs/adr/0042).
    "housecairn.tasks.middleware.PersonLanguageMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    # After CSRF, the session and the messages: `no-store` and a one-time token on the
    # creation forms, so that the browser's back button creates no duplicate (NAV-12).
    "housecairn.core.middleware.CreationFormMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    # A refusal of the demonstration mode becomes a message on the same screen (docs/adr/0071).
    "housecairn.demo.middleware.DemoRefusalMiddleware",
    # Last: reads the database, so after the checks that need no query (docs/adr/0035).
    "housecairn.instance.middleware.SettingsSnapshotMiddleware",
]

# Path prefix of the application, such as /housecairn, empty at the root (docs/adr/0055). It is
# FORCE_SCRIPT_NAME: every address Django builds starts with it, emails of the worker included.
HOUSECAIRN_URL_PREFIX: str = url_prefix.parse_prefix(env.str("HOUSECAIRN_URL_PREFIX", default=""))
FORCE_SCRIPT_NAME: str | None = HOUSECAIRN_URL_PREFIX or None
# A header that gives the prefix of each request, such as X-Ingress-Path: off unless named,
# and read only from HOUSECAIRN_FORWARDED_ALLOW_IPS (core.proxy removes it from anyone else).
HOUSECAIRN_URL_PREFIX_HEADER: str = url_prefix.parse_header_name(
    env.str("HOUSECAIRN_URL_PREFIX_HEADER", default="")
)

ROOT_URLCONF = "housecairn.urls"
WSGI_APPLICATION = "housecairn.wsgi.application"
ASGI_APPLICATION = "housecairn.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "housecairn.accounts.context_processors.signup",
                "housecairn.core.context_processors.shell",
                "housecairn.core.help.help_link",
                "housecairn.demo.context_processors.demo",
            ],
            # `{% instance_name %}` in every template, emails included (docs/adr/0042).
            "builtins": ["housecairn.core.templatetags.instance"],
        },
    },
]

# Database: SQLite in the data directory unless DATABASE_URL says otherwise
# (for example postgres://user:password@db.example.com:5432/housecairn).
DATABASES = {
    "default": env.db_url(
        "DATABASE_URL", default=f"sqlite:///{(DATA_DIR / 'db.sqlite3').as_posix()}"
    ),
}
if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    DATABASES["default"].setdefault("OPTIONS", {}).update(
        {
            # WAL lets the web process and the task worker read and write at once;
            # IMMEDIATE avoids "database is locked" errors on concurrent writes.
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
            "transaction_mode": "IMMEDIATE",
        }
    )

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

# Internationalization: English is the source language, French ships with V1.
LANGUAGE_CODE = "en"
# Each language is named in itself, whatever the language of the page: never translated.
LANGUAGES = [("en", "English"), ("fr", "Français")]
# One catalog per app, `<app>/locale/` (docs/adr/0013): two pull requests that add
# strings to two apps never touch the same file. Listed here, and not only found
# through INSTALLED_APPS, so that they keep priority over the catalogs of allauth
# and Django; the shared strings (shell, common templates) are in `core`, first.
LOCALE_PATHS = sorted(
    BASE_DIR.glob("*/locale"), key=lambda path: (path.parent.name != "core", path)
)
USE_I18N = True
# The time zone of the households (docs/adr/0032, 0042): the variable, else UTC. The
# interface can set another one, activated for each request (core.middleware).
TIME_ZONE: str = _ENV.values["time_zone"]
USE_TZ = True
# Language for a browser that asks for none the instance offers, and the name of the
# instance in the app bar, the titles and the emails (docs/adr/0042).
HOUSECAIRN_DEFAULT_LANGUAGE: str = _ENV.values["default_language"]
HOUSECAIRN_INSTANCE_NAME: str = _ENV.values["instance_name"]
# Files of a home (docs/adr/0030, 0042): the largest file, and the quotas of each home.
HOUSECAIRN_PHOTO_MAX_BYTES: int = _ENV.values["file_max_bytes"]
HOUSECAIRN_HOME_STORAGE_MB: int = _ENV.values["home_storage_mb"]
HOUSECAIRN_HOME_MAX_FILES: int = _ENV.values["home_max_files"]

STATIC_URL = url_prefix.STATIC_PATH
STATIC_ROOT = Path(env.str("STATIC_ROOT", default=str(DATA_DIR / "static")))
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_ROOT = Path(env.str("MEDIA_ROOT", default=str(DATA_DIR / "media")))
MEDIA_URL = "media/"
# Static files are collected once, at image build time, with a content hash in
# their names and a compressed copy, then served by WhiteNoise with long-lived
# cache headers (docs/adr/0015). With DEBUG on, the hashed names are not used.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # WhiteNoise's storage, with addresses that follow the path prefix (core.storage).
    "staticfiles": {"BACKEND": "housecairn.core.storage.ManifestStorage"},
}

# Public source repository, shown on the About screen.
HOUSECAIRN_SOURCE_URL = "https://github.com/HouseCairn/housecairn"

LOGIN_URL = "account_login"
# After sign-in: Today, at the root address (an explicit `next` still wins).
LOGIN_REDIRECT_URL = "index"

# Security headers and cookies (docs/adr/0012). Safe values unless DEBUG is on.
SECURE_SSL_REDIRECT: bool = env.bool("SECURE_SSL_REDIRECT", default=not DEBUG)
# Django matches it against the path, prefix included, without its leading slash.
SECURE_REDIRECT_EXEMPT = [
    "^" + (re.escape(HOUSECAIRN_URL_PREFIX[1:] + "/") if HOUSECAIRN_URL_PREFIX else "") + "healthz$"
]
HOUSECAIRN_BEHIND_TLS_PROXY: bool = env.bool("HOUSECAIRN_BEHIND_TLS_PROXY", default=False)
if HOUSECAIRN_BEHIND_TLS_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# Reverse proxies whose forwarding headers count: X-Forwarded-Proto above and
# X-Forwarded-For for the client address. From any other peer, these headers
# are removed by housecairn.core.proxy.ForwardedHeadersMiddleware. The loopback
# by default; set it to the proxy's address or network.
HOUSECAIRN_FORWARDED_ALLOW_IPS: list[str] = env.list(
    "HOUSECAIRN_FORWARDED_ALLOW_IPS", default=["127.0.0.1", "::1"]
)
TrustedProxies.parse(HOUSECAIRN_FORWARDED_ALLOW_IPS)  # fails at start-up if invalid
# The client address is resolved once, by that middleware, into REMOTE_ADDR:
# django-allauth's rate limits must not read X-Forwarded-For themselves.
ALLAUTH_TRUSTED_PROXY_COUNT = 0
ALLAUTH_TRUSTED_CLIENT_IP_HEADER = None
# The Host header, not X-Forwarded-Host, names the site: the proxy must pass
# Host through (Caddy and Traefik do by default; nginx: proxy_set_header Host).
USE_X_FORWARDED_HOST = False
USE_X_FORWARDED_PORT = False
SECURE_HSTS_SECONDS: int = env.int("SECURE_HSTS_SECONDS", default=0 if DEBUG else 31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS: bool = env.bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)
SECURE_HSTS_PRELOAD: bool = env.bool("SECURE_HSTS_PRELOAD", default=False)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
SESSION_COOKIE_SECURE: bool = env.bool("SESSION_COOKIE_SECURE", default=not DEBUG)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE: bool = env.bool("CSRF_COOKIE_SECURE", default=not DEBUG)
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
# Under a prefix, the cookies of the application are sent to its addresses only. A prefix that
# comes from a header is applied to each response by core.prefix.UrlPrefixMiddleware.
SESSION_COOKIE_PATH = CSRF_COOKIE_PATH = LANGUAGE_COOKIE_PATH = HOUSECAIRN_URL_PREFIX or "/"
# Framing (docs/adr/0055): refused to every page by default. `true` lets a page of the same
# origin show HouseCairn in a frame, as Home Assistant's Ingress panel does; never another origin.
HOUSECAIRN_ALLOW_SAME_ORIGIN_FRAMING: bool = registry.boolean_variable(
    "HOUSECAIRN_ALLOW_SAME_ORIGIN_FRAMING", default=False
)
X_FRAME_OPTIONS = "SAMEORIGIN" if HOUSECAIRN_ALLOW_SAME_ORIGIN_FRAMING else "DENY"
# Including subdomains in HSTS and asking for preloading are commitments for the
# whole domain, which only its owner can make: they stay opt-in, so Django's
# reminders about them are silenced (docs/adr/0012).
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]

# Content Security Policy (docs/adr/0011): no inline script or style, nothing
# from another origin.
_form_action: list[str] = [SELF]

# Authentication: django-allauth (docs/adr/0007).
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
HOUSECAIRN_SIGNUP_OPEN: bool = _ENV.values["signup_open"]
# With sign-up open, an address is verified by email before the account can be used;
# otherwise the accounts are the administrator's and the invited people's.
# Follows the sign-up setting when the interface changes it (instance/config.py).
_VERIFICATION_PINNED: str = env.str("ACCOUNT_EMAIL_VERIFICATION", default="")
HOUSECAIRN_EMAIL_VERIFICATION_PINNED: bool = bool(_VERIFICATION_PINNED)
ACCOUNT_EMAIL_VERIFICATION = _VERIFICATION_PINNED or (
    "mandatory" if HOUSECAIRN_SIGNUP_OPEN else "optional"
)
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
ACCOUNT_ADAPTER = "housecairn.accounts.adapters.AccountAdapter"
# Messages that name the person show the display name, never the address (docs/adr/0045).
ACCOUNT_USER_DISPLAY = "housecairn.accounts.names.person_name"
ACCOUNT_FORMS = {
    "login": "housecairn.accounts.forms.LoginForm",
    "change_password": "housecairn.accounts.forms.ChangePasswordForm",
    "reauthenticate": "housecairn.accounts.forms.ReauthenticateForm",
    "signup": "housecairn.accounts.forms.SignupForm",
}
SOCIALACCOUNT_ADAPTER = "housecairn.accounts.adapters.SocialAccountAdapter"
SOCIALACCOUNT_ONLY = False
# An identity from a provider never signs in to, nor attaches itself to, an
# existing account because it claims the same address: the account's owner
# links it once signed in (docs/adr/0007, docs/adr/0021).
SOCIALACCOUNT_EMAIL_AUTHENTICATION = False
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = False
# Rate limits of django-allauth, plus those of invitations (docs/adr/0020):
# creating or sending again, per person; opening or accepting a link, per
# client address (REMOTE_ADDR, see HOUSECAIRN_FORWARDED_ALLOW_IPS). The numbers
# are instance settings (docs/adr/0042): the mapping reads them at each use.
for _key in registry.SETTINGS:
    if _key.startswith("ratelimit_"):
        globals()[registry.SETTINGS[_key].django_name] = _ENV.values[_key]
ACCOUNT_RATE_LIMITS = RateLimits()
# Days an invitation link stays valid.
HOUSECAIRN_INVITATION_LIFETIME_DAYS: int = _ENV.values["invitation_lifetime_days"]
# Public address of the instance (scheme and host, no path), used for links in
# emails. Empty: the address of the request, which ALLOWED_HOSTS has checked.
HOUSECAIRN_SITE_URL: str = _ENV.values["site_url"]
# Open sign-up (docs/adr/0024): the proof of work asked on the sign-up page, as a
# count of zero bits (8 to 24; 16 takes a second or so), and optional lists of
# domains, comma-separated, that may (only those) or may not sign up.
HOUSECAIRN_SIGNUP_POW_BITS: int = _ENV.values["signup_pow_bits"]
HOUSECAIRN_SIGNUP_ALLOWED_DOMAINS: list[str] = _ENV.values["signup_allowed_domains"]
HOUSECAIRN_SIGNUP_BLOCKED_DOMAINS: list[str] = _ENV.values["signup_blocked_domains"]
# Sign-up through OIDC: "closed" (default) refuses an identity without an
# account, except the very first one, which becomes the instance
# administrator; "open" lets anyone the provider accepts create an account.
HOUSECAIRN_OIDC_SIGNUP: str = _ENV.values["oidc_signup"]
# If set, only this address, verified by the provider, can become the first
# administrator through OIDC.
HOUSECAIRN_BOOTSTRAP_ADMIN_EMAIL: str = env.str(
    "HOUSECAIRN_BOOTSTRAP_ADMIN_EMAIL", default=""
).strip()

# The first-account screen (/setup/), opened by a one-time code written in the process
# log, while the instance has no administrator (docs/adr/0056). `false` for an instance
# whose administrator is created another way.
HOUSECAIRN_SETUP_SCREEN: bool = env.bool("HOUSECAIRN_SETUP_SCREEN", default=True)
if HOUSECAIRN_DEMO:
    # The accounts of a demonstration instance come from `reset_demo` alone.
    HOUSECAIRN_SETUP_SCREEN = False

# Outgoing email, off until EMAIL_HOST is set. Without a server the console
# backend only prints messages, so features that need a real mailbox, such as
# password reset by email, stay hidden (accounts.services.email_is_configured).
EMAIL_HOST: str = _ENV.values["email_host"]
EMAIL_PORT: int = _ENV.values["email_port"]
EMAIL_HOST_USER: str = _ENV.values["email_host_user"]
EMAIL_HOST_PASSWORD: str = _ENV.values["email_host_password"]
# STARTTLS on the submission port (587) by default; EMAIL_USE_SSL is TLS from the first byte
# (port 465). Django refuses both at once, so SSL wins.
EMAIL_USE_SSL: bool = _ENV.values["email_use_ssl"]
EMAIL_USE_TLS: bool = _ENV.values["email_use_tls"] and not EMAIL_USE_SSL
# Seconds to wait for the mail server: a server that does not answer must not hold a
# web process for minutes (the default of Python is no limit).
EMAIL_TIMEOUT: int = _ENV.values["email_timeout"]
# SMTP when a host is set, the console otherwise, decided for each message from the settings
# in force (docs/adr/0035), so that the interface can change the server without a restart.
EMAIL_BACKEND = "housecairn.instance.mail.EmailBackend"
DEFAULT_FROM_EMAIL: str = _ENV.values["default_from_email"]
# Security alerts (password or passkey changed, new address, new sign-in) go out
# only when mail can be delivered (docs/adr/0023); subjects start with the name.
ACCOUNT_EMAIL_NOTIFICATIONS = bool(EMAIL_HOST)
# Subjects start with the name of the instance (accounts.adapters, docs/adr/0042).

MFA_SUPPORTED_TYPES = ["totp", "recovery_codes", "webauthn"]
MFA_PASSKEY_LOGIN_ENABLED = True
MFA_TOTP_ISSUER = "HouseCairn"
MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN = DEBUG

# Generic OpenID Connect provider, enabled only when its variables are set. No
# provider is named in the code: any compliant identity provider works.
SOCIALACCOUNT_PROVIDERS: dict[str, Any] = {}
_oidc_server_url: str = env.str("OIDC_SERVER_URL", default="")
# No sign-in through a provider on a demonstration instance: it would leave the server.
if _oidc_server_url and not HOUSECAIRN_DEMO:
    SOCIALACCOUNT_PROVIDERS["openid_connect"] = {
        "OAUTH_PKCE_ENABLED": True,
        "APPS": [
            {
                "provider_id": env.str("OIDC_PROVIDER_ID", default="oidc"),
                "name": env.str("OIDC_PROVIDER_NAME", default="Single sign-on"),
                "client_id": env.str("OIDC_CLIENT_ID"),
                "secret": env.str("OIDC_CLIENT_SECRET", default=""),
                "settings": {"server_url": _oidc_server_url},
            }
        ],
    }
    # Browsers apply form-action to the redirect that follows the sign-in form.
    _parts = urlsplit(_oidc_server_url)
    _form_action.append(f"{_parts.scheme}://{_parts.netloc}")

CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": [NONE],
        "script-src": [SELF],
        "style-src": [SELF],
        "img-src": [SELF, "data:"],
        "font-src": [SELF],
        "connect-src": [SELF],
        "manifest-src": [SELF],
        # The service worker of the application, and the worker of the anti-robot check.
        "worker-src": [SELF],
        "form-action": _form_action,
        "frame-ancestors": [SELF if HOUSECAIRN_ALLOW_SAME_ORIGIN_FRAMING else NONE],
        "base-uri": [NONE],
        "object-src": [NONE],
    },
}

# REST API (docs/adr/0004).
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        # First: answers every `hca_` bearer token, the connection or 401 (docs/adr/0078).
        "housecairn.apitokens.authentication.ConnectionAuthentication",
        "housecairn.apitokens.authentication.ApiTokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        # First: a connection opens the sync API only, and only a connection opens it, with
        # its own code before any other check answers (docs/adr/0078).
        "housecairn.apitokens.permissions.CredentialFitsView",
        "rest_framework.permissions.IsAuthenticated",
        "housecairn.apitokens.permissions.TokenScopePermission",
    ],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_PAGINATION_CLASS": "housecairn.core.pagination.DefaultCursorPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
    "EXCEPTION_HANDLER": "housecairn.homes.scoping.api_exception_handler",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "HouseCairn API",
    "DESCRIPTION": "Version 1 of the HouseCairn API, shared by every client.",
    "VERSION": "1.0.0",
    "OAS_VERSION": "3.1.0",
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    "SERVE_INCLUDE_SCHEMA": False,
    # An `events` token posts events and reads nothing, the schema included.
    "SERVE_PERMISSIONS": [
        # First, as in DEFAULT_PERMISSION_CLASSES (docs/adr/0078).
        "housecairn.apitokens.permissions.CredentialFitsView",
        "rest_framework.permissions.IsAuthenticated",
        "housecairn.apitokens.permissions.TokenScopePermission",
    ],
    "COMPONENT_SPLIT_REQUEST": True,
    # The same three kinds appear in the label, in its creation and in the answer to a scan.
    # The statuses of an occurrence are those of the tasks and of the calendar entries; the name
    # is the one the schema already gave them.
    "ENUM_NAME_OVERRIDES": {
        "LabelKindEnum": "housecairn.labels.models.LabelKind",
        "OccurrenceStatusEnum": "housecairn.tasks.models.Status",
    },
    # Under a prefix, `servers` says where the API is (core.prefix, docs/adr/0055).
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.hooks.postprocess_schema_enums",
        "housecairn.core.prefix.schema_servers",
    ],
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "REDOC_DIST": "SIDECAR",
}

# Background tasks: django-q2 with the ORM broker (docs/adr/0008).
Q_CLUSTER = {
    "name": "housecairn",
    "orm": "default",
    "workers": env.int("HOUSECAIRN_TASK_WORKERS", default=2),
    "timeout": 300,
    "retry": 360,
    "max_attempts": 3,
    "save_limit": 250,
    "catch_up": False,
    "sync": env.bool("HOUSECAIRN_TASKS_SYNC", default=False),
}
# Export of a home (docs/adr/0048): above this many bytes of files, the archive is made by
# the worker and downloaded later, at most a few times and for a day.
HOUSECAIRN_EXPORT_INLINE_MAX_BYTES = 50 * 1024 * 1024
HOUSECAIRN_EXPORT_DOWNLOADS = 3
HOUSECAIRN_EXPORT_KEEP_HOURS = 24
# Notifications (docs/adr/0043): on or off, and how often the worker looks for reminders.
HOUSECAIRN_NOTIFICATIONS_ENABLED: bool = _ENV.values["notifications_enabled"]
HOUSECAIRN_NOTIFICATIONS_INTERVAL: int = _ENV.values["notifications_interval"]
HOUSECAIRN_NOTIFICATIONS_PRIVATE_NETWORKS: bool = _ENV.values["notifications_private_networks"]
# Notifications on the device, by Web Push (docs/adr/0066).
HOUSECAIRN_WEBPUSH_ENABLED: bool = _ENV.values["webpush_enabled"]
HOUSECAIRN_WEBPUSH_DISCREET: bool = _ENV.values["webpush_discreet"]
# Preview of the links of projects (docs/adr/0050).
HOUSECAIRN_LINK_PREVIEW_ENABLED: bool = _ENV.values["link_preview_enabled"]

# API tokens (docs/adr/0007).
HOUSECAIRN_API_TOKEN_DEFAULT_LIFETIME_DAYS: int = env.int(
    "HOUSECAIRN_API_TOKEN_DEFAULT_LIFETIME_DAYS", default=90
)
HOUSECAIRN_EVENT_PAYLOAD_MAX_BYTES = 16 * 1024
# The mobile app's client (docs/adr/0078): on unless the instance turns it off.
HOUSECAIRN_MOBILE_APP_ENABLED: bool = _ENV.values["mobile_app_enabled"]

# OAuth 2 with PKCE for the mobile app, by django-oauth-toolkit (docs/adr/0078): each value
# is a rule of that record, and a test reads them back.
OAUTH2_PROVIDER = {
    "PKCE_REQUIRED": True,
    "COMPLIANT_BCP_RFC9700_PKCE_METHOD": True,
    "COMPLIANT_BCP_RFC9700_PKCE_REQUIRED": True,
    "COMPLIANT_BCP_RFC9700_TOKEN_STORAGE": True,
    "ROTATE_REFRESH_TOKEN": True,
    "REFRESH_TOKEN_REUSE_PROTECTION": True,
    "COMPLIANT_BCP_RFC9700_REFRESH_TOKEN": True,
    "REFRESH_TOKEN_GRACE_PERIOD_SECONDS": 0,
    "ALLOWED_REDIRECT_URI_SCHEMES": ["app.housecairn.mobile"],
    "COMPLIANT_BCP_RFC9700_REDIRECT_URI_SCHEME": True,
    "COMPLIANT_BCP_RFC9700_REDIRECT_URI_MATCHING": True,
    "COMPLIANT_BCP_RFC9700_IMPLICIT_GRANT": True,
    "COMPLIANT_BCP_RFC9700_PASSWORD_GRANT": True,
    "COMPLIANT_BCP_RFC9700_ACCESS_TOKEN_TRANSPORT": True,
    "COMPLIANT_BCP_RFC9700_AUTHZ_RESPONSE_ISS": True,
    "AUTHORIZATION_CODE_EXPIRE_SECONDS": 60,
    "ACCESS_TOKEN_EXPIRE_SECONDS": 3600,
    # Idle-based: a refresh token dies this long after its access token expired.
    "REFRESH_TOKEN_EXPIRE_SECONDS": HOUSECAIRN_API_TOKEN_DEFAULT_LIFETIME_DAYS * 24 * 3600,
    "REQUEST_APPROVAL_PROMPT": "force",
    "ACCESS_TOKEN_GENERATOR": "housecairn.apitokens.oauth.access_token",
    "REFRESH_TOKEN_GENERATOR": "housecairn.apitokens.oauth.refresh_token",
    "OAUTH2_BACKEND_CLASS": "housecairn.apitokens.oauth.TransportRuleCore",
    "OAUTH2_VALIDATOR_CLASS": "housecairn.apitokens.oauth.ConnectionValidator",
    "OIDC_ENABLED": False,
    "SCOPES": {
        "sync": "The sync API of the homes of the connection",
        "homes.create": "Create homes",
    },
    "DEFAULT_SCOPES": ["sync"],
}
# django-oauth-toolkit's own models, not swapped: the connection of a token is kept by the
# link tables of `apitokens`, whose migration names these settings.
OAUTH2_PROVIDER_APPLICATION_MODEL = "oauth2_provider.Application"
OAUTH2_PROVIDER_ACCESS_TOKEN_MODEL = "oauth2_provider.AccessToken"  # noqa: S105 - a model name
OAUTH2_PROVIDER_REFRESH_TOKEN_MODEL = "oauth2_provider.RefreshToken"  # noqa: S105 - a model name
OAUTH2_PROVIDER_GRANT_MODEL = "oauth2_provider.Grant"
OAUTH2_PROVIDER_ID_TOKEN_MODEL = "oauth2_provider.IDToken"  # noqa: S105 - a model name

# Cache, which holds the counters of the rate limits (docs/adr/0025). The default is
# in memory and per process: with several web processes each counts alone, so a limit
# is up to HOUSECAIRN_WEB_WORKERS times looser. A shared cache makes it exact, for
# example HOUSECAIRN_CACHE_URL=dbcache://housecairn_cache (the table is created at
# start-up), or a Redis or Memcached URL.
CACHES = {"default": env.cache("HOUSECAIRN_CACHE_URL", default="locmemcache://housecairn")}

# Documentation site opened by the help link of each screen (docs/adr/0016).
# A link only: the interface loads nothing from this address.
HOUSECAIRN_DOCS_URL: str = env.str(
    "HOUSECAIRN_DOCS_URL", default="https://docs.housecairn.app"
).rstrip("/")
if urlsplit(HOUSECAIRN_DOCS_URL).scheme not in {"http", "https"}:
    raise ImproperlyConfigured("HOUSECAIRN_DOCS_URL must be an http:// or https:// address.")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    # The address of a calendar feed is a secret: django.request writes the path of a 4xx.
    "filters": {"feed_secrets": {"()": "housecairn.core.log_redaction.RedactFeedAddresses"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "filters": ["feed_secrets"]}},
    "root": {"handlers": ["console"], "level": env.str("LOG_LEVEL", default="INFO")},
}
