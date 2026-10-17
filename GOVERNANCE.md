# Governance

HomelabScribe is a small open-core project run by one maintainer. This document says who decides what, who has access, and what happens if the maintainer is unavailable.

## Roles

| Role | Who | Responsibilities |
| --- | --- | --- |
| Maintainer / owner | [@HoneyBearTech](https://github.com/HoneyBearTech) | Final say on scope and releases; merges pull requests; handles security reports; holds release and signing access |
| Contributor | Anyone sending issues or PRs | Follows [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md); signs off commits (DCO) |

Additional maintainers may be added after a sustained record of good contributions; the list of people with sensitive access (admin on the repository, ability to publish releases, PyPI and GHCR publishing) is the owner above plus anyone added here and in [.github/CODEOWNERS](.github/CODEOWNERS).

## Decisions

Day-to-day decisions are made in issues and pull requests. The maintainer decides when there is disagreement, explains the reasoning in the thread and records significant decisions in the changelog or docs. The [roadmap](docs/roadmap.md) states what the project will and will not do.

## Access and continuity

This is a single-maintainer project. The maintainer has a documented continuity plan (kept privately by the owner because it covers account recovery and access) for keeping the project going or winding it down in an orderly way if they become unavailable. In addition:

- Source, issues and releases are public on GitHub; AGPL-3.0 lets anyone fork the core.
- Releases are built by GitHub Actions from tags, signed with keyless Sigstore signatures and verifiable by anyone ([verifying releases](docs/verifying-releases.md)); no secret signing key is needed to verify the core.
- The GitHub account uses two-factor authentication; account recovery for the GitHub, PyPI and registry accounts is held by the owner outside the repository, as part of the continuity plan.
- If the project is abandoned, the owner will mark the repository archived with a notice in the README. Anyone is free to fork the AGPL core.

## Changing this document

By pull request, approved by the maintainer.
