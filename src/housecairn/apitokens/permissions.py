"""Scope checks for requests authenticated by a token, and the boundary of the sync API.

- `read`: safe methods (GET, HEAD, OPTIONS) on regular endpoints;
- `write`: other methods on regular endpoints;
- `events`: only endpoints that declare `token_scope = Scope.EVENTS`, and on
  those, nothing else is accepted.

Session-authenticated requests are not concerned: their rights come from the
user's role in the home, checked by the services. An access token of a connected app has
the scope `sync` (docs/adr/0078).
"""

from typing import Any

from django.utils.translation import gettext_lazy as _
from rest_framework import permissions
from rest_framework.request import Request

from housecairn.apitokens.credentials import Credential, api_token_of, credential_of
from housecairn.apitokens.models import Scope


def required_scope(request: Request, view: Any) -> Scope:
    if getattr(view, "token_scope", None) == Scope.EVENTS:
        return Scope.EVENTS
    if request.method in permissions.SAFE_METHODS:
        return Scope.READ
    return Scope.WRITE


class TokenScopePermission(permissions.BasePermission):
    message = _("This token does not have the scope this request needs.")

    def has_permission(self, request: Request, view: Any) -> bool:
        credential = credential_of(request)
        if credential is Credential.SESSION:
            return True
        if credential is Credential.API_TOKEN:
            token = api_token_of(request)
            return token is not None and token.has_scope(required_scope(request, view))
        return "sync" in str(request.auth.scope).split()  # type: ignore[union-attr]


class SessionOnly(permissions.BasePermission):
    """Managing tokens is for a person signed in: a token never lists, mints or revokes one."""

    message = _("API tokens are managed by a person signed in, not by an API token.")

    def has_permission(self, request: Request, view: Any) -> bool:
        return credential_of(request) is Credential.SESSION


class CredentialFitsView(permissions.BasePermission):
    """A connection on the sync API only, and only a connection on it (docs/adr/0078).

    In `DEFAULT_PERMISSION_CLASSES`, in `SERVE_PERMISSIONS` of the schema, and in every view
    that sets its own `permission_classes`: a test fails on a view of the API without it.
    `ConnectionAuthentication` already refuses a connection outside the sync API; this is
    the second lock. The one open route is `info` without any credential
    (`sync_open = True`).
    """

    def has_permission(self, request: Request, view: Any) -> bool:
        from housecairn.apitokens.authentication import (
            CodedPermissionDenied,
            connection_outside_sync,
        )

        credential = credential_of(request)
        if getattr(view, "sync_api", False):
            if credential is Credential.CONNECTION:
                return True
            if (
                credential is Credential.SESSION
                and not request.user.is_authenticated
                and getattr(view, "sync_open", False)
            ):
                return True
            raise CodedPermissionDenied(
                _("The sync API takes an access token of a connected app only."),
                "sync_needs_connection",
            )
        if credential is Credential.CONNECTION:
            raise connection_outside_sync()
        return True
