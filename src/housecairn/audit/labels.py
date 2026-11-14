"""What each audit action says, in the language of the person reading the log."""

from typing import Any

from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _

LABELS: dict[str, Promise] = {
    # Home scope.
    "home.created": _("Home created"),
    "member.role_changed": _("Role changed to %(role)s"),
    "member.removed": _("Member removed"),
    "member.left": _("Member left"),
    "ownership.transferred": _("Ownership transferred"),
    "invitation.sent": _("Invitation sent (%(role)s)"),
    "invitation.resent": _("Invitation sent again"),
    "invitation.revoked": _("Invitation revoked"),
    "invitation.accepted": _("Invitation accepted (%(role)s)"),
    "sso.required": _("Sign-in through %(provider)s required"),
    "sso.lifted": _("Sign-in requirement removed"),
    "home.exported": _("Home exported"),
    "home.archived": _("Home archived"),
    "home.unarchived": _("Home brought back from the archive"),
    "home.restored": _("Home restored from an archive"),
    "home.copied": _("Home restored as a copy of an archive"),
    "home.imported": _("Tables imported from CSV files"),
    "room.created": _("Room added: %(title)s"),
    "room.updated": _("Room changed: %(title)s"),
    "room.deleted": _("Room deleted: %(title)s"),
    "item.created": _("Item added: %(title)s"),
    "item.updated": _("Item changed: %(title)s"),
    "item.deleted": _("Item deleted: %(title)s"),
    "contractor.created": _("Contractor added: %(title)s"),
    "contractor.updated": _("Contractor changed: %(title)s"),
    "contractor.deleted": _("Contractor deleted: %(title)s"),
    "task.created": _("Task added: %(title)s"),
    "task.updated": _("Task changed: %(title)s"),
    "task.deleted": _("Task deleted: %(title)s"),
    "photo.added": _("Photo added to %(title)s"),
    "photo.deleted": _("Photo deleted from %(title)s"),
    "attachment.added": _("File added to the task %(title)s"),
    "attachment.deleted": _("File deleted from the task %(title)s"),
    "calendar.feed_created": _("Calendar feed created"),
    "calendar.feed_regenerated": _("Calendar feed given a new address"),
    "calendar.feed_revoked": _("Calendar feed revoked"),
    "label.revoked": _("QR label revoked: %(title)s"),
    "label.regenerated": _("QR label replaced by a new one: %(title)s"),
    "report.created": _("Problem reported: %(title)s"),
    "report.converted": _("Report made into a task: %(title)s"),
    "report.closed": _("Report closed: %(title)s"),
    "project.created": _("Project added: %(title)s"),
    "project.updated": _("Project changed: %(title)s"),
    "project.deleted": _("Project deleted: %(title)s"),
    "project.task_added": _("Task put in the project %(title)s"),
    "project.task_removed": _("Task taken out of the project %(title)s"),
    "budget_line.created": _("Budget line added to the project %(title)s"),
    "budget_line.updated": _("Budget line changed in the project %(title)s"),
    "budget_line.deleted": _("Budget line deleted from the project %(title)s"),
    "quote.created": _("Quote added to the project %(title)s"),
    "quote.updated": _("Quote changed in the project %(title)s"),
    "quote.accepted": _("Quote accepted in the project %(title)s"),
    "quote.deleted": _("Quote deleted from the project %(title)s"),
    "project_file.added": _("File added to the project %(title)s"),
    "project_file.deleted": _("File deleted from the project %(title)s"),
    "project_link.added": _("Link to %(domain)s added to the project %(title)s"),
    "project_link.deleted": _("Link to %(domain)s deleted from the project %(title)s"),
    "article.created": _("Stock article added: %(title)s"),
    "article.updated": _("Stock article changed: %(title)s"),
    "article.deleted": _("Stock article deleted: %(title)s"),
    "supplier.created": _("Supplier added: %(title)s"),
    "supplier.updated": _("Supplier changed: %(title)s"),
    "supplier.deleted": _("Supplier deleted: %(title)s"),
    "envelope.set": _("Annual envelope set for %(title)s"),
    # Instance scope.
    "instance_invitation.sent": _("Instance invitation sent"),
    "instance_invitation.resent": _("Instance invitation sent again"),
    "instance_invitation.revoked": _("Instance invitation revoked"),
    "instance_invitation.accepted": _("Instance invitation accepted"),
    "apitoken.created": _("API token created: %(title)s"),
    "apitoken.revoked": _("API token revoked: %(title)s"),
    "account.created": _("Account created"),
    "account.email_verified": _("Email address verified from the server"),
    "account.email_primary": _("Primary email address changed from the server"),
    "account.deleted": _("Account deleted"),
    "instance.first_admin_created": _("First administrator created on the setup screen"),
    "home.deleted": _("A home was deleted"),
    "homes.unlocked": _("Sign-in requirement removed from %(count)s homes"),
    "settings.changed": _("Setting %(setting)s changed from %(old)s to %(new)s"),
    "settings.secret_changed": _("Secret setting %(setting)s changed"),
    "settings.reset": _("Setting %(setting)s reset to its default"),
    "provider.created": _("Sign-in provider %(provider)s added"),
    "provider.changed": _(
        "Sign-in provider %(provider)s: %(setting)s changed from %(old)s to %(new)s"
    ),
    "provider.secret_changed": _("Sign-in provider %(provider)s: client secret changed"),
    "provider.secret_cleared": _(
        "Sign-in provider %(provider)s: client secret cleared, the issuer changed"
    ),
    "provider.deleted": _("Sign-in provider %(provider)s deleted"),
    "admin.user_changed": _("Account changed in the administration site"),
    "admin.user_deleted": _("Account deleted in the administration site"),
}


def label(action: str, detail: dict[str, Any]) -> str:
    """The sentence for an entry; an unknown action is shown as its code."""
    from housecairn.homes.models import Role

    template = LABELS.get(action)
    if template is None:
        return action
    values = dict(detail)
    for key in ("role", "previous_role"):
        if key in values and values[key] in Role.values:
            values[key] = Role(values[key]).label
    try:
        return str(template) % values
    except (KeyError, TypeError):
        return str(template).split(" (")[0].split(" %(")[0]
