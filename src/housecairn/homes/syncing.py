"""What of a home the sync API sends (docs/adr/0077, 0079)."""

from housecairn.homes.models import Home
from housecairn.sync import rows


def register() -> None:
    rows.register(
        Home,
        rows.Spec(
            # A rule of the instance (docs/adr/0021), not data of the home: a phone neither reads
            # nor writes it. The mark of the example home (docs/adr/0084) is the same: a column
            # of the server that the protocol ignores, and a pushed one is refused as forbidden.
            skip=frozenset({"required_provider", "is_example"}),
            # An archive made on the web reads as the clock of its write (ADR 0077, need 9).
            write_clock=frozenset({"archived_at"}),
        ),
    )
