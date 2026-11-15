from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class TasksConfig(AppConfig):
    name = "housecairn.tasks"
    label = "tasks"
    verbose_name = _("Tasks")

    def ready(self) -> None:
        from housecairn.homes import files
        from housecairn.tasks import exports, syncing

        exports.register()
        syncing.register()
        files.register_media_folder("attachments")
