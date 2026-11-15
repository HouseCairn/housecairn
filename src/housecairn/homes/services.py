"""Business rules about homes, households and invitations (docs/adr/0002, 0006, 0020, 0021).

Web views and API views call these functions; neither holds rules of its own.
"""

import hashlib
import logging
import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from housecairn.accounts.models import User
from housecairn.audit import models as audit_models
from housecairn.audit import services as audit
from housecairn.demo import mode
from housecairn.homes import files
from housecairn.homes.models import WRITE_ROLES, Hemisphere, Home, Invitation, Membership, Role
from housecairn.instance.config import get_setting

AnyUser = User | AnonymousUser

INVITATION_PREFIX = "hci_"

logger = logging.getLogger("housecairn.mail")


class DeliveryFailedError(Exception):
    """The invitation exists but its email could not be sent (docs/adr/0020).

    The link was never shown to anybody and only its digest is kept: sending the
    invitation again makes a new one.
    """

    def __init__(self, invitation: Any) -> None:
        super().__init__("invitation email not sent")
        self.invitation = invitation


def deliver(
    invitation: Any, raw: str, send: Callable[[Any, str], None] | None
) -> tuple[Any, str | None]:
    """Send the link by email, outside any transaction of ours.

    A mail server that is down must neither undo the invitation nor become a
    server error: it is logged, without the address, and reported to the caller.
    """
    if send is None:
        return invitation, raw
    try:
        send(invitation, raw)
    except Exception as exc:
        logger.warning("invitation email not sent: %s", type(exc).__name__)
        raise DeliveryFailedError(invitation) from exc
    return invitation, None


class SsoRequiredError(Exception):
    """The home requires a sign-in through a provider this session did not use."""

    def __init__(self, home: Home) -> None:
        super().__init__(home.required_provider)
        self.home = home
        self.provider_id = home.required_provider


def homes_for(user: AnyUser, *, restrict_to: Iterable[UUID] | None = None) -> models.QuerySet[Home]:
    """Homes the user belongs to; optionally only those in `restrict_to`.

    This is the single entry point that limits every query to the user's homes.
    """
    if not user.is_authenticated or not user.is_active:
        return Home.objects.none()
    member_of = Membership.objects.filter(user=user).values("home_id")
    homes = Home.objects.filter(id__in=member_of)
    if restrict_to is not None:
        homes = homes.filter(id__in=list(restrict_to))
    return homes


def open_homes(homes: models.QuerySet[Home], providers: frozenset[str]) -> models.QuerySet[Home]:
    """Of `homes`, those whose sign-in requirement `providers` satisfies (docs/adr/0021)."""
    return homes.filter(models.Q(required_provider="") | models.Q(required_provider__in=providers))


def active_homes(homes: models.QuerySet[Home]) -> models.QuerySet[Home]:
    """Of `homes`, those not archived: what the lists, Today and the reminders cover (ADR 0079).

    Reading, changing or exporting a home opened by its address does not go through here:
    an archived home stays readable and editable.
    """
    return homes.filter(archived_at__isnull=True)


def is_open(home: Home, providers: frozenset[str]) -> bool:
    return not home.required_provider or home.required_provider in providers


def check_open(home: Home, providers: frozenset[str]) -> None:
    if not is_open(home, providers):
        raise SsoRequiredError(home)


def role_of(user: AnyUser, home: Home) -> Role | None:
    if not user.is_authenticated:
        return None
    membership = Membership.objects.filter(user=user, home=home).first()
    return Role(membership.role) if membership else None


def can_write(user: AnyUser, home: Home) -> bool:
    return role_of(user, home) in WRITE_ROLES


def is_owner(user: AnyUser, home: Home) -> bool:
    return role_of(user, home) == Role.OWNER


def _require_owner(user: AnyUser, home: Home) -> None:
    if not is_owner(user, home):
        raise PermissionDenied


@transaction.atomic
def create_home(
    owner: User, *, name: str, currency: str = "EUR", hemisphere: str = Hemisphere.NORTH
) -> Home:
    home = Home(name=name, currency=currency, hemisphere=hemisphere)
    home.full_clean()
    home.save()
    Membership.objects.create(home=home, user=owner, role=Role.OWNER)
    audit.record("home.created", actor=owner, home_id=home.id)
    return home


