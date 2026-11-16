"""What of the projects the sync API sends (docs/adr/0077, 0079)."""

from typing import Any

from housecairn.projects.changelog import NOT_CARRIED
from housecairn.projects.models import ProjectFile, ProjectLink
from housecairn.sync import files, rows


def project_file(stored: ProjectFile) -> dict[str, Any]:
    """The file reference of a file of a project, as for an attachment (ADR 0077, need 10)."""
    return {"file": files.reference_of(stored, files.HOLDERS["projects.projectfile"])}


def register() -> None:
    # Where the reading of a link's page stands is not data of the home (docs/adr/0070).
    rows.register(ProjectLink, rows.Spec(skip=NOT_CARRIED["projects.projectlink"]))
    rows.register(
        ProjectFile,
        rows.Spec(
            skip=frozenset({"content_type", "size_bytes", "width", "height"}),
            virtual=project_file,
        ),
    )
