from django.apps import AppConfig
from django.db.models.signals import post_migrate
from django.utils.translation import gettext_lazy as _


class HomesConfig(AppConfig):
    name = "housecairn.homes"
    label = "homes"
    verbose_name = _("Homes")

    def ready(self) -> None:
        from housecairn.homes import jobs, syncing

        # The worker removes expired export archives every hour (docs/adr/0048).
        post_migrate.connect(jobs.schedule_purge, sender=self)
        syncing.register()
