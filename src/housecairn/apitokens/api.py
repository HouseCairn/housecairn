"""Management of API tokens: `GET/POST /me/tokens/` and `DELETE /me/tokens/<id>/`.

For a person signed in, never for a token: a token that could mint tokens would turn
a read-only integration into one that outlives its own revocation. Writes need the CSRF
token of the session (DRF's session authentication). A person sees and revokes their own
tokens; another person's is a 404.
"""

from typing import Any
from uuid import UUID

from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import exceptions, permissions, serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from housecairn.accounts.models import User
from housecairn.apitokens import services
from housecairn.apitokens.models import ApiToken, Scope
from housecairn.apitokens.permissions import CredentialFitsView, SessionOnly
from housecairn.homes import sso
from housecairn.homes.models import Home

# These routes take the cookie of a signed-in person only, never a token (see SessionOnly).
# drf-spectacular types `auth` as names but writes each item as a security requirement,
# which OpenAPI wants as an object: a bare name is rejected by `spectacular --validate`.
SESSION_AUTH: Any = [{"cookieAuth": []}]


class TokenSerializer(serializers.ModelSerializer[ApiToken]):
    homes = serializers.SerializerMethodField(help_text="The ids of the homes the token opens.")
    token = serializers.CharField(
        read_only=True,
        required=False,
        help_text="The secret (`hc_...`). Only in the answer that creates the token, and "
        "never again: only a digest is kept.",
    )

    class Meta:
        model = ApiToken
        fields = [
            "id",
            "name",
            "prefix",
            "scopes",
            "homes",
            "created_at",
            "expires_at",
            "last_used_at",
            "token",
        ]
        read_only_fields = fields

    def get_homes(self, token: ApiToken) -> list[UUID]:
        return sorted(home.id for home in token.homes.all())


class TokenCreateSerializer(serializers.Serializer[Any]):
    name = serializers.CharField(max_length=100, help_text="What the token is for.")
    scopes = serializers.ListField(
        child=serializers.ChoiceField(choices=Scope.choices),
        required=False,
        allow_empty=False,
        help_text="`read` (the default), `write` and `events`. They do not imply each other.",
    )
    homes = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
        help_text="The homes the token opens, among those of the person.",
    )
    expires_in_days = serializers.ChoiceField(
        choices=services.LIFETIME_CHOICES,
        required=False,
        help_text="Lifetime in days: 30, 90 or 365. There is no token without an expiry. "
        "Defaults to the lifetime of the instance, 90 days unless it is set otherwise.",
    )

    def validate_name(self, value: str) -> str:
        name = " ".join(value.split())
        if not name:
            raise serializers.ValidationError("This field may not be blank.")
        return name


def _own_token(request: Request, token_id: Any) -> ApiToken:
    user = request.user
    assert isinstance(user, User)  # noqa: S101 - authentication ran
    found: ApiToken = get_object_or_404(services.tokens_of(user), pk=token_id)
    return found


class TokenListView(APIView):
    permission_classes = [CredentialFitsView, permissions.IsAuthenticated, SessionOnly]

    @extend_schema(auth=SESSION_AUTH, responses=TokenSerializer(many=True))
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        user = request.user
        assert isinstance(user, User)  # noqa: S101 - authentication ran
        return Response(TokenSerializer(services.tokens_of(user), many=True).data)

    @extend_schema(
        auth=SESSION_AUTH, request=TokenCreateSerializer, responses={201: TokenSerializer}
    )
    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        user = request.user
        assert isinstance(user, User)  # noqa: S101 - authentication ran
        serializer = TokenCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        wanted: set[UUID] = set(data["homes"])
        homes: list[Home] = list(services.token_homes(user).filter(pk__in=wanted))
        # The same answer for a home that does not exist and for one that is not theirs.
        if {home.id for home in homes} != wanted:
            raise exceptions.ValidationError({"homes": ["Choose one of your homes."]})
        try:
            # A home of theirs that requires a provider this session did not sign in
            # through raises SsoRequiredError: 403 with `code: sso_required`.
            token, raw = services.create_token(
                user,
                name=data["name"],
                scopes=data.get("scopes") or [Scope.READ],
                homes=homes,
                lifetime_days=data.get("expires_in_days", services.default_lifetime_days()),
                signed_in_with=sso.signed_in_providers(request),
            )
        except DjangoValidationError as exc:
            raise exceptions.ValidationError({"detail": exc.messages}) from None
        body = dict(TokenSerializer(token).data)
        body["token"] = raw
        response = Response(body, status=status.HTTP_201_CREATED)
        response["Cache-Control"] = "no-store"
        return response


class TokenDetailView(APIView):
    permission_classes = [CredentialFitsView, permissions.IsAuthenticated, SessionOnly]

    @extend_schema(
        auth=SESSION_AUTH,
        responses={204: None},
        description="Revoke a token: it stops working with the next request that uses it.",
    )
    def delete(self, request: Request, token_id: Any, *args: Any, **kwargs: Any) -> Response:
        user = request.user
        assert isinstance(user, User)  # noqa: S101 - authentication ran
        services.revoke_token(user, _own_token(request, token_id))
        return Response(status=status.HTTP_204_NO_CONTENT)
