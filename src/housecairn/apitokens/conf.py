"""The setting of the mobile app, paired with its variable (docs/adr/0035, 0078).

Added to `housecairn.instance.registry` at the end of that module; like it, this module imports
no model, since `housecairn.settings` reads it before the database exists.
"""

from django.utils.translation import gettext_lazy as _

from housecairn.instance.registry import Kind, Section, Setting

SETTINGS: tuple[Setting, ...] = (
    Setting(
        "mobile_app_enabled",
        "HOUSECAIRN_MOBILE_APP_ENABLED",
        "HOUSECAIRN_MOBILE_APP_ENABLED",
        Kind.BOOL,
        Section.MOBILE_APP,
        _("Mobile app"),
        _(
            "People may connect the HouseCairn mobile app to their account, each phone with "
            "the homes they choose, and revoke it from their access page. Off: no phone can "
            "connect or sync; the connected phones stay listed and work again when it is "
            "turned back on."
        ),
        True,
    ),
)
