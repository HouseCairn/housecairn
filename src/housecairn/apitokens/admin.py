from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from housecairn.apitokens import services
from housecairn.apitokens.models import ApiToken


@admin.register(ApiToken)
class ApiTokenAdmin(admin.ModelAdmin[ApiToken]):
    """Listing and revocation only: tokens are issued by their owner."""

    list_display = ("name", "prefix", "user", "scopes", "expires_at", "revoked_at", "last_used_at")
    list_filter = ("revoked_at",)
    search_fields = ("name", "prefix", "user__email")
    readonly_fields = (
        "id",
        "user",
        "name",
        "prefix",
        "scopes",
        "signed_in_provider",
        "expires_at",
        "revoked_at",
        "last_used_at",
        "created_at",
    )
    # Neither the secret nor the homes the token opens: an administrator may list and
    # revoke tokens, and learns nothing of any home (docs/adr/0022).
    exclude = ("digest", "homes")
    actions = ("revoke",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: ApiToken | None = None) -> bool:
        # Revoke instead: a deletion page would list the homes of the token.
        return False

    @admin.action(description=_("Revoke selected tokens"))
    def revoke(self, request: HttpRequest, queryset: QuerySet[ApiToken]) -> None:
        for token in queryset:
            services.revoke_token(request.user, token)  # type: ignore[arg-type]


# django-oauth-toolkit registers its models in the admin site: none of them is shown but the
# client of the mobile app, read only. No other client is created from the interface, and no
# token or grant is listed (docs/adr/0078, 0022).
def _replace_oauth_admin() -> None:
    from oauth2_provider.models import (
        get_access_token_model,
        get_application_model,
        get_grant_model,
        get_id_token_model,
        get_refresh_token_model,
    )

    for model in (
        get_application_model(),
        get_access_token_model(),
        get_grant_model(),
        get_id_token_model(),
        get_refresh_token_model(),
    ):
        if admin.site.is_registered(model):
            admin.site.unregister(model)

    application = get_application_model()

    class ApplicationAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
        list_display = ("name", "client_id", "client_type", "authorization_grant_type")
        fields = ("name", "client_id", "client_type", "authorization_grant_type", "redirect_uris")
        readonly_fields = fields

        def has_add_permission(self, request: HttpRequest) -> bool:
            return False

        def has_change_permission(self, request: HttpRequest, obj: object = None) -> bool:
            return False

        def has_delete_permission(self, request: HttpRequest, obj: object = None) -> bool:
            return False

    admin.site.register(application, ApplicationAdmin)


_replace_oauth_admin()
