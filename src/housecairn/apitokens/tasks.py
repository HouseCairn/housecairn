"""Background task of connected apps, run by the worker of the container (docs/adr/0008, 0078).

Every night, django-oauth-toolkit's `cleartokens` removes the expired access tokens, the
used authorization codes and the refresh tokens that were revoked or expired. The link
tables of `apitokens` follow by cascade; the connections themselves stay listed.
"""

from django.db import DatabaseError

SCHEDULE = "housecairn.apitokens.clear_expired"


def clear_expired() -> None:
    """Run by the schedule: the toolkit's own `cleartokens`."""
    from oauth2_provider.models import clear_expired as toolkit_clear_expired

    toolkit_clear_expired()


def schedule(**_kwargs: object) -> None:
    """Create the daily schedule of `clear_expired` once; called after `migrate`."""
    from django_q.models import Schedule

    try:
        Schedule.objects.get_or_create(
            name=SCHEDULE,
            defaults={
                "func": "housecairn.apitokens.tasks.clear_expired",
                "schedule_type": Schedule.DAILY,
                "repeats": -1,
            },
        )
    except DatabaseError:  # before django_q's tables exist
        return
