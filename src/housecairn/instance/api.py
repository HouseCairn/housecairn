"""API of the instance administrator, for a signed-in administrator only (docs/adr/0022).

An API token never serves these endpoints, even one owned by an administrator:
administration needs a person signed in.
"""

from collections.abc import Iterable
from typing import Any

from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import exceptions, mixins, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from housecairn.accounts.services import email_is_configured, is_instance_admin
from housecairn.apitokens.credentials import is_session
from housecairn.apitokens.permissions import CredentialFitsView
from housecairn.audit import services as audit
from housecairn.audit.api import AuditEventSerializer
from housecairn.audit.models import AuditEvent
from housecairn.homes import quotas, sso
from housecairn.homes.scoping import allow
from housecairn.instance import config, emails, providers, registry, services, settings_services
from housecairn.instance.models import InstanceInvitation, OidcProvider
from housecairn.instance.reauth import signed_in_recently


class InstanceAdminOnly(permissions.BasePermission):
    message = "Instance administration needs an administrator signed in; API tokens cannot."

    def has_permission(self, request: Request, view: Any) -> bool:
        if not is_session(request):
            return False
        return is_instance_admin(request.user)


class InstanceInvitationSerializer(serializers.ModelSerializer[InstanceInvitation]):
    class Meta:
        model = InstanceInvitation
        fields = ["id", "email", "sent_at", "expires_at"]
        read_only_fields = ["id", "sent_at", "expires_at"]


def _validation(exc: DjangoValidationError) -> serializers.ValidationError:
    return serializers.ValidationError({"detail": exc.messages})


class InstanceInvitationViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet[InstanceInvitation]
):
    """Pending invitations to the instance. They are sent by email, never returned."""

    serializer_class = InstanceInvitationSerializer
    permission_classes = [InstanceAdminOnly, CredentialFitsView]
    queryset = InstanceInvitation.objects.none()
    lookup_field = "id"
    lookup_url_kwarg = "invitation_id"
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self) -> Any:
        return services.pending_invitations()

    def _send(self) -> Any:
        if not email_is_configured():
            raise exceptions.ValidationError(
                {"detail": ["This instance cannot send email, so it cannot invite by link."]}
            )
        if not allow(self.request, "housecairn_instance_invite"):
            raise exceptions.Throttled()
        return emails.sender_for(self.request)

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        send = self._send()
        try:
            invitation = services.invite(
                request.user,  # type: ignore[arg-type]
                email=serializer.validated_data["email"],
                send=send,
            )
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(self.get_serializer(invitation).data, status=status.HTTP_201_CREATED)

    def destroy(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Revoke the invitation: its link stops working."""
        invitation = get_object_or_404(InstanceInvitation.objects.all(), id=kwargs["invitation_id"])
        services.revoke(request.user, invitation)  # type: ignore[arg-type]
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=None, responses={200: InstanceInvitationSerializer})
    @action(detail=True, methods=["post"])
    def resend(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """A new link and a new expiry; the previous link stops working."""
        invitation = get_object_or_404(InstanceInvitation.objects.all(), id=kwargs["invitation_id"])
        send = self._send()
        try:
            invitation = services.resend(
                request.user,  # type: ignore[arg-type]
                invitation,
                send=send,
            )
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(self.get_serializer(invitation).data)


class InstanceAuditView(mixins.ListModelMixin, viewsets.GenericViewSet[AuditEvent]):
    """Audit log of the instance: administration, invitations, accounts. No home content."""

    serializer_class = AuditEventSerializer
    permission_classes = [InstanceAdminOnly, CredentialFitsView]
    queryset = AuditEvent.objects.none()

    def get_queryset(self) -> Any:
        return audit.instance_log(self.request.user)


class UnlockHomesView(APIView):
    """Clear the sign-in rule of every home whose provider the instance no longer offers."""

    permission_classes = [InstanceAdminOnly, CredentialFitsView]

    @extend_schema(
        request=None,
        responses={200: inline_serializer("Unlocked", {"unlocked": serializers.IntegerField()})},
    )
    def post(self, request: Request) -> Response:
        try:
            count = services.unlock_removed_providers(
                request.user,  # type: ignore[arg-type]
                declared=[p.id for p in sso.declared_providers(request)],
            )
        except PermissionDenied as exc:  # pragma: no cover - the permission class refuses first
            raise exceptions.PermissionDenied() from exc
        return Response({"unlocked": count})


class InstanceUsageView(APIView):
    """Space and files each home takes, against the quotas (docs/adr/0042).

    A home is its identifier and its numbers only: no name, member or file.
    """

    permission_classes = [InstanceAdminOnly, CredentialFitsView]

    @extend_schema(
        responses={
            200: inline_serializer(
                "HomesUsage",
                {
                    "limits": inline_serializer(
                        "HomeQuotas",
                        {"bytes": serializers.IntegerField(), "files": serializers.IntegerField()},
                    ),
                    "homes": serializers.ListField(
                        child=inline_serializer(
                            "HomeUsage",
                            {
                                "id": serializers.UUIDField(),
                                "bytes": serializers.IntegerField(),
                                "files": serializers.IntegerField(),
                                "percent": serializers.IntegerField(),
                            },
                        )
                    ),
                },
            )
        },
    )
    def get(self, request: Request) -> Response:
        allowed = quotas.limits()
        return Response(
            {
                "limits": {"bytes": allowed.bytes, "files": allowed.files},
                "homes": [
                    {"id": row.id, "bytes": row.bytes, "files": row.files, "percent": row.percent}
                    for row in quotas.by_home()
                ],
            }
        )


def _setting_field(setting: registry.Setting) -> serializers.Field[Any, Any, Any, Any]:
    kind = setting.kind
    options: dict[str, Any] = {"required": False, "help_text": str(setting.help)}
    if kind is registry.Kind.BOOL:
        return serializers.BooleanField(**options)
    if kind is registry.Kind.INT:
        return serializers.IntegerField(
            min_value=setting.minimum,  # type: ignore[arg-type]
            max_value=setting.maximum,  # type: ignore[arg-type]
            **options,
        )
    if kind is registry.Kind.CHOICE:
        return serializers.ChoiceField(choices=list(setting.choices), **options)
    if kind is registry.Kind.LIST:
        return serializers.ListField(child=serializers.CharField(max_length=253), **options)
    if kind is registry.Kind.SECRET:
        return serializers.CharField(write_only=True, max_length=1024, **options)
    return serializers.CharField(allow_blank=True, max_length=1024, **options)


SettingsUpdateSerializer = type(
    "SettingsUpdate",
    (serializers.Serializer,),
    {s.key: _setting_field(s) for s in registry.SETTINGS.values()},
)

SettingSerializer = inline_serializer(
    "InstanceSetting",
    {
        "key": serializers.CharField(),
        "section": serializers.CharField(),
        "kind": serializers.ChoiceField(choices=[k.value for k in registry.Kind]),
        "label": serializers.CharField(),
        "value": serializers.JSONField(
            allow_null=True, help_text="Null for a secret, which is never returned."
        ),
        "default": serializers.JSONField(allow_null=True),
        "source": serializers.ChoiceField(
            choices=["environment", "database", "default", "demo"],
            help_text="`demo`: a demonstration instance imposes the value (docs/adr/0071).",
        ),
        "locked": serializers.BooleanField(
            help_text="True when an environment variable sets it: it cannot be changed here."
        ),
        "environment_variable": serializers.CharField(),
        "reload": serializers.ChoiceField(choices=[r.value for r in registry.Reload]),
        "secret_set": serializers.BooleanField(
            allow_null=True, help_text="For a secret only: whether one is in force."
        ),
        "minimum": serializers.IntegerField(allow_null=True),
        "maximum": serializers.IntegerField(allow_null=True),
        "choices": serializers.ListField(child=serializers.CharField()),
    },
)
SettingsListSerializer = inline_serializer(
    "InstanceSettings", {"settings": serializers.ListField(child=SettingSerializer)}
)


def _setting_json(key: str) -> dict[str, Any]:
    state = config.describe(key)
    setting = state.setting
    return {
        "key": key,
        "section": setting.section.value,
        "kind": setting.kind.value,
        "label": str(setting.label),
        "value": state.value,
        "default": None if setting.secret else setting.default,
        "source": state.source,
        "locked": state.locked,
        "environment_variable": setting.env,
        "reload": setting.reload.value,
        "secret_set": state.is_set if setting.secret else None,
        "minimum": setting.minimum,
        "maximum": setting.maximum,
        "choices": list(setting.choices),
    }


def _all_settings() -> dict[str, Any]:
    return {"settings": [_setting_json(key) for key in registry.SETTINGS]}


def _needs_recent_sign_in(request: Request) -> Response | None:
    if signed_in_recently(request._request):
        return None
    return Response(
        {
            "detail": "Sign in again to change settings.",
            "code": "reauthentication_required",
        },
        status=status.HTTP_403_FORBIDDEN,
    )


def _locked(keys: Iterable[str]) -> Response | None:
    locked = sorted(k for k in keys if k in registry.SETTINGS and config.is_locked(k))
    if not locked:
        return None
    return Response(
        {
            "detail": "A setting is set by an environment variable: change it there.",
            "code": "locked_by_environment",
            "locked": locked,
        },
        status=status.HTTP_409_CONFLICT,
    )


class InstanceSettingsView(APIView):
    """Settings of the instance, paired with environment variables (docs/adr/0035).

    Reading needs an administrator signed in; changing also needs a recent sign-in. A setting
    that a variable sets is `locked` and refuses writes. Secrets are write-only.
    """

    permission_classes = [InstanceAdminOnly, CredentialFitsView]

    @extend_schema(responses={200: SettingsListSerializer})
    def get(self, request: Request) -> Response:
        return Response(_all_settings())

    @extend_schema(request=SettingsUpdateSerializer, responses={200: SettingsListSerializer})
    def patch(self, request: Request) -> Response:
        if (refused := _needs_recent_sign_in(request)) is not None:
            return refused
        if not isinstance(request.data, dict):
            raise exceptions.ValidationError({"detail": ["Expected an object."]})
        if (conflict := _locked(request.data)) is not None:
            return conflict
        serializer = SettingsUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        unknown = sorted(set(request.data) - set(registry.SETTINGS))
        if unknown:
            raise exceptions.ValidationError({k: ["Unknown setting."] for k in unknown})
        try:
            settings_services.update(request.user, dict(serializer.validated_data))  # type: ignore[arg-type]
        except DjangoValidationError as exc:
            raise exceptions.ValidationError(exc.message_dict) from exc
        return Response(_all_settings())


class InstanceSettingResetView(APIView):
    """Restore the default of one setting: the stored value is removed."""

    permission_classes = [InstanceAdminOnly, CredentialFitsView]

    @extend_schema(request=None, responses={204: None})
    def delete(self, request: Request, key: str) -> Response:
        if key not in registry.SETTINGS:
            raise exceptions.NotFound()
        if (refused := _needs_recent_sign_in(request)) is not None:
            return refused
        if (conflict := _locked([key])) is not None:
            return conflict
        try:
            settings_services.reset(request.user, key)  # type: ignore[arg-type]
        except DjangoValidationError as exc:
            raise exceptions.ValidationError(exc.message_dict) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)


ProviderSerializer = inline_serializer(
    "SignInProvider",
    {
        "slug": serializers.CharField(),
        "name": serializers.CharField(),
        "issuer": serializers.CharField(),
        "client_id": serializers.CharField(),
        "pkce": serializers.BooleanField(),
        "enabled": serializers.BooleanField(),
        "secret_set": serializers.BooleanField(help_text="Whether a client secret is stored."),
        "secret_unreadable": serializers.BooleanField(),
        "locked": serializers.BooleanField(
            help_text="True when environment variables set it: it cannot be changed here."
        ),
        "linked_accounts": serializers.IntegerField(),
        "homes": serializers.IntegerField(help_text="How many homes require it."),
    },
)


class ProviderWriteSerializer(serializers.Serializer[Any]):
    """A provider to create or change; `secret` is write-only and never returned."""

    slug = serializers.CharField(max_length=32, required=False)
    name = serializers.CharField(max_length=64, required=False)
    issuer = serializers.CharField(max_length=255, required=False)
    client_id = serializers.CharField(max_length=255, required=False)
    secret = serializers.CharField(max_length=1024, required=False, write_only=True)
    pkce = serializers.BooleanField(required=False)
    enabled = serializers.BooleanField(required=False)


def _provider_json(state: providers.Declared) -> dict[str, Any]:
    return {
        "slug": state.slug,
        "name": state.name,
        "issuer": state.issuer,
        "client_id": state.client_id,
        "pkce": state.pkce,
        "enabled": state.enabled,
        "secret_set": state.secret_set,
        "secret_unreadable": state.secret_unreadable,
        "locked": state.locked,
        "linked_accounts": state.linked_accounts,
        "homes": state.homes,
    }


def _provider_changes(request: Request) -> dict[str, Any]:
    if not isinstance(request.data, dict):
        raise exceptions.ValidationError({"detail": ["Expected an object."]})
    unknown = sorted(set(request.data) - set(ProviderWriteSerializer().fields))
    if unknown:
        raise exceptions.ValidationError({k: ["Unknown field."] for k in unknown})
    serializer = ProviderWriteSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    return dict(serializer.validated_data)


_PROVIDER_LOCKED = {
    "detail": "This provider is set by environment variables: change it there.",
    "code": "locked_by_environment",
}


class InstanceProvidersView(APIView):
    """Sign-in providers (docs/adr/0041): those of the variables, read only, then the others.

    Creating one needs a recent sign-in. The client secret is write-only.
    """

    permission_classes = [InstanceAdminOnly, CredentialFitsView]

    @extend_schema(
        operation_id="instance_providers_list",
        responses={
            200: inline_serializer(
                "SignInProviders", {"providers": serializers.ListField(child=ProviderSerializer)}
            )
        },
    )
    def get(self, request: Request) -> Response:
        return Response({"providers": [_provider_json(p) for p in providers.describe_all()]})

    @extend_schema(request=ProviderWriteSerializer, responses={201: ProviderSerializer})
    def post(self, request: Request) -> Response:
        if (refused := _needs_recent_sign_in(request)) is not None:
            return refused
        data = _provider_changes(request)
        try:
            provider = providers.create(request.user, data)  # type: ignore[arg-type]
        except DjangoValidationError as exc:
            raise exceptions.ValidationError(exc.message_dict) from exc
        return Response(
            _provider_json(providers.describe(provider)), status=status.HTTP_201_CREATED
        )


class InstanceProviderView(APIView):
    """One provider: read, change (PATCH), delete. One set by variables answers 409 to writes."""

    permission_classes = [InstanceAdminOnly, CredentialFitsView]

    def _find(self, slug: str) -> OidcProvider | None:
        if slug in providers.environment_slugs():
            return None
        provider: OidcProvider | None = OidcProvider.objects.filter(slug=slug).first()
        if provider is None:
            raise exceptions.NotFound()
        return provider

    @extend_schema(responses={200: ProviderSerializer})
    def get(self, request: Request, slug: str) -> Response:
        provider = self._find(slug)
        if provider is None:
            state = next(p for p in providers.describe_all() if p.slug == slug)
            return Response(_provider_json(state))
        return Response(_provider_json(providers.describe(provider)))

    @extend_schema(request=ProviderWriteSerializer, responses={200: ProviderSerializer})
    def patch(self, request: Request, slug: str) -> Response:
        provider = self._find(slug)
        if provider is None:
            return Response(_PROVIDER_LOCKED, status=status.HTTP_409_CONFLICT)
        if (refused := _needs_recent_sign_in(request)) is not None:
            return refused
        try:
            providers.update(request.user, provider, _provider_changes(request))  # type: ignore[arg-type]
        except DjangoValidationError as exc:
            raise exceptions.ValidationError(exc.message_dict) from exc
        provider.refresh_from_db()
        return Response(_provider_json(providers.describe(provider)))

    @extend_schema(request=None, responses={204: None})
    def delete(self, request: Request, slug: str) -> Response:
        provider = self._find(slug)
        if provider is None:
            return Response(_PROVIDER_LOCKED, status=status.HTTP_409_CONFLICT)
        if (refused := _needs_recent_sign_in(request)) is not None:
            return refused
        try:
            providers.delete(request.user, provider)  # type: ignore[arg-type]
        except DjangoValidationError as exc:
            raise exceptions.ValidationError(exc.message_dict) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)


class InstanceProviderCheckView(APIView):
    """The test button: read an issuer's OpenID configuration and check it; saves nothing."""

    permission_classes = [InstanceAdminOnly, CredentialFitsView]

    @extend_schema(
        request=inline_serializer("ProviderCheckRequest", {"issuer": serializers.CharField()}),
        responses={
            200: inline_serializer(
                "ProviderCheck",
                {"ok": serializers.BooleanField(), "detail": serializers.CharField()},
            )
        },
    )
    def post(self, request: Request) -> Response:
        if not allow(request, "housecairn_settings_test"):
            raise exceptions.Throttled()
        issuer = request.data.get("issuer") if isinstance(request.data, dict) else None
        ok, detail = providers.check(request.user, str(issuer or ""))  # type: ignore[arg-type]
        return Response({"ok": ok, "detail": detail})


class InstanceEmailTestView(APIView):
    """Connect to the mail server with the settings in force, to check them; sends nothing."""

    permission_classes = [InstanceAdminOnly, CredentialFitsView]

    @extend_schema(
        request=None,
        responses={
            200: inline_serializer(
                "EmailTest", {"ok": serializers.BooleanField(), "detail": serializers.CharField()}
            )
        },
    )
    def post(self, request: Request) -> Response:
        if not allow(request, "housecairn_settings_test"):
            raise exceptions.Throttled()
        ok, detail = settings_services.check_email(request.user)  # type: ignore[arg-type]
        return Response({"ok": ok, "detail": detail})
