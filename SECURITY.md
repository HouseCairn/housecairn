# Security policy

## Supported versions

HouseCairn is in beta (0.x). Only the latest release receives security fixes,
in a new patch version; the `main` branch and the `edge` image are for testing
only.

| Version | Supported |
| ------- | --------- |
| 0.4.x   | Yes       |
| 0.3.x   | No        |
| 0.2.x   | No        |
| 0.1.x   | No        |

## Reporting a vulnerability

Please report vulnerabilities **only** through GitHub Private Vulnerability
Reporting, with the private form at
<https://github.com/HouseCairn/housecairn/security/advisories/new> (or: the
repository's **Security** tab, then **Report a vulnerability**). You need a
GitHub account; the report is visible only to you and the maintainer.

Do **not** open a public issue, pull request or discussion about a
vulnerability.

You will receive an acknowledgement within 7 days. We will then work with you
on an assessment and a fix, and agree on a disclosure date. If you wish, you
will be credited in the published advisory.

The documentation describes how to report a vulnerability and how to verify
the signature of the container images:
<https://docs.housecairn.app/security/>.

## How the code is protected

These safeguards are described in
[ADR 0014](docs/adr/0014-continuous-integration-and-supply-chain.md):

- every pull request passes automatic checks before the maintainer reads it:
  tests, type checks, Django's deployment checks, and a review of new
  dependencies, which refuses known vulnerabilities of moderate severity or
  more;
- property-based tests generate unexpected input for the code that reads
  what others write (request headers and bodies, identity provider claims,
  pull request titles, commit messages, Git refs), as described in
  [ADR 0019](docs/adr/0019-property-based-tests.md);
- code scanning: CodeQL for the Python code, zizmor for the GitHub Actions
  workflows, and OpenSSF Scorecard for the repository's practices;
- GitHub Actions are pinned to full commit SHAs, workflow tokens are read-only
  unless a job needs more, and no workflow runs the code of a pull request with
  write access;
- Dependabot proposes dependency updates weekly, only once a release has been
  public for 7 days; security updates are not delayed. The base image of the
  container is the exception: it is checked daily, without that delay, and the
  published image is scanned every day for fixable vulnerabilities.
