"""API v1 of homes, households and invitations. Validation only; rules live in `services`."""

import uuid
from typing import Any

from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models, transaction
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_field, inline_serializer
from rest_framework import exceptions, mixins, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from housecairn.accounts.models import User
from housecairn.accounts.names import person_name
from housecairn.apitokens.credentials import is_session
from housecairn.apitokens.permissions import CredentialFitsView
from housecairn.audit import services as audit_services
from housecairn.audit.api import AuditEventSerializer
from housecairn.demo.mode import DemoRefusedError
from housecairn.homes import csv_export, csv_import, exports, jobs, restore, services, sso
from housecairn.homes.models import ExportJob, Home, Invitation, Membership, Role
from housecairn.homes.scoping import (
    HomeScopedMixin,
    allow,
    delivery_for,
    homes_for_request,
    invitation_link,
)


def _validation(exc: DjangoValidationError) -> serializers.ValidationError:
    return serializers.ValidationError({"detail": exc.messages})


class NoTokenForHousehold(permissions.BasePermission):
    """Who belongs to a home is changed only by a signed-in person.

    An API token, even with the `write` scope, cannot invite, change roles or
    remove members (docs/adr/0020): a leaked integration token must not let
    anyone in. It can read the household with the `read` scope.
    """

    message = "API tokens cannot change who belongs to a home; sign in instead."

    def has_permission(self, request: Request, view: Any) -> bool:
        if request.method in permissions.SAFE_METHODS:
            return True
        return is_session(request)


# ---------- homes ----------


class HomeSerializer(serializers.ModelSerializer[Home]):
    role = serializers.SerializerMethodField()
    locked = serializers.SerializerMethodField(
        help_text="True when the home requires a sign-in provider this session did not use."
    )
    required_provider = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=100,
        help_text="Identifier of the sign-in provider members must use; empty for none.",
    )

    class Meta:
        model = Home
        fields = [
            "id",
            "name",
            "currency",
            "hemisphere",
            "role",
            "required_provider",
            "locked",
            "archived_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "role", "locked", "archived_at", "created_at", "updated_at"]
        extra_kwargs = {
            "archived_at": {
                "help_text": (
                    "When an owner archived the home; null when it is not archived. Changed "
                    "by `archive/` and `unarchive/` only."
                )
            }
        }

    @extend_schema_field(serializers.ChoiceField(choices=Role.choices, allow_null=True))
    def get_role(self, home: Home) -> str | None:
        role = services.role_of(self.context["request"].user, home)
        return role.value if role else None

    def get_locked(self, home: Home) -> bool:
        return not services.is_open(home, sso.signed_in_providers(self.context["request"]))


class ExportJobSerializer(serializers.ModelSerializer[ExportJob]):
    download = serializers.SerializerMethodField(help_text="POST here to download, when ready.")

    class Meta:
        model = ExportJob
        fields = ["id", "status", "size_bytes", "downloads_left", "expires_at", "download"]
        read_only_fields = fields

    def get_download(self, job: ExportJob) -> str:
        return reverse(
            "api-v1:home-export-download", kwargs={"home_id": job.home_id, "job_id": job.id}
        )


class ImportSerializer(serializers.Serializer[Any]):
    rooms = serializers.FileField(required=False, help_text="CSV of rooms.")
    items = serializers.FileField(required=False, help_text="CSV of items.")
    tasks = serializers.FileField(required=False, help_text="CSV of tasks.")
    stock = serializers.FileField(required=False, help_text="CSV of the stock.")
    contractors = serializers.FileField(required=False, help_text="CSV of contractors.")
    confirm = serializers.BooleanField(
        required=False,
        default=False,
        help_text="False (the default): preview only, nothing is written. True: write.",
    )


class RestoreSerializer(serializers.Serializer[Any]):
    archive = serializers.FileField(help_text="The zip file made by the export, as it is.")
    as_copy = serializers.BooleanField(
        required=False,
        default=False,
        help_text=(
            "False (the default): the home comes back with its identifiers. True: a copy, "
            "with new identifiers, next to the original; only you are its member."
        ),
    )
    name = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=restore.NAME_MAX,
        help_text='Name of the copy; empty, the name of the home followed by "(copy)".',
    )


