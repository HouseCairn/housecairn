"""Writing and reading the audit log (docs/adr/0023)."""

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db import models

from housecairn.accounts import names
from housecairn.accounts.models import User
from housecairn.audit.models import AuditEvent, Scope
from housecairn.core.text import without_control_characters

# Keys `detail` may carry. A value is short and says nothing of a home's content.
ALLOWED_DETAIL = frozenset(
    {
        "role",
        "previous_role",
        "provider",
        "count",
        "by",
        "fields",
        "format",
        "setting",
        "old",
        "new",
        "title",
        "kind",
        "domain",
    }
)
TITLE_MAX_LENGTH = 120


def record(
    action: str,
    *,
    actor: User | AnonymousUser | None = None,
    home_id: UUID | None = None,
    target: User | None = None,
    event_id: UUID | None = None,
    **detail: Any,
) -> AuditEvent:
    """Add an entry: of the home when `home_id` is given, else of the instance.

    `event_id`: the id of the entry, for an entry written once however often its cause is
    seen (a closing pushed by a phone, docs/adr/0077 need 7); by default a new one.
    """
    unknown = set(detail) - ALLOWED_DETAIL
    if unknown:
        raise ValueError(f"Audit detail may not carry: {', '.join(sorted(unknown))}")
    return AuditEvent.objects.create(
        **({} if event_id is None else {"id": event_id}),
        scope=Scope.HOME if home_id else Scope.INSTANCE,
        action=action,
        actor=actor if isinstance(actor, User) else None,
        target_user=target,
        home_id=home_id,
        detail=detail,
    )


def record_object(
    action: str, *, actor: User | AnonymousUser | None, home_id: UUID, title: object, **detail: Any
) -> AuditEvent:
    """Note an action on something of a home, named by its title and nothing else.

    The title is the name a person gave it (a room, a task); never a note, a cost, a file or
    a procedure. `fields` may list the names of what changed, never their values.
    """
    shown = without_control_characters(" ".join(str(title).split()))[:TITLE_MAX_LENGTH]
    return record(action, actor=actor, home_id=home_id, title=shown, **detail)


def home_log(actor: User | AnonymousUser, home_id: UUID) -> models.QuerySet[AuditEvent]:
    """Entries of a home, for its owners only."""
    from housecairn.homes.models import Membership, Role

    owner = (
        actor.is_authenticated
        and Membership.objects.filter(home_id=home_id, user=actor, role=Role.OWNER).exists()
    )
    if not owner:
        raise PermissionDenied
    return AuditEvent.objects.filter(scope=Scope.HOME, home_id=home_id).select_related(
        "actor", "target_user"
    )


def instance_log(actor: User | AnonymousUser) -> models.QuerySet[AuditEvent]:
    """Entries of the instance, for its administrators only; never a home's."""
    from housecairn.accounts.services import is_instance_admin

    if not is_instance_admin(actor):
        raise PermissionDenied
    return AuditEvent.objects.filter(scope=Scope.INSTANCE).select_related("actor", "target_user")


def name_people(entries: Iterable[AuditEvent]) -> list[AuditEvent]:
    """Give each entry the names of its actor and target, told apart across the list.

    Two people who look the same on screen (same name, whatever the case or the filler
    characters) show their address in brackets, as in the other lists (docs/adr/0045).
    The names are `actor_label` and `target_label`, empty for nobody.
    """
    found = list(entries)
    people = {
        user.pk: user
        for entry in found
        for user in (entry.actor, entry.target_user)
        if user is not None
    }
    shown = names.labels(people.values())
    for entry in found:
        entry.actor_label = shown[entry.actor.pk] if entry.actor else ""  # type: ignore[attr-defined]
        entry.target_label = shown[entry.target_user.pk] if entry.target_user else ""  # type: ignore[attr-defined]
    return found
