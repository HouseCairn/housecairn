from django.apps import AppConfig
from django.db.models.signals import post_migrate
from django.utils.translation import gettext_lazy as _


class ApiTokensConfig(AppConfig):
    name = "housecairn.apitokens"
    label = "apitokens"
    verbose_name = _("API tokens")

    def ready(self) -> None:
        from housecairn.apitokens import (
            schema,  # noqa: F401 - registers the extension
            tasks,
        )

        post_migrate.connect(tasks.schedule, sender=self)
