from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ProjectsConfig(AppConfig):
    name = "housecairn.projects"
    label = "projects"
    verbose_name = _("Projects")

    def ready(self) -> None:
        from housecairn.homes import files
        from housecairn.projects import exports, syncing

        exports.register()
        syncing.register()
        files.register_media_folder("projects")
