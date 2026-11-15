"""OpenAPI description of the token authentication (loaded by the app config)."""

from typing import Any

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class ApiTokenScheme(OpenApiAuthenticationExtension):  # type: ignore[no-untyped-call]
    target_class = "housecairn.apitokens.authentication.ApiTokenAuthentication"
    name = "apiToken"

    def get_security_definition(self, auto_schema: Any) -> dict[str, str]:
        return {
            "type": "http",
            "scheme": "bearer",
            "description": "HouseCairn API token (hc_...), with scopes read, write or events.",
        }


class ConnectedAppScheme(OpenApiAuthenticationExtension):  # type: ignore[no-untyped-call]
    target_class = "housecairn.apitokens.authentication.ConnectionAuthentication"
    name = "connectedApp"
    priority = 1

    def get_security_definition(self, auto_schema: Any) -> dict[str, str]:
        return {
            "type": "http",
            "scheme": "bearer",
            "description": "Access token of a connected app (hca_...), for the sync API only.",
        }
