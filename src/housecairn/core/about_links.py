"""The links of the About screen, in one place.

Every address the About screen points to is written here and nowhere else: the project
site, the documentation, the three repositories, the iOS beta and the Android store. The
iOS beta address (`TESTFLIGHT_URL`) is also the one More points to: change it here only. A
store whose address is empty is shown as a "Coming soon" icon without a link; to publish
the application in a store, set its address in `STORE_URLS`: the icon becomes a link, with
no other change.

The links only leave the page when someone follows them: the screen loads nothing from
these hosts (docs/adr/0011).
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _

SITE_URL = "https://housecairn.app"
REPOSITORY_URLS = {
    "addon": "https://github.com/HouseCairn/home-assistant-addons",
    "integration": "https://github.com/HouseCairn/home-assistant-housecairn",
    "mobile": "https://github.com/HouseCairn/housecairn-mobile",
}
# The public beta of the iOS application. The one place that holds this address.
TESTFLIGHT_URL = "https://testflight.apple.com/join/eygTuxTT"
# Empty until the application is published in the store.
STORE_URLS = {"android": ""}


@dataclass(frozen=True)
class AboutLink:
    key: str
    label: str | Promise
    icon: str
    url: str
    # Opens in a new tab: the links that lead to a service other than the project's own.
    new_tab: bool = False

    @property
    def available(self) -> bool:
        return bool(self.url)


def links() -> list[AboutLink]:
    """The links of the first row, then the stores, in display order."""
    return [
        AboutLink("site", _("Website"), "globe", SITE_URL),
        AboutLink("docs", _("Documentation"), "book", settings.HOUSECAIRN_DOCS_URL),
        AboutLink("app", _("Application code"), "code", settings.HOUSECAIRN_SOURCE_URL),
        AboutLink("addon", _("Home Assistant app"), "puzzle", REPOSITORY_URLS["addon"]),
        AboutLink(
            "integration", _("Home Assistant integration"), "plug", REPOSITORY_URLS["integration"]
        ),
        AboutLink("mobile", _("Mobile application"), "phone", REPOSITORY_URLS["mobile"]),
        AboutLink(
            "ios", _("iOS app (TestFlight beta)"), "phone-download", TESTFLIGHT_URL, new_tab=True
        ),
        AboutLink("android", "Android", "phone-download", STORE_URLS["android"]),
    ]
