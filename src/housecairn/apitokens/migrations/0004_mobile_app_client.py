"""The one OAuth client of every instance: the HouseCairn mobile app (docs/adr/0078).

Public (no secret), authorization code with PKCE and refresh tokens, its private-use redirect
URI matched exactly, consent never skipped.
"""

from typing import Any

from django.db import migrations

CLIENT_ID = "housecairn-mobile"


def create_client(apps: Any, schema_editor: Any) -> None:
    application = apps.get_model("oauth2_provider", "Application")
    application.objects.update_or_create(
        client_id=CLIENT_ID,
        defaults={
            "name": "HouseCairn mobile app",
            "client_type": "public",
            "authorization_grant_type": "authorization-code",
            "redirect_uris": "app.housecairn.mobile:/oauth/callback",
            "post_logout_redirect_uris": "",
            "allowed_origins": "",
            "client_secret": "",
            "hash_client_secret": False,
            "skip_authorization": False,
            "algorithm": "",
            "user": None,
        },
    )


def remove_client(apps: Any, schema_editor: Any) -> None:
    apps.get_model("oauth2_provider", "Application").objects.filter(client_id=CLIENT_ID).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("apitokens", "0003_app_connections"),
        ("oauth2_provider", "0022_refreshtoken_token_family_index"),
    ]

    operations = [migrations.RunPython(create_client, remove_client)]
