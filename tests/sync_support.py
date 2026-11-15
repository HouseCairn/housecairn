"""Helpers of the tests of the change log."""

from housecairn.accounts.models import User
from housecairn.homes.models import Home, Membership, Role


def quiet_home(owner: User, name: str = "Main house") -> Home:
    """A home of `owner` whose change log is empty: made straight in the database.

    Creating a home through the service records its change. The tests of the log itself pin
    positions and clocks from a known start, so they begin from a home that has none.
    """
    home = Home.objects.create(name=name)
    Membership.objects.create(home=home, user=owner, role=Role.OWNER)
    return home