@transaction.atomic
def update_home(actor: AnyUser, home: Home, **changes: Any) -> Home:
    if not can_write(actor, home):
        raise PermissionDenied
    allowed = {"name", "currency", "hemisphere"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"Cannot change: {', '.join(sorted(unknown))}")
    moved = "hemisphere" in changes and changes["hemisphere"] != home.hemisphere
    for field, value in changes.items():
        setattr(home, field, value)
    home.full_clean()
    home.save(update_fields=[*changes, "updated_at"])
    if moved:
        # The predefined seasons of the tasks follow the hemisphere (docs/adr/0045).
        from housecairn.tasks import services as task_services  # imported late: tasks import homes

        task_services.resync_seasons(home)
    return home


def set_required_provider(
    actor: AnyUser,
    home: Home,
    provider_id: str,
    *,
    declared: Iterable[str],
    signed_in_with: frozenset[str],
) -> Home:
    """Require (or, with "", stop requiring) a sign-in provider for this home.

    Only an owner may, only for a provider the instance declares, and only from
    a session signed in through that provider: an owner cannot lock the home
    against the session that sets the rule, nor name a provider nobody can use.
    """
    _require_owner(actor, home)
    if provider_id:
        if provider_id not in set(declared):
            raise ValidationError(_("This sign-in provider is not offered by this instance."))
        if provider_id not in signed_in_with:
            raise ValidationError(
                _("Sign in through this provider first, then require it for the home.")
            )
    home.required_provider = provider_id
    home.save(update_fields=["required_provider", "updated_at"])
    if provider_id:
        audit.record("sso.required", actor=actor, home_id=home.id, provider=provider_id)
    else:
        audit.record("sso.lifted", actor=actor, home_id=home.id)
    return home


def lift_unavailable_requirement(actor: AnyUser, home: Home, *, declared: Iterable[str]) -> Home:
    """Stop requiring a sign-in provider the instance no longer offers.

    The way out when the administrator removes a provider from the
    configuration: nobody could otherwise open the home to change the rule. An
    owner only, and only for a provider that is gone: a provider that is still
    offered keeps its protection (docs/adr/0021).
    """
    _require_owner(actor, home)
    if not home.required_provider or home.required_provider in set(declared):
        raise ValidationError(_("This home's sign-in provider is still offered by this instance."))
    home.required_provider = ""
    home.save(update_fields=["required_provider", "updated_at"])
    audit.record("sso.lifted", actor=actor, home_id=home.id)
    return home


def lift_all_unavailable_requirements(*, declared: Iterable[str]) -> int:
    """Instance-wide version for the administrator: how many homes were freed.

    Names no home and reads none: it only clears a rule that points at a provider
    that no longer exists.
    """
    return int(
        Home.objects.exclude(required_provider="")
        .exclude(required_provider__in=list(declared))
        .update(required_provider="", updated_at=timezone.now())
    )


# ---------- archiving ----------


@transaction.atomic
def archive_home(actor: AnyUser, home: Home) -> Home:
    """An owner archives the home: listed apart, no reminder, an empty calendar feed.

    Nothing is deleted and nothing becomes read only (docs/adr/0079). Archiving a home that
    is archived already changes nothing and records nothing.
    """
    _require_owner(actor, home)
    if home.archived_at is None:
        home.archived_at = timezone.now()
        home.save(update_fields=["archived_at", "updated_at"])
        audit.record(
            "home.archived", actor=actor if isinstance(actor, User) else None, home_id=home.id
        )
    return home


@transaction.atomic
def unarchive_home(actor: AnyUser, home: Home) -> Home:
    """An owner brings the home back: it is listed again and its reminders resume."""
    _require_owner(actor, home)
    if home.archived_at is not None:
        home.archived_at = None
        home.save(update_fields=["archived_at", "updated_at"])
        audit.record(
            "home.unarchived", actor=actor if isinstance(actor, User) else None, home_id=home.id
        )
    return home


# ---------- deletion ----------


@transaction.atomic
def erase_home(home: Home) -> None:
    """Delete a home and everything that hangs off it, for good (docs/adr/0023).

    Rows go by cascade, soft-deleted ones included, and so does the change log of the
    sync API; so do the home's entries in its audit log and, when files exist, its
    directory of files. An API token left with no home is revoked. What remains is a
    count in the instance log, and one row in the table of erased homes, written first
    with the connected apps bound to the home, so that the sync API can say it is gone
    (docs/adr/0077).
    """
    from housecairn.apitokens.models import ApiToken
    from housecairn.sync import erased

    home_id = home.pk
    tokens = list(ApiToken.objects.filter(homes=home).values_list("pk", flat=True))
    erased.record_erased(home_id)
    Home.all_objects.filter(pk=home_id).delete()
    audit_models.AuditEvent.objects.filter(home_id=home_id).delete()
    ApiToken.objects.filter(pk__in=tokens, homes__isnull=True, revoked_at__isnull=True).update(
        revoked_at=timezone.now()
    )
    transaction.on_commit(lambda: files.erase_home_files(home_id))


