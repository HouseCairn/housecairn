"""The rate limits of django-allauth and of HouseCairn, from the instance settings (ADR 0042).

`settings.ACCOUNT_RATE_LIMITS` is a `RateLimits`: django-allauth reads it at each limited
request (`allauth.account.app_settings.RATE_LIMITS`), and each read asks `get_setting`,
so a limit changed in the interface applies within the few seconds of the settings
snapshot, without a restart. This module imports no model: `settings.py` uses it.
"""

from collections.abc import Iterator, Mapping


def rates() -> dict[str, str]:
    """The rate of each action, in allauth's format (amount/period/key)."""
    from housecairn.demo import mode
    from housecairn.instance.config import get_setting

    def n(key: str) -> int:
        return int(get_setting(f"ratelimit_{key}"))

    # On a demonstration instance the failures are counted by address only: the accounts are
    # public, and a count by account would let anyone lock every visitor out (ADR 0071).
    failed = "10/m/ip" if mode.is_on() else f"10/m/ip,{n('login_failures')}/300s/key"
    invite = f"{n('invitations')}/m/user,{n('invitations_day')}/d/user"
    return {
        # allauth's own actions; the defaults are those of allauth or of ADR 0024.
        "login": f"{n('login')}/m/ip",
        "login_failed": failed,
        "signup": f"{n('signup')}/m/ip,{n('signup_day')}/d/ip",
        "reset_password": f"{n('password_reset')}/m/ip,5/m/key",
        # HouseCairn's (ADR 0020, 0022, 0023, 0035, 0041).
        "housecairn_invite": invite,
        "housecairn_instance_invite": invite,
        "housecairn_invitation": f"{n('invitation_links')}/m/ip",
        "housecairn_settings_test": f"{n('tests')}/m/user",
        "housecairn_delete_account": "5/m/user",
        # The first-account screen, signed out: every look and every try counts (ADR 0056).
        "housecairn_setup": "10/m/ip,60/h/ip",
        # Adding a link while previews are on reads a web page (ADR 0050): per person, and
        # for all the members of a home together (the key is the home).
        "housecairn_link_preview": f"{n('link_previews')}/m/user,{n('link_previews_home')}/m/key",
        # A scanned code looked up while the online lookup is on sends it to the open
        # databases (docs/adr/0083): per person, and for all the members of a home together.
        "housecairn_barcode_lookup": "10/m/user,30/m/key",
        # The secret address of a calendar feed (ADR 0052): a calendar application polls it.
        "housecairn_calendar_feed": "60/m/ip",
        # The reads of the sync API, per connected phone (docs/adr/0077, need 11).
        "housecairn_sync_read": "600/m/key",
        # The snapshot of a home is built in memory from every row: a few a minute per phone,
        # on top of the reads above (a phone asks for it on its first sync or after a 410).
        "housecairn_sync_snapshot": "6/m/key",
        # The pushes of the sync API, per connected phone, apart from its reads.
        "housecairn_sync_push": "60/m/key",
    }


class RateLimits(Mapping[str, str]):
    """A mapping that is computed again at each read; allauth merges it into its own."""

    def __getitem__(self, action: str) -> str:
        return rates()[action]

    def __iter__(self) -> Iterator[str]:
        return iter(rates())

    def __len__(self) -> int:
        return len(rates())

    def __repr__(self) -> str:
        return f"RateLimits({rates()!r})"
