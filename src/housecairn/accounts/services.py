"""Who may create an account by signing in through OIDC (docs/adr/0007).

Sign-up through OIDC is closed by default. A sign-in without a matching
account is refused, except:

- while the instance has never had an account: the first sign-in creates the
  instance administrator (bootstrap), optionally restricted to one verified
  address with `HOUSECAIRN_BOOTSTRAP_ADMIN_EMAIL`;
- when the operator opens sign-up to everyone the provider accepts
  (`HOUSECAIRN_OIDC_SIGNUP=open`);
- when a home has a pending invitation for the address, and the provider
  says the address is verified (`has_invitation`, docs/adr/0020).

Accounts created in the last two cases have no administration rights.
"""

import enum
import logging
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from housecairn.accounts import names
from housecairn.accounts.models import DisplayNameSource, InstanceSetup, SetupMethod, User
from housecairn.core.text import without_control_characters
from housecairn.instance.config import get_setting

logger = logging.getLogger("housecairn.security")

SIGNUP_CLOSED = "closed"
SIGNUP_OPEN = "open"


class Decision(enum.Enum):
    BOOTSTRAP_ADMIN = "bootstrap_admin"
    MEMBER = "member"
    REFUSED = "refused"


class FirstAccountTakenError(Exception):
    """Another sign-in created the first account in the meantime."""


def instance_has_accounts() -> bool:
    """True once the instance has had an account, even if it was deleted since."""
    return InstanceSetup.objects.exists() or User.objects.exists()


# Backends that do not deliver anywhere: the console one prints, the dummy one drops.
_NON_DELIVERING_BACKENDS = frozenset(
    {
        "django.core.mail.backends.console.EmailBackend",
        "django.core.mail.backends.dummy.EmailBackend",
    }
)


def email_is_configured() -> bool:
    """True when the instance can really send email.

    That takes an outgoing server (`EMAIL_HOST`) and a backend that delivers:
    the console backend, the default without `EMAIL_HOST`, only prints. Password
    reset by email is offered only then.
    """
    return (
        bool(get_setting("email_host")) and settings.EMAIL_BACKEND not in _NON_DELIVERING_BACKENDS
    )


def is_instance_admin(user: Any) -> bool:
    """The administrator of the instance: staff and superuser, signed in and active.

    An administrator runs the instance (accounts, invitations, unlocking) and
    reads no home: being one adds no row to `homes_for` (docs/adr/0022).
    """
    return bool(
        getattr(user, "is_authenticated", False)
        and user.is_active
        and user.is_staff
        and user.is_superuser
    )


def signup_is_open() -> bool:
    """Whether anyone may create a local account from the sign-up page."""
    return bool(get_setting("signup_open"))


def signup_domain_allowed(email: str | None) -> bool:
    """Whether sign-up is open to this address's domain.

    With `HOUSECAIRN_SIGNUP_ALLOWED_DOMAINS` set, only those domains; and never
    one of `HOUSECAIRN_SIGNUP_BLOCKED_DOMAINS`. Neither list set: any domain.
    Subdomains count as their parent (`mail.example.com` is `example.com`).
    """
    domain = (email or "").rpartition("@")[2].strip().lower().rstrip(".")
    allowed: list[str] = get_setting("signup_allowed_domains")
    blocked: list[str] = get_setting("signup_blocked_domains")

    def listed(names: list[str]) -> bool:
        return any(domain == name or domain.endswith("." + name) for name in names)

    if allowed and not (domain and listed(allowed)):
        return False
    return not (domain and listed(blocked))


