from django.apps import AppConfig
from django.db.models.signals import post_migrate


class SyncConfig(AppConfig):
    name = "housecairn.sync"
    label = "sync"
    verbose_name = "Sync"

    def ready(self) -> None:
        from housecairn.sync import tasks

        # The worker purges what the retention has expired every night (docs/adr/0077).
        post_migrate.connect(tasks.schedule, sender=self)
