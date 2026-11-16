from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class GuideConfig(AppConfig):
    name = "housecairn.guide"
    label = "guide"
    verbose_name = _("Guide")