class HomeViewSet(
    HomeScopedMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet[Home],
):
    """Homes of the authenticated user (narrowed to the token's homes, if any).

    The list includes homes locked behind a sign-in provider (`locked`); reading
    or changing one answers 403 with `code: sso_required` until the session
    signs in through it. It also includes archived homes (`archived_at` is set): the
    screens hide them, the API does not, so an integration keeps seeing its homes.
    """

    serializer_class = HomeSerializer
    lookup_field = "id"
    lookup_url_kwarg = "home_id"
    http_method_names = ["get", "post", "patch", "head", "options"]
    # What an archived home still takes (docs/adr/0089); `partial_update` and `import_tables`
    # are refused with 403 `home_archived`.
    archived_home_writes = frozenset(
        {"archive", "unarchive", "export", "export_download", "delete_home", "leave", "unlock"}
    )

    def get_queryset(self) -> models.QuerySet[Home]:
        return homes_for_request(self.request).order_by("name")

    def get_object(self) -> Home:
        return self.get_home()

    def perform_create(self, serializer: serializers.BaseSerializer[Home]) -> None:
        if not is_session(self.request):
            # A token is bound to existing homes; creating one needs a sign-in.
            raise exceptions.PermissionDenied("API tokens cannot create homes.")
        data = serializer.validated_data
        try:
            serializer.instance = services.create_home(
                self.request.user,
                name=data["name"],
                currency=data.get("currency", "EUR"),
                hemisphere=data.get("hemisphere", "north"),
            )
        except DjangoValidationError as exc:
            raise _validation(exc) from exc

    def partial_update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        home = self.get_object()
        serializer = self.get_serializer(home, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        changes = dict(serializer.validated_data)
        provider = changes.pop("required_provider", None)
        if provider is not None and not is_session(request):
            # Refused before anything is written: no rename applied, then a 403.
            raise exceptions.PermissionDenied(NoTokenForHousehold.message)
        try:
            # One transaction: a refused sign-in requirement leaves the name as it was.
            with transaction.atomic():
                if changes:
                    home = services.update_home(request.user, home, **changes)
                if provider is not None:
                    home = services.set_required_provider(
                        request.user,
                        home,
                        provider,
                        declared=[p.id for p in sso.declared_providers(request)],
                        signed_in_with=sso.signed_in_providers(request),
                    )
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(self.get_serializer(home).data)

    @extend_schema(request=None, responses={200: HomeSerializer})
    @action(detail=True, methods=["post"])
    def archive(self, request: Request, home_id: str) -> Response:
        """Archive the home: hidden from the screens' lists and Today, no reminder, an empty
        calendar feed. Nothing is deleted. Owners only, signed in: an API token cannot."""
        return self._archiving(request, archive=True)

    @extend_schema(request=None, responses={200: HomeSerializer})
    @action(detail=True, methods=["post"])
    def unarchive(self, request: Request, home_id: str) -> Response:
        """Bring an archived home back. Owners only, signed in: an API token cannot."""
        return self._archiving(request, archive=False)

    def _archiving(self, request: Request, *, archive: bool) -> Response:
        if not is_session(request):
            # A leaked integration token must not silence the reminders of a home.
            raise exceptions.PermissionDenied(
                "API tokens cannot archive or bring back a home; sign in instead."
            )
        home = self.get_home()
        change = services.archive_home if archive else services.unarchive_home
        try:
            home = change(request.user, home)
        except PermissionDenied as exc:
            raise exceptions.PermissionDenied() from exc
        return Response(self.get_serializer(home).data)

    @extend_schema(request=None, responses={204: None})
    @action(detail=True, methods=["post"])
    def leave(self, request: Request, home_id: str) -> Response:
        """Leave the home. The last owner cannot leave."""
        if not is_session(request):
            raise exceptions.PermissionDenied(NoTokenForHousehold.message)
        home = self.get_home(allow_locked=True)  # leaving never needs the provider
        try:
            services.leave_home(request.user, home)  # type: ignore[arg-type]
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(responses={200: AuditEventSerializer(many=True)})
    @action(detail=True, methods=["get"])
    def audit(self, request: Request, home_id: str) -> Response:
        """Audit log of the home: roles, invitations, sign-in rule, export. Owners only."""
        home = self.get_home()
        if not is_session(request):
            raise exceptions.PermissionDenied(NoTokenForHousehold.message)
        try:
            entries = audit_services.home_log(request.user, home.id)
        except PermissionDenied as exc:
            raise exceptions.PermissionDenied() from exc
        page = self.paginate_queryset(entries)  # type: ignore[arg-type]
        return self.get_paginated_response(AuditEventSerializer(page, many=True).data)

    @extend_schema(
        request=None,
        responses={(200, "application/zip"): OpenApiTypes.BINARY},
        description=(
            "Everything of the home as one archive: `export.json` (versioned, documented in "
            "Administration > Export and deletion) and the home's files. Owners only, signed "
            "in: an API token cannot export."
        ),
    )
    @action(detail=True, methods=["post"])
    def export(self, request: Request, home_id: str) -> Any:
        home = self.get_home()
        if not is_session(request):
            raise exceptions.PermissionDenied(NoTokenForHousehold.message)
        try:
            exports.check_owner(request.user, home)
            if jobs.is_large(home):
                job = jobs.start_export(request.user, home)
                return Response(
                    ExportJobSerializer(job, context={"request": request}).data,
                    status=status.HTTP_202_ACCEPTED,
                )
            archive, name = exports.export_home(request.user, home)
        except DemoRefusedError:
            raise
        except PermissionDenied as exc:
            raise exceptions.PermissionDenied() from exc
        return FileResponse(
            archive, as_attachment=True, filename=name, content_type="application/zip"
        )

    def _job(self, request: Request, job_id: str) -> tuple[Home, ExportJob]:
        home = self.get_home()
        if not is_session(request):
            raise exceptions.PermissionDenied(NoTokenForHousehold.message)
        try:
            return home, jobs.job_of(request.user, home, uuid.UUID(job_id))
        except PermissionDenied as exc:
            raise exceptions.PermissionDenied() from exc
        except (jobs.ExportUnavailableError, ValueError):
            raise exceptions.NotFound() from None

    @extend_schema(responses={200: ExportJobSerializer})
    @action(detail=True, methods=["get"], url_path=r"export/(?P<job_id>[0-9a-f-]{36})")
    def export_job(self, request: Request, home_id: str, job_id: str) -> Response:
        """An archive made in the background: `pending`, `ready` or `failed`. Owners only."""
        _home, job = self._job(request, job_id)
        return Response(ExportJobSerializer(job, context={"request": request}).data)

    @extend_schema(request=None, responses={(200, "application/zip"): OpenApiTypes.BINARY})
    @action(detail=True, methods=["post"], url_path=r"export/(?P<job_id>[0-9a-f-]{36})/download")
    def export_download(self, request: Request, home_id: str, job_id: str) -> Any:
        """Download the archive of a job; each call uses one of its downloads. Owners only."""
        home, job = self._job(request, job_id)
        try:
            archive, name = jobs.open_download(request.user, home, job.id)
        except jobs.ExportUnavailableError:
            raise exceptions.NotFound() from None
        return FileResponse(
            archive, as_attachment=True, filename=name, content_type="application/zip"
        )

    @extend_schema(
        responses={(200, "text/csv"): OpenApiTypes.BINARY},
        description=(
            "One table of the home as CSV (UTF-8 with a byte order mark): `rooms`, `items`, "
            "`tasks`, `history`, `stock`, `contractors` or `reports`. Every member, and a "
            "token with the `read` scope on this home."
        ),
    )
    @action(detail=True, methods=["get"], url_path=r"export/(?P<entity>[a-z_]+)\.csv")
    def export_csv(self, request: Request, home_id: str, entity: str) -> HttpResponse:
        home = self.get_home()
        found = csv_export.ENTITIES.get(entity)
        if found is None:
            raise exceptions.NotFound()
        data, name = csv_export.build_csv(home, found)
        response = HttpResponse(data, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        return response

    @extend_schema(
        request={"multipart/form-data": ImportSerializer},
        responses={200: OpenApiTypes.OBJECT, 201: OpenApiTypes.OBJECT},
        description=(
            "Import CSV tables (the columns of `export/<table>.csv`, separated by commas, "
            "UTF-8 with or without a byte order mark) into the home. Without `confirm`, a "
            "preview: every row accepted or refused with its reasons, nothing written (200; "
            "`confirmable` says whether it can be written). With `confirm=true`, the rows are "
            "written in one transaction (201), all or nothing: a single row refused, and "
            "nothing is written (400, with the reasons). "
            "Owners and members, and a token with the `write` scope on this home."
        ),
    )
    @action(
        detail=True,
        methods=["post"],
        url_path="import",
        url_name="import",
        parser_classes=[MultiPartParser],
    )
    def import_tables(self, request: Request, home_id: str) -> Response:
        home = self.get_home()
        if not services.can_write(request.user, home):
            raise exceptions.PermissionDenied()
        if int(request.META.get("CONTENT_LENGTH") or 0) > csv_import.MAX_BYTES * 2:
            raise exceptions.ValidationError({"detail": [csv_import.too_large_message()]})
        serializer = ImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        uploads = {
            name: serializer.validated_data[name]
            for name in csv_import.entities()
            if serializer.validated_data.get(name)
        }
        if not uploads:
            raise exceptions.ValidationError({"detail": ["Give at least one CSV file."]})
        confirm = serializer.validated_data["confirm"]
        try:
            texts = csv_import.texts_from_uploads(uploads)
            report = csv_import.run(request.user, home, texts, confirm=confirm)
        except csv_import.ImportRefusedError as exc:
            raise serializers.ValidationError({"detail": exc.messages}) from exc
        except PermissionDenied as exc:  # pragma: no cover - checked above
            raise exceptions.PermissionDenied() from exc
        if report.error or (confirm and report.refused):
            # All or nothing: a row refused, and the confirmation wrote none of them.
            return Response(report.as_data(), status=status.HTTP_400_BAD_REQUEST)
        code = status.HTTP_201_CREATED if report.written else status.HTTP_200_OK
        return Response(report.as_data(), status=code)

    @extend_schema(
        request={"multipart/form-data": RestoreSerializer},
        responses={201: HomeSerializer},
        description=(
            "Restore the archive of a home (made by `export`) as a new home owned by the "
            "person signed in. Refused with 400 and the reason when the archive is altered, "
            "of an unknown version, or already on this instance. With `as_copy`, a copy with "
            "new identifiers, which may sit next to the original; refused with 403 when the "
            "home of the archive is on this instance and you are not one of its owners. "
            "An API token cannot."
        ),
    )
    @action(detail=False, methods=["post"], parser_classes=[MultiPartParser])
    def restore(self, request: Request) -> Response:
        if not is_session(request):
            raise exceptions.PermissionDenied("API tokens cannot restore a home; sign in instead.")
        declared = int(request.META.get("CONTENT_LENGTH") or 0)
        if declared > restore.max_unpacked_bytes():
            raise exceptions.ValidationError({"archive": ["This file is too large."]})
        serializer = RestoreSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        as_copy = serializer.validated_data["as_copy"]
        try:
            result = restore.restore_home(
                request.user,
                serializer.validated_data["archive"],
                as_copy=as_copy,
                name=serializer.validated_data["name"] if as_copy else "",
            )
        except restore.CopyNotAllowedError as exc:
            raise exceptions.PermissionDenied(exc.message) from exc
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"archive": exc.messages}) from exc
        data = dict(self.get_serializer(result.home).data)
        data["unattributed"] = result.unattributed
        return Response(data, status=status.HTTP_201_CREATED)

    @extend_schema(
        request=inline_serializer("HomeDeletion", {"confirmation": serializers.CharField()}),
        responses={204: None},
        description="Delete the home for good. The confirmation is the exact name of the home.",
    )
    @action(detail=True, methods=["post"], url_path="delete")
    def delete_home(self, request: Request, home_id: str) -> Response:
        home = self.get_home()
        if not is_session(request):
            raise exceptions.PermissionDenied(NoTokenForHousehold.message)
        confirmation = request.data.get("confirmation") if isinstance(request.data, dict) else None
        try:
            services.delete_home(request.user, home, confirmation=str(confirmation or ""))
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=None, responses={200: HomeSerializer})
    @action(detail=True, methods=["post"])
    def unlock(self, request: Request, home_id: str) -> Response:
        """Stop requiring a sign-in provider the instance no longer offers (owners)."""
        home = self.get_home(allow_locked=True)
        if not is_session(request):
            raise exceptions.PermissionDenied(NoTokenForHousehold.message)
        try:
            home = services.lift_unavailable_requirement(
                request.user,
                home,
                declared=[p.id for p in sso.declared_providers(request)],
            )
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(self.get_serializer(home).data)


