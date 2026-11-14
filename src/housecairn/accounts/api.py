"""API of the signed-in person's own account: profile and erasure (docs/adr/0023, 0045)."""

from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import exceptions, permissions, serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from housecairn.accounts import names, provider_names, services
from housecairn.accounts.models import DisplayNameSource, User
from housecairn.apitokens.credentials import is_session
from housecairn.apitokens.permissions import CredentialFitsView
from housecairn.homes.scoping import allow


class SignedInPerson(permissions.BasePermission):
    """A person signed in: an API token, which stands for an integration, is not one."""

    message = "This changes the account itself and needs the person signed in; API tokens cannot."

    def has_permission(self, request: Request, view: object) -> bool:
        return bool(request.user.is_authenticated) and is_session(request)


class PersonOnlyToChange:
    """Mixin: reading follows the view's own permissions; any other method is for a person.

    The rule for everything under `me/` that belongs to the person (account, display
    preferences, notification channels and reminders): an API token is tied to homes
    and may read where it could, but changes nothing here.
    """

    def get_permissions(self) -> list[Any]:
        found: list[Any] = list(super().get_permissions())  # type: ignore[misc]
        if self.request.method not in permissions.SAFE_METHODS:  # type: ignore[attr-defined]
            found.append(SignedInPerson())
        return found


class MeSerializer(serializers.ModelSerializer[User]):
    display_name = serializers.CharField(
        max_length=names.DISPLAY_NAME_MAX_LENGTH,
        required=False,
        allow_blank=True,
        help_text="How the other members of the homes see the person. Optional.",
    )
    name = serializers.SerializerMethodField(
        help_text="The name shown for the person: the display name, then the full name, "
        "then the part of the address before the @."
    )
    display_name_source = serializers.ChoiceField(
        choices=DisplayNameSource.choices,
        required=False,
        help_text="`oidc`: the sign-in provider sets the display name at each sign-in through "
        "it (setting it back to `oidc` takes the provider's last name at once when there is "
        "one); `manual`: the person typed it and no provider changes it. Changing "
        "`display_name` makes it `manual`.",
    )
    display_name_provider = serializers.CharField(
        read_only=True,
        help_text="Identifier of the provider the display name was taken from; empty when "
        "the person typed it.",
    )

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "display_name",
            "display_name_source",
            "display_name_provider",
            "name",
        ]
        read_only_fields = ["id", "email", "display_name_provider"]

    def get_name(self, user: User) -> str:
        return names.person_name(user)


class MeView(PersonOnlyToChange, APIView):
    """The signed-in person: who they are, and how they are named.

    An API token may read it. Changing it is for the person signed in: the name is seen in
    every home of the person, which is wider than the one home a token can be tied to.
    """

    @extend_schema(responses=MeSerializer)
    def get(self, request: Request) -> Response:
        user = request.user
        assert isinstance(user, User)  # noqa: S101 - authentication ran
        return Response(MeSerializer(user).data)

    @extend_schema(request=MeSerializer, responses=MeSerializer)
    def patch(self, request: Request) -> Response:
        user = request.user
        assert isinstance(user, User)  # noqa: S101 - authentication ran
        serializer = MeSerializer(user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        if "display_name" in serializer.validated_data:
            try:
                services.set_display_name(user, serializer.validated_data["display_name"])
            except DjangoValidationError as exc:
                raise serializers.ValidationError({"display_name": exc.messages}) from exc
        source = serializer.validated_data.get("display_name_source")
        if source == DisplayNameSource.OIDC:
            provider_names.use_provider_name(user)
        elif source == DisplayNameSource.MANUAL:
            provider_names.keep_typed_name(user)
        return Response(MeSerializer(user).data)


class AccountDeleteView(APIView):
    permission_classes = [SignedInPerson, CredentialFitsView]

    @extend_schema(
        request=inline_serializer(
            "AccountDeletion",
            {
                "confirm_email": serializers.EmailField(),
                "password": serializers.CharField(required=False),
            },
        ),
        responses={204: None},
        description=(
            "Erase the account of the signed-in person, for good. The address (and the "
            "password, for an account that has one) confirm it. Refused while the account "
            "is the only owner of a home with other members, or the only administrator."
        ),
    )
    def post(self, request: Request) -> Response:
        user = request.user
        assert isinstance(user, User)  # noqa: S101 - SignedInPerson ran
        data = request.data if isinstance(request.data, dict) else {}
        # Every attempt counts, before the address or the password is looked at.
        if not allow(request, "housecairn_delete_account"):
            raise exceptions.Throttled()
        if not services.same_address(str(data.get("confirm_email") or ""), user.email):
            raise exceptions.ValidationError({"confirm_email": ["This is not your address."]})
        if user.has_usable_password() and not user.check_password(str(data.get("password") or "")):
            raise exceptions.ValidationError({"password": ["The password is not correct."]})
        try:
            services.delete_account(user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"detail": exc.messages}) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)
