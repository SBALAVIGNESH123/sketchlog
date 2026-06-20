# Governance Policy

This document outlines the lightweight governance structure for the Sketchlog project. It defines roles, responsibilities, and decision-making processes to ensure the project remains sustainable and secure.

## Roles and Responsibilities

### Contributors
Anyone who interacts with the project is a contributor. This includes opening issues, submitting pull requests, participating in discussions, or improving documentation.

### Core Maintainers
Maintainers are community members who have demonstrated sustained commitment to the project. They hold push/merge access to the repository.

**Responsibilities:**
*   Reviewing and merging pull requests.
*   Triage and management of issues.
*   Enforcing the Code of Conduct.
*   Guiding the architectural direction of the project.

**Becoming a Maintainer:**
Maintainer status is granted by consensus of the existing maintainers. Candidates are typically active contributors who have submitted significant code, performed reviews, or managed community interactions over several months.

### Project Owner (@SBALAVIGNESH123)
The original creator and lead maintainer of the project. The Owner has administrative access to the repository, package registries (PyPI), and workflow configurations.

**Responsibilities:**
*   Final say on architectural decisions and the RFC process.
*   Managing GitHub branch protection and repository settings.
*   Publishing official releases to package managers.

## Release Ownership

*   **Standard Releases**: Releases are cut via GitHub Actions. The Project Owner initiates a release by pushing an annotated tag (e.g., `v1.2.0`). This triggers the CD pipeline to build wheels and push to PyPI.
*   **Emergency Patches**: In the event of a critical security vulnerability or zero-day bug, any maintainer can author a hotfix. The Project Owner will expedite the review, merge the PR directly (bypassing normal wait times if necessary), and immediately cut a patch release.

## Contributor Permission Boundaries

*   **`main` Branch**: Direct pushes to `main` are strictly prohibited. All changes, including those from the Project Owner, must go through a reviewed Pull Request.
*   **Code Reviews**: A PR requires at least one approval from a designated `CODEOWNER` for the affected area before merging.
*   **Force Pushes**: Force pushing to `main` is blocked.
*   **CI Checks**: All PRs must pass the required GitHub Actions CI checks (tests, type checking, hygiene) before they can be merged.

## Decision Making

Routine bug fixes and small features are managed through standard Pull Requests. However, significant architectural changes, breaking API modifications, or major new features must go through the [RFC Process](RFC_PROCESS.md).