# ---------- members ----------


class MemberUserSerializer(serializers.ModelSerializer[User]):
    name = serializers.SerializerMethodField(
        help_text="The name shown for the person: the display name, then the full name, "
        "then the part of the address before the @."
    )

    class Meta:
        model = User
        fields = ["id", "email", "name"]
        read_only_fields = fields

    def get_name(self, user: User) -> str:
        return person_name(user)


class MembershipSerializer(serializers.ModelSerializer[Membership]):
    user = MemberUserSerializer(read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "user", "role", "created_at", "updated_at"]
        read_only_fields = ["id", "user", "created_at", "updated_at"]


class MemberViewSet(
    HomeScopedMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet[Membership],
):
    """Members of a home. Owners change roles, remove members and hand over ownership."""

    serializer_class = MembershipSerializer
    # For the schema only (types of the path parameters); get_queryset scopes.
    queryset = Membership.objects.none()
    lookup_field = "id"
    lookup_url_kwarg = "member_id"
    http_method_names = ["get", "patch", "delete", "post", "head", "options"]
    # Removing a member, or leaving, stays open in an archived home (docs/adr/0089).
    archived_home_writes = frozenset({"destroy"})

    def get_permissions(self) -> list[Any]:
        return [*super().get_permissions(), NoTokenForHousehold()]

    def get_queryset(self) -> models.QuerySet[Membership]:
        return services.members(self.get_home())

    @extend_schema(
        request=inline_serializer("RoleChange", {"role": serializers.ChoiceField(Role.choices)})
    )
    def partial_update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        membership = self.get_object()
        role = request.data.get("role") if isinstance(request.data, dict) else None
        if role not in Role.values:
            raise serializers.ValidationError({"role": ["Choose owner, member or read_only."]})
        try:
            membership = services.change_role(request.user, membership, role)
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(self.get_serializer(membership).data)

    def destroy(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        membership = self.get_object()
        try:
            services.remove_member(request.user, membership)
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=None, responses={200: MembershipSerializer})
    @action(detail=True, methods=["post"], url_path="make-owner")
    def make_owner(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Make this member owner; the acting owner becomes a member."""
        membership = self.get_object()
        try:
            membership = services.transfer_ownership(
                request.user,  # type: ignore[arg-type]
                membership.home,
                membership,
            )
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(self.get_serializer(membership).data)


# ---------- invitations ----------


class InvitationSerializer(serializers.ModelSerializer[Invitation]):
    link = serializers.SerializerMethodField(
        help_text=(
            "The invitation link, only in the answer that creates it and only when the "
            "instance does not send email; shown once."
        )
    )

    class Meta:
        model = Invitation
        fields = ["id", "email", "role", "sent_at", "expires_at", "link"]
        read_only_fields = ["id", "sent_at", "expires_at", "link"]

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_link(self, invitation: Invitation) -> str | None:
        raw = self.context.get("raw")
        return invitation_link(self.context["request"], raw) if raw else None


class InvitationViewSet(
    HomeScopedMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet[Invitation],
):
    """Pending invitations of a home; owners only."""

    serializer_class = InvitationSerializer
    # For the schema only (types of the path parameters); get_queryset scopes.
    queryset = Invitation.objects.none()
    lookup_field = "id"
    lookup_url_kwarg = "invitation_id"
    http_method_names = ["get", "post", "delete", "head", "options"]
    # Revoking an invitation stays open in an archived home (docs/adr/0089).
    archived_home_writes = frozenset({"destroy"})

    def get_permissions(self) -> list[Any]:
        return [*super().get_permissions(), NoTokenForHousehold()]

    def owned_home(self) -> Home:
        home = self.get_home()
        if not services.is_owner(self.request.user, home):
            raise PermissionDenied
        return home

    def get_queryset(self) -> models.QuerySet[Invitation]:
        return services.pending_invitations(self.owned_home())

    def invitation(self) -> Invitation:
        invitation: Invitation = get_object_or_404(
            Invitation.objects.filter(home=self.owned_home()), id=self.kwargs["invitation_id"]
        )
        return invitation

    def _limit(self) -> None:
        if not allow(self.request, "housecairn_invite"):
            raise exceptions.Throttled()

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        home = self.owned_home()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self._limit()
        try:
            invitation, raw = services.invite(
                request.user,  # type: ignore[arg-type]
                home,
                email=serializer.validated_data["email"],
                role=serializer.validated_data.get("role", Role.MEMBER),
                delivery=delivery_for(request),
            )
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        data = InvitationSerializer(invitation, context={"request": request, "raw": raw}).data
        return Response(data, status=status.HTTP_201_CREATED)

    def destroy(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Revoke the invitation: its link stops working."""
        services.revoke_invitation(request.user, self.invitation())  # type: ignore[arg-type]
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=None, responses={200: InvitationSerializer})
    @action(detail=True, methods=["post"])
    def resend(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """A new link and a new expiry; the previous link stops working."""
        invitation = self.invitation()
        self._limit()
        try:
            invitation, raw = services.resend_invitation(
                request.user,  # type: ignore[arg-type]
                invitation,
                delivery=delivery_for(request),
            )
        except DjangoValidationError as exc:
            raise _validation(exc) from exc
        return Response(
            InvitationSerializer(invitation, context={"request": request, "raw": raw}).data
        )


class AcceptInvitationView(APIView):
    """Accept an invitation with the secret of its link, for the signed-in account.

    The account's address must be the invited one. Unknown, used, revoked and
    expired secrets all answer 404.
    """

    permission_classes = [CredentialFitsView, permissions.IsAuthenticated, NoTokenForHousehold]

    @extend_schema(
        request=inline_serializer("InvitationAccept", {"token": serializers.CharField()}),
        responses={200: HomeSerializer},
    )
    def post(self, request: Request) -> Response:
        if not allow(request, "housecairn_invitation"):
            raise exceptions.Throttled()
        raw = request.data.get("token") if isinstance(request.data, dict) else None
        try:
            membership = services.accept_invitation(request.user, raw or "")  # type: ignore[arg-type]
        except Invitation.DoesNotExist as exc:
            raise exceptions.NotFound() from exc
        except services.UnverifiedAddressError as exc:
            # The message says what to do: verify the address (docs/adr/0020).
            raise exceptions.PermissionDenied(exc.args[0]) from exc
        return Response(HomeSerializer(membership.home, context={"request": request}).data)
