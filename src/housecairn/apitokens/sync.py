"""The base of the sync API's views, and `GET /api/v1/sync/info` (docs/adr/0077, 0078).

Every view of `/api/v1/sync/` sets `sync_api = True`: only an access token of a connected app
opens it (`CredentialFitsView`, first in its permissions, so that an API token of any scope
or a session is refused with `sync_needs_connection` before anything else is checked), and it
follows the transport rule (`apitokens.transport`). Its answers carry `HC-Sync: 1`.
"""

from importlib import metadata
from typing import Any

from django.urls import reverse
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import permissions, serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from housecairn.apitokens import connections, transport
from housecairn.apitokens.authentication import CodedPermissionDenied
from housecairn.apitokens.credentials import connection_of
from housecairn.apitokens.oauth import CLIENT_ID
from housecairn.apitokens.permissions import CredentialFitsView, TokenScopePermission

SYNC_VERSIONS = [1]


class SyncAPIView(APIView):
    sync_api = True
    permission_classes = [CredentialFitsView, permissions.IsAuthenticated, TokenScopePermission]

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        reason = transport.refusal(request._request)
        if reason is not None:
            raise CodedPermissionDenied(reason, "insecure_transport")
        super().initial(request, *args, **kwargs)

    def finalize_response(self, request: Request, response: Any, *args: Any, **kwargs: Any) -> Any:
        response = super().finalize_response(request, response, *args, **kwargs)
        response["HC-Sync"] = "1"
        return response


def _server_version() -> str:
    try:
        return metadata.version("housecairn")
    except metadata.PackageNotFoundError:
        return "dev"


class SyncInfoView(SyncAPIView):
    """What the server speaks; with a connection's token, also the server and the connection."""

    sync_open = True  # the one route of the sync API open without a credential
    permission_classes = [CredentialFitsView]

    @extend_schema(
        responses=inline_serializer(
            "SyncInfo",
            {
                "sync": serializers.ListField(child=serializers.IntegerField()),
                "oauth": serializers.DictField(allow_null=True),
                "server": serializers.CharField(required=False),
                "features": serializers.ListField(required=False),
                "limits": serializers.DictField(required=False),
                "connection": serializers.DictField(required=False),
            },
        ),
        description=(
            "The versions of the sync protocol this server speaks and where the mobile app "
            "signs in; `oauth` is null when the instance turned the mobile app off. With an "
            "access token of a connected app, also the server's version, the limits it keeps "
            "to (`limits`: reads, snapshots and pushes per minute per connection, operations "
            "and bytes per push) and the connection."
        ),
    )
    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        oauth: dict[str, str] | None = None
        if connections.mobile_app_enabled():
            oauth = {
                "authorize": request.build_absolute_uri(reverse("oauth2_provider:authorize")),
                "token": request.build_absolute_uri(reverse("oauth2_provider:token")),
                "revoke": request.build_absolute_uri(reverse("oauth2_provider:revoke-token")),
                "client_id": CLIENT_ID,
            }
        from housecairn.sync import limits as sync_limits  # the sync app reads this module

        body: dict[str, Any] = {"sync": SYNC_VERSIONS, "oauth": oauth}
        connection = connection_of(request)
        if connection is not None:
            body.update(
                server=_server_version(),
                features=[],
                limits=sync_limits.announced(),
                connection={
                    "id": str(connection.id),
                    "may_create_homes": connection.may_create_homes,
                },
            )
        return Response(body)
