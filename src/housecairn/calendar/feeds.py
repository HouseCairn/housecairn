"""Creating, regenerating, revoking and finding calendar feeds (docs/adr/0052).

Only a person signed in with a session manages their feeds: an API token never creates,
reads or changes one, since a feed address is a credential that outlives the token.
"""

import contextvars
import secrets
from typing import Any

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from housecairn.accounts.models import User
from housecairn.apitokens.services import hash_token
from housecairn.audit import services as audit
from housecairn.audit.models import AuditEvent
from housecairn.calendar.models import Feed
from housecairn.homes import services as home_services
from housecairn.homes.models import Home, Membership

AnyUser = User | AnonymousUser
DEPARTURES = frozenset({"member.left", "member.removed"})


def _person(actor: AnyUser) -> User:
    if not isinstance(actor, User) or not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied
    return actor


def feed_homes(user: User) -> models.QuerySet[Home]:
    """The homes a feed may carry: the person's, minus those that require a sign-in provider.

    A feed address is a bearer secret and cannot prove which provider opened the session.
    """
    return home_services.open_homes(home_services.homes_for(user), frozenset()).order_by("name")


def feeds_of(actor: AnyUser) -> models.QuerySet[Feed]:
    return Feed.objects.filter(user=_person(actor)).select_related("home")


def _secret() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)  # 256 bits
    return raw, hash_token(raw)


def _record(action: str, user: User, home: Home | None, kind: str) -> None:
    audit.record(action, actor=user, home_id=home.id if home else None, kind=kind)


@transaction.atomic
def create_feed(actor: AnyUser, home: Home | None = None) -> tuple[Feed, str]:
    """Create the feed of `home` (all the homes when None) and return it with its secret.

    An archived home gets no new feed (docs/adr/0079): its feeds stay valid but empty, so a
    new one would hold nothing until the home is brought back. A feed it already has can
    still be regenerated or revoked.
    """
    user = _person(actor)
    if home is not None and not feed_homes(user).filter(pk=home.pk).exists():
        raise PermissionDenied
    if home is not None and home.archived_at is not None:
        raise ValidationError(_("This home is archived. Bring it back to make a feed of it."))
    raw, digest = _secret()
    try:
        with transaction.atomic():
            feed = Feed.objects.create(user=user, home=home, digest=digest)
    except IntegrityError:
        raise ValidationError(
            _("You already have a feed for this. Regenerate it to get a new address.")
        ) from None
    _record("calendar.feed_created", user, home, "home" if home else "combined")
    return feed, raw


@transaction.atomic
def regenerate_feed(actor: AnyUser, feed: Feed) -> tuple[Feed, str]:
    """Give the feed a new secret: the old address stops working at once."""
    user = _person(actor)
    if feed.user_id != user.pk:
        raise PermissionDenied
    raw, feed.digest = _secret()
    feed.regenerated_at = timezone.now()
    feed.save(update_fields=["digest", "regenerated_at", "updated_at"])
    _record("calendar.feed_regenerated", user, feed.home, "home" if feed.home_id else "combined")
    return feed, raw


@transaction.atomic
def revoke_feed(actor: AnyUser, feed: Feed) -> None:
    user = _person(actor)
    if feed.user_id != user.pk:
        raise PermissionDenied
    home, kind = feed.home, "home" if feed.home_id else "combined"
    feed.delete()
    _record("calendar.feed_revoked", user, home, kind)


def find_feed(raw: str) -> Feed | None:
    """The feed this secret opens, or None: unknown, revoked, inactive account, home left."""
    feed: Feed | None = (
        Feed.objects.select_related("user", "home").filter(digest=hash_token(raw)).first()
    )
    if feed is None or not feed.user.is_active:
        return None
    if feed.home_id is not None and not feed_homes(feed.user).filter(pk=feed.home_id).exists():
        return None
    return feed


# The feeds a membership took with it, until the audit entry of the departure says who did it.
_departed: contextvars.ContextVar[dict[tuple[Any, Any], list[str]] | None] = contextvars.ContextVar(
    "departed", default=None
)


def membership_saved(sender: Any, instance: Membership, **kwargs: Any) -> None:
    """A person who leaves a home (or is removed) no longer has a feed of it.

    The feed goes at once, in the same transaction, so that an address cannot come back to
    life if the person joins the home again. Who did it is not known here (`homes` does not
    say), so the audit entry waits for the departure's own entry: `departure_recorded`.
    """
    if instance.deleted_at is None:
        return
    doomed = list(Feed.objects.filter(user_id=instance.user_id, home_id=instance.home_id))
    if not doomed:
        return
    pending = _departed.get() or {}
    pending[(instance.home_id, instance.user_id)] = [str(feed.pk) for feed in doomed]
    _departed.set(pending)
    for feed in doomed:
        feed.delete()


def departure_recorded(sender: Any, instance: AuditEvent, created: bool, **kwargs: Any) -> None:
    """The audit entry of a departure ("left" or "removed") names who revoked the feed.

    A person who leaves is their own actor; an owner who removes someone is the actor, and the
    person is the target. The entry is written here, not when the membership ends, because
    only the departure's entry knows the actor.
    """
    if not created or instance.action not in DEPARTURES:
        return
    pending = _departed.get() or {}
    revoked = pending.pop((instance.home_id, instance.target_user_id), None)
    if not revoked:
        return
    for _feed in revoked:
        audit.record(
            "calendar.feed_revoked",
            actor=instance.actor,
            home_id=instance.home_id,
            target=instance.target_user,
            kind="left",
        )
