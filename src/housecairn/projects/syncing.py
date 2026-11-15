"""What of the projects the restore records in the change log (docs/adr/0077, 0079)."""

from housecairn.projects.changelog import NOT_CARRIED
from housecairn.projects.models import ProjectLink
from housecairn.sync import rows


def register() -> None:
    # Where the reading of a link's page stands is not data of the home (docs/adr/0070).
    rows.register(ProjectLink, rows.Spec(skip=NOT_CARRIED["projects.projectlink"]))
