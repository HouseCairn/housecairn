# 0075. Every pull request says its Home Assistant impact

- Status: accepted
- Date: 2026-11-14

## Context

Two other repositories of the project follow this one:

- the Home Assistant app, [HouseCairn/home-assistant-addons](https://github.com/HouseCairn/home-assistant-addons):
  a layer over the image published here, whose start script turns the app's options into
  HouseCairn's environment variables and sets those of Home Assistant's Ingress;
- the Home Assistant integration, [HouseCairn/home-assistant-housecairn](https://github.com/HouseCairn/home-assistant-housecairn):
  a client of the API.

The app now receives a pull request for each release (its decision D13), and runs a start
test on the new image (D14). Neither tells whether a change here needs a change there: a new
setting that the app should offer as an option, a behaviour that differs behind Ingress, an
API operation that the integration calls. Nothing asked the author of a pull request, who is
the one who knows.

## Decision

The pull request template has a section **Home Assistant impact** with three boxes:

- **None**;
- **The app must change** (an option, a variable, a behaviour behind Ingress);
- **The integration must change** (the API it calls).

The `Home Assistant impact` check (`.github/workflows/ha-impact.yml`,
`scripts/check_ha_impact.py`) reads the description of the pull request, on the model of
`Documentation updated`, and fails when the section is missing, when no box is ticked, when
**None** is ticked with a change, or when a change is ticked without a link, in the section,
to an issue or a pull request of the repository concerned
(`https://github.com/HouseCairn/<repository>/issues/<n>` or `/pull/<n>`, or
`HouseCairn/<repository>#<n>`). HTML comments are ignored, so the example link of the
template does not count. The check runs again when the description is edited, and on the
merge queue with the description read from the API at that moment
(`scripts/merge_queue.py`, ADR 0040). Pull requests opened by Dependabot are exempt: they
use no template.

The description reaches the script through the environment only, never through the
workflow's expressions, with a read-only token.

## Consequences

- An author has to think about Home Assistant before asking for a review, and the reviewer
  of the app's update pull request finds, in the release notes, the pull requests that
  announced a change and their links.
- The issue or pull request in the other repository exists before the change here is
  merged, so the work there is not forgotten.
- The check reads what the author declares; it cannot tell whether "None" is true. The
  review does.
- The check must be added to the required checks of `main` by the maintainer; until then it
  only reports.