def same_address(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def may_bootstrap(email: str | None, email_verified: bool) -> bool:
    """Whether this identity may become the first administrator."""
    pinned: str = settings.HOUSECAIRN_BOOTSTRAP_ADMIN_EMAIL
    if not pinned:
        return True
    return bool(email) and email_verified and same_address(email or "", pinned)


def has_invitation(email: str | None, email_verified: bool) -> bool:
    """Whether an invitation lets this identity sign up through OIDC.

    Only for an address the provider asserts as verified: otherwise anyone the
    provider accepts could claim the invited address.
    """
    if not email or not email_verified:
        return False
    from housecairn.homes.services import has_pending_invitation
    from housecairn.instance.services import has_pending

    return has_pending_invitation(email) or has_pending(email)


def oidc_signup_decision(email: str | None, email_verified: bool) -> Decision:
    """What a sign-in through OIDC without a matching account leads to."""
    if not instance_has_accounts():
        # Before the first account, only the bootstrap rule applies, even when
        # sign-up is open: nobody else may take the administrator's place.
        return (
            Decision.BOOTSTRAP_ADMIN if may_bootstrap(email, email_verified) else Decision.REFUSED
        )
    if has_invitation(email, email_verified):
        return Decision.MEMBER
    if get_setting("oidc_signup") == SIGNUP_OPEN and signup_domain_allowed(email):
        return Decision.MEMBER
    return Decision.REFUSED


def create_first_admin(user: User, save: Callable[[], User]) -> User:
    """Save `user` as the instance administrator, if it is still the first account.

    `save` stores the user and its linked identity. Everything runs in one
    transaction that also inserts the single `InstanceSetup` row: of two
    simultaneous first sign-ins, one inserts it and the other fails on the
    primary key (or, with SQLite, waits for the first and then sees its
    account). The loser gets `FirstAccountTakenError` and nothing of it is kept.
    """
    try:
        with transaction.atomic():
            if instance_has_accounts():
                raise FirstAccountTakenError
            setup = InstanceSetup.objects.create(method=SetupMethod.OIDC)
            user.is_staff = True
            user.is_superuser = True
            saved = save()
            setup.first_user = saved
            setup.save(update_fields=["first_user"])
    except IntegrityError as exc:
        raise FirstAccountTakenError from exc
    logger.warning(
        "first account created as instance administrator via OIDC",
        extra={"user_id": str(saved.pk)},
    )
    return saved


def record_first_account(user: User) -> None:
    """Mark the instance as set up when its first account is created otherwise."""
    if InstanceSetup.objects.exists():
        return
    try:
        with transaction.atomic():
            InstanceSetup.objects.create(method=SetupMethod.OTHER, first_user=user)
    except IntegrityError:
        # Another account was first; the instance is set up either way.
        pass


# ---------- erasure ----------


def is_last_instance_admin(user: User) -> bool:
    """Whether no other active administrator would remain without this account."""
    if not is_instance_admin(user):
        return False
    others = User.objects.filter(is_active=True, is_staff=True, is_superuser=True)
    return not others.exclude(pk=user.pk).exists()


def homes_blocking_erasure(user: User) -> list[Any]:
    """Homes in which this account is the only owner and other people are members.

    Erasing the account would leave them without an owner: the person hands
    ownership over, or deletes the home, first. A home where they are the only
    member is not in this list: it goes with the account.
    """
    from housecairn.homes.models import Home, Membership, Role

    owned = Membership.objects.filter(user=user, role=Role.OWNER).values("home_id")
    blocking = []
    for home in Home.objects.filter(id__in=owned).order_by("name"):
        members = Membership.objects.filter(home=home)
        if (
            not members.filter(role=Role.OWNER).exclude(user=user).exists()
            and members.exclude(user=user).exists()
        ):
            blocking.append(home)
    return blocking


def delete_account(user: User, *, by_admin: User | None = None) -> None:
    """Erase an account and what only it held (the right to erasure, docs/adr/0023).

    Refused for the only administrator, and for an account that is the only
    owner of a home others belong to; the message names no home, since an
    administrator may be the one asking. A home with no other member goes with
    the account. The audit log keeps that it happened, not who: its references
    to the account become empty.
    """
    from housecairn.audit import services as audit
    from housecairn.homes import services as home_services
    from housecairn.homes.models import Invitation, Membership
    from housecairn.instance.models import InstanceInvitation
    from housecairn.tasks import services as task_services

    # On a demonstration instance, deleting a public account is refused by the guard of the
    # model (demo.guards), inside the transaction below: nothing is erased (docs/adr/0071).
    if by_admin is not None and not is_instance_admin(by_admin):
        raise PermissionDenied
    with transaction.atomic():
        # Lock the memberships of the account's homes, as `remove_member` and
        # `change_role` do, so that a member leaving at the same moment is seen by the
        # rules below. Always in the same order, so that two erasures cannot wait on
        # each other.
        homes = Membership.objects.filter(user=user).values("home_id")
        list(
            Membership.objects.select_for_update()
            .filter(home_id__in=homes)
            .order_by("home_id", "pk")
        )
        if is_last_instance_admin(user):
            raise ValidationError(_("This is the only administrator of the instance."))
        blocking = homes_blocking_erasure(user)
        if blocking:
            raise ValidationError(
                _(
                    "This account is the only owner of %(count)s home(s) that have other "
                    "members: hand ownership over or delete the home first."
                )
                % {"count": len(blocking)}
            )
        # Homes where the account is the only member go with it.
        alone = [
            membership.home
            for membership in Membership.objects.filter(user=user).select_related("home")
            if not Membership.objects.filter(home=membership.home).exclude(user=user).exists()
        ]
        for home in alone:
            home_services.erase_home(home)
        # Invitations to the address, accepted or not, are not kept after erasure.
        Invitation.all_objects.filter(email__iexact=user.email).delete()
        InstanceInvitation.all_objects.filter(email__iexact=user.email).delete()
        audit.record(
            "admin.user_deleted" if by_admin else "account.deleted",
            actor=by_admin or user,
            by="administrator" if by_admin else "self",
        )
        # The tasks of the homes that stay lose the account (SET_NULL) with a change each.
        task_services.forget_person(user)
        user.delete()


# ---------- security alerts ----------

DEVICE_COOKIE = "housecairn_device"
DEVICE_COOKIE_MAX_AGE = 400 * 24 * 3600
# Devices kept per account: the oldest are forgotten beyond this.
MAX_KNOWN_DEVICES = 20


def _digest(secret: str) -> str:
    import hashlib

    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def note_sign_in(request: Any, user: User) -> str | None:
    """Record a sign-in; email an alert if the browser is new to an account that had others.

    Returns the secret to set in the browser's cookie when the device is new,
    else None. The first device of an account raises no alert: a new account is
    not worth one. Without outgoing email nothing is recorded.
    """
    import logging
    import secrets

    from allauth.account.adapter import get_adapter
    from django.utils import timezone

    from housecairn.accounts.models import KnownDevice

    if not email_is_configured():
        return None
    cookie = request.COOKIES.get(DEVICE_COOKIE, "")
    if cookie:
        known = KnownDevice.objects.filter(user=user, digest=_digest(cookie)).first()
        if known is not None:
            KnownDevice.objects.filter(pk=known.pk).update(last_seen_at=timezone.now())
            return None
    had_devices = KnownDevice.objects.filter(user=user).exists()
    secret = secrets.token_urlsafe(32)
    KnownDevice.objects.create(user=user, digest=_digest(secret))
    stale = KnownDevice.objects.filter(user=user).order_by("-last_seen_at")[MAX_KNOWN_DEVICES:]
    KnownDevice.objects.filter(pk__in=[d.pk for d in stale]).delete()
    if had_devices:
        try:
            get_adapter(request).send_notification_mail(
                "account/email/new_login", user, email=user.email
            )
        except Exception as exc:
            # A mail server that is down must not stop anyone from signing in.
            logging.getLogger("housecairn.mail").warning(
                "sign-in alert not sent: %s", type(exc).__name__
            )
    return secret


def clean_display_name(value: str) -> str:
    """`value` as a display name, or `ValidationError` when it cannot be one.

    One line, no control character, 60 characters at most, and not misleading: no "@" (it
    would pass for an address) and something that draws (a name of fillers is no name).
    Fillers at either end are removed. Empty is allowed: the person is then named as before
    they chose. The rule of every way in, the name a sign-in provider gives included.
    """
    cleaned = " ".join(without_control_characters(" ".join(str(value).split())).split())
    if names.looks_like_an_address(cleaned):
        raise ValidationError(_("A display name cannot contain “@”: it would pass for an address."))
    if cleaned and not names.has_visible_glyph(cleaned):
        raise ValidationError(_("Give a name with at least one visible character."))
    # A filler at either end draws a gap: "Sam" and "Sam\u3164" would look like two people.
    cleaned = names.trim_fillers(cleaned)
    probe = User(display_name=cleaned)
    probe.full_clean(exclude=[f.name for f in User._meta.fields if f.name != "display_name"])
    return cleaned


def set_display_name(user: User, value: str) -> User:
    """Change how the person is named, by their own hand (`clean_display_name`).

    A name the person changes is theirs: from then on, no sign-in provider overwrites it
    (docs/adr/0045). Saving the same name again changes nothing, the source included.
    """
    cleaned = clean_display_name(value)
    fields = ["display_name", "updated_at"]
    if cleaned != user.display_name:
        user.display_name_source = DisplayNameSource.MANUAL
        user.display_name_provider = ""
        fields += ["display_name_source", "display_name_provider"]
    user.display_name = cleaned
    user.save(update_fields=fields)
    return user