@transaction.atomic
def delete_home(actor: AnyUser, home: Home, *, confirmation: str) -> None:
    """An owner deletes the home, for good, after typing its name.

    The confirmation is the name as it is shown: a typo, or a request that does
    not know the name (a script run by mistake), changes nothing. There is no
    grace period: the specification asks for a definitive deletion, so the
    owner is told to export first (docs/administration/data.md).
    """
    _require_owner(actor, home)
    if confirmation.strip() != home.name.strip():
        raise ValidationError(_("Type the exact name of the home to delete it."))
    erase_home(home)
    audit.record("home.deleted", actor=actor if isinstance(actor, User) else None)


# ---------- household ----------


def members(home: Home) -> models.QuerySet[Membership]:
    """Active members of the home, owners first."""
    order = models.Case(
        models.When(role=Role.OWNER, then=0),
        models.When(role=Role.MEMBER, then=1),
        default=2,
    )
    return (
        Membership.objects.filter(home=home).select_related("user").order_by(order, "user__email")
    )


def _lock_memberships(home: Home) -> list[Membership]:
    """Lock the home's memberships for the rest of the transaction."""
    return list(Membership.objects.select_for_update().filter(home=home))


def _check_an_owner_remains(memberships: list[Membership]) -> None:
    if not any(m.role == Role.OWNER for m in memberships):
        raise ValidationError(_("A home always keeps at least one owner."))


@transaction.atomic
def change_role(actor: AnyUser, membership: Membership, role: Role | str) -> Membership:
    """An owner changes the role of a member, themselves included."""
    new_role = Role(role)
    home = membership.home
    _require_owner(actor, home)
    locked = _lock_memberships(home)
    target = next((m for m in locked if m.pk == membership.pk), None)
    if target is None:
        raise Membership.DoesNotExist
    previous = target.role
    target.role = new_role
    _check_an_owner_remains(locked)
    target.save(update_fields=["role", "updated_at"])
    audit.record(
        "member.role_changed",
        actor=actor,
        home_id=home.id,
        target=target.user,
        role=str(new_role),
        previous_role=str(previous),
    )
    return target


@transaction.atomic
def remove_member(actor: AnyUser, membership: Membership) -> None:
    """An owner removes a member; anyone may remove themselves (leaving)."""
    home = membership.home
    if membership.user_id != getattr(actor, "pk", None):
        _require_owner(actor, home)
    locked = _lock_memberships(home)
    target = next((m for m in locked if m.pk == membership.pk), None)
    if target is None:
        raise Membership.DoesNotExist
    _check_an_owner_remains([m for m in locked if m.pk != target.pk])
    target.soft_delete()
    audit.record(
        "member.left" if target.user_id == getattr(actor, "pk", None) else "member.removed",
        actor=actor,
        home_id=home.id,
        target=target.user,
    )


def leave_home(actor: User, home: Home) -> None:
    membership = Membership.objects.filter(home=home, user=actor).first()
    if membership is None:
        raise Membership.DoesNotExist
    remove_member(actor, membership)


@transaction.atomic
def transfer_ownership(actor: User, home: Home, to: Membership) -> Membership:
    """The acting owner makes another member owner and becomes a member."""
    _require_owner(actor, home)
    locked = _lock_memberships(home)
    target = next((m for m in locked if m.pk == to.pk), None)
    mine = next((m for m in locked if m.user_id == actor.pk), None)
    if target is None or mine is None:
        raise Membership.DoesNotExist
    if target.pk == mine.pk:
        raise ValidationError(_("Choose another member of the home."))
    target.role = Role.OWNER
    mine.role = Role.MEMBER
    target.save(update_fields=["role", "updated_at"])
    mine.save(update_fields=["role", "updated_at"])
    audit.record("ownership.transferred", actor=actor, home_id=home.id, target=target.user)
    return target


# ---------- invitations ----------


