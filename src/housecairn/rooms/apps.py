from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class RoomsConfig(AppConfig):
    name = "housecairn.rooms"
    label = "rooms"
    verbose_name = _("Rooms")

    def ready(self) -> None:
        from housecairn.homes import files
        from housecairn.rooms import checks, exports, syncing  # noqa: F401 - registers the check

        exports.register()
        syncing.register()
        files.register_media_folder("photos")
