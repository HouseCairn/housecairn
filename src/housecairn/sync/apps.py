from django.apps import AppConfig
from django.db.models.signals import post_migrate


class SyncConfig(AppConfig):
    name = "housecairn.sync"
    label = "sync"
    verbose_name = "Sync"

    def ready(self) -> None:
        from housecairn.homes import files
        from housecairn.sync import storage, tasks

        # The files a phone uploaded count in the quota of their home and go with it.
        files.register_media_folder(storage.FOLDER)

        # The worker purges what the retention has expired every night (docs/adr/0077).
        post_migrate.connect(tasks.schedule, sender=self)