def hash_invitation(raw: str) -> str:
    """SHA-256, as for API tokens: the secret has 256 bits of entropy."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _new_secret() -> tuple[str, str]:
    raw = INVITATION_PREFIX + secrets.token_urlsafe(32)
    return raw, hash_invitation(raw)


def _lifetime() -> timedelta:
    return timedelta(days=get_setting("invitation_lifetime_days"))


def pending_invitations(home: Home) -> models.QuerySet[Invitation]:
    return Invitation.objects.filter(
        home=home, accepted_at__isnull=True, revoked_at__isnull=True, expires_at__gt=timezone.now()
    ).order_by("-sent_at")


def same_address(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


@dataclass(frozen=True)
class Delivery:
    """How an invitation reaches its recipient.

    `send(invitation, raw)` sends the link by email; without it the link is
    returned once to the person who invites, to pass on themselves.
    """

    send: Callable[[Invitation, str], None] | None = None


def invite(
    actor: User, home: Home, *, email: str, role: Role | str, delivery: Delivery
) -> tuple[Invitation, str | None]:
    """Invite an address to the home with a role.

    Returns the invitation and, when it was not sent by email, its link secret
    (shown once). The answer does not depend on whether an account uses the
    address, so inviting cannot be used to find out who has an account. The
    email goes out after the invitation is saved: if it fails, the invitation
    stays and `DeliveryFailedError` says so. A demonstration instance sends no email: there,
    only an invitation by link is made (docs/adr/0071).
    """
    _require_owner(actor, home)
    if delivery.send is not None:
        mode.refuse(mode.nothing_sent())
    address = email.strip()
    new_role = Role(role)
    with transaction.atomic():
        if any(same_address(m.user.email, address) for m in members(home)):
            raise ValidationError(_("This person is already a member of the home."))
        _retire_expired(home, address)
        if any(same_address(i.email, address) for i in pending_invitations(home)):
            raise ValidationError(
                _("An invitation to this address is already pending; send it again instead.")
            )
        raw, digest = _new_secret()
        now = timezone.now()
        invitation = Invitation(
            home=home,
            email=address,
            role=new_role,
            invited_by=actor,
            digest=digest,
            expires_at=now + _lifetime(),
            sent_at=now,
            delivered_by_email=delivery.send is not None,
        )
        invitation.full_clean(exclude=["digest"])
        try:
            with transaction.atomic():
                invitation.save()
        except IntegrityError as exc:
            # Two simultaneous invitations to one address: the database settled it.
            raise ValidationError(
                _("An invitation to this address is already pending; send it again instead.")
            ) from exc
        audit.record("invitation.sent", actor=actor, home_id=home.id, role=str(new_role))
    return deliver(invitation, raw, delivery.send)


def _retire_expired(home: Home, address: str) -> None:
    """Revoke the home's lapsed invitations to this address, so it can be invited again.

    A lapsed invitation opens nothing; it would otherwise keep the one-pending
    rule of the database (`unique_pending_invitation`) from letting a new one in.
    """
    now = timezone.now()
    Invitation.objects.filter(
        home=home,
        email__iexact=address,
        accepted_at__isnull=True,
        revoked_at__isnull=True,
        expires_at__lte=now,
    ).update(revoked_at=now)


def resend_invitation(
    actor: User, invitation: Invitation, *, delivery: Delivery
) -> tuple[Invitation, str | None]:
    """A new link and a new expiry; the previous link stops working."""
    _require_owner(actor, invitation.home)
    if delivery.send is not None:
        mode.refuse(mode.nothing_sent())
    if invitation.accepted_at is not None or invitation.revoked_at is not None:
        raise ValidationError(_("This invitation was already accepted or revoked."))
    raw, digest = _new_secret()
    now = timezone.now()
    invitation.digest = digest
    invitation.sent_at = now
    invitation.expires_at = now + _lifetime()
    invitation.delivered_by_email = delivery.send is not None
    with transaction.atomic():
        invitation.save(
            update_fields=["digest", "sent_at", "expires_at", "delivered_by_email", "updated_at"]
        )
        audit.record("invitation.resent", actor=actor, home_id=invitation.home_id)
    return deliver(invitation, raw, delivery.send)


def revoke_invitation(actor: User, invitation: Invitation) -> None:
    _require_owner(actor, invitation.home)
    if invitation.revoked_at is None and invitation.accepted_at is None:
        with transaction.atomic():
            invitation.revoked_at = timezone.now()
            invitation.save(update_fields=["revoked_at", "updated_at"])
            audit.record("invitation.revoked", actor=actor, home_id=invitation.home_id)


def find_invitation(raw: str) -> Invitation | None:
    """The pending invitation matching this link secret, or None.

    Unknown, accepted, revoked and expired links give the same None: whoever
    holds a dead link learns nothing more from it.
    """
    if not isinstance(raw, str) or not raw.startswith(INVITATION_PREFIX) or len(raw) > 200:
        return None
    invitation: Invitation | None = (
        Invitation.objects.select_related("home").filter(digest=hash_invitation(raw)).first()
    )
    if invitation is None or not invitation.is_pending or invitation.home.deleted_at is not None:
        return None
    return invitation


def has_pending_invitation(email: str) -> bool:
    """Whether some home has a pending invitation for this address."""
    return any(
        same_address(i.email, email)
        for i in Invitation.objects.filter(
            email__iexact=email.strip(),
            accepted_at__isnull=True,
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
    )


@transaction.atomic
def accept_invitation(user: User, raw: str) -> Membership:
    """Join the home of the invitation, which is then used up.

    The account's address must be the invited one: a forwarded or stolen link
    is of no use to another account. Someone already a member keeps their role.
    A link that went by email proves the address; a link handed over by hand
    proves nothing, so the account must have verified the address itself
    (docs/adr/0020): otherwise anyone could declare it and use the link.
    """
    invitation = find_invitation(raw)
    if invitation is None:
        raise Invitation.DoesNotExist
    # Lock the row: of two simultaneous acceptances, only one uses it.
    invitation = Invitation.objects.select_for_update().get(pk=invitation.pk)
    if not invitation.is_pending:
        raise Invitation.DoesNotExist
    if not same_address(user.email, invitation.email):
        raise PermissionDenied(_("This invitation is for another email address."))
    if invitation.delivered_by_email:
        # The link reached the mailbox of that address and nobody else saw it:
        # whoever opens it with this address proves the address (docs/adr/0020).
        verify_address(user, invitation.email)
    elif not has_verified_address(user, invitation.email):
        raise UnverifiedAddressError(
            _("Verify this address in your account first, then open the link again.")
        )
    membership: Membership | None = Membership.objects.filter(
        home=invitation.home, user=user
    ).first()
    if membership is None:
        membership = Membership.objects.create(
            home=invitation.home, user=user, role=invitation.role
        )
    invitation.accepted_at = timezone.now()
    invitation.accepted_by = user
    invitation.save(update_fields=["accepted_at", "accepted_by", "updated_at"])
    audit.record(
        "invitation.accepted", actor=user, home_id=invitation.home_id, role=str(membership.role)
    )
    return membership


class UnverifiedAddressError(PermissionDenied):
    """The account has the invited address but has not verified it (docs/adr/0020)."""


def has_verified_address(user: User, address: str) -> bool:
    """Whether this account has verified this address, case ignored."""
    from allauth.account.models import EmailAddress

    return any(
        same_address(record.email, address)
        for record in EmailAddress.objects.filter(
            user=user, email__iexact=address.strip(), verified=True
        )
    )


def verify_address(user: User, address: str) -> None:
    """Mark the account's address verified, adding its record if it has none."""
    from allauth.account.models import EmailAddress

    record = EmailAddress.objects.filter(user=user, email__iexact=address).first()
    if record is None:
        has_primary = EmailAddress.objects.filter(user=user, primary=True).exists()
        EmailAddress.objects.create(
            user=user, email=user.email, primary=not has_primary, verified=True
        )
    elif not record.verified:
        record.verified = True
        record.save(update_fields=["verified"])


@transaction.atomic
def create_account_for_invitation(raw: str, *, password: str) -> User:
    """Create the account of an invited address, then accept the invitation.

    The invitation is the way in when sign-up is closed. Only a link that went
    to the address by email can create the account, and that is what verifies
    the address. A link the owner handed over (the instance sends no email)
    proves nothing about the address and never creates an account: it could
    otherwise be used to take the address or to learn whether an account uses
    it (docs/adr/0020).
    """
    invitation = find_invitation(raw)
    if invitation is None:
        raise Invitation.DoesNotExist
    if not invitation.delivered_by_email:
        raise PermissionDenied(_("This link was not sent by email: it cannot create an account."))
    if User.objects.filter(email__iexact=invitation.email).exists():
        raise ValidationError(_("An account already uses this address: sign in to accept."))
    user = User.objects.create_user(email=invitation.email, password=password)
    accept_invitation(user, raw)
    return user
