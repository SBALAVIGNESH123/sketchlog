# RFC / ADR Process

Significant changes to Sketchlog—such as new protocols, storage format changes, core algorithm modifications, or breaking API changes—require an architectural design review. We manage this through a Request for Comments (RFC) or Architectural Decision Record (ADR) process.

## When to write an RFC

You should write an RFC if your proposal:
*   Introduces a breaking change to the public API.
*   Changes the binary serialization format or network protocol.
*   Introduces a new statistical algorithm or significantly alters an existing one (e.g., changing from Count-Min Sketch to something else).
*   Adds a major new subsystem (like the standalone server).

You do **not** need an RFC for:
*   Bug fixes.
*   Performance optimizations that do not change public APIs.
*   Adding new documentation or tests.
*   Small, non-breaking additive features.

## The RFC Lifecycle

1.  **Drafting**: Copy the `0000-template.md` (if available, or follow a standard structure) into a new markdown file in the `rfcs/` directory. Name it appropriately (e.g., `rfcs/0001-grpc-support.md`).
2.  **Pull Request**: Open a Pull Request adding your RFC. This serves as the forum for discussion.
3.  **Review**: The community and maintainers will review the proposal. Expect to iterate on the design based on feedback.
4.  **Resolution**: The RFC will eventually be either:
    *   **Accepted**: The PR is merged into the `rfcs/` directory, and implementation can begin.
    *   **Rejected**: The PR is closed with a summary of why the proposal was not accepted.
    *   **Deferred**: The proposal has merit but is not a priority right now. The PR may be closed or kept open as a draft.

## RFC Document Structure

A good RFC should include:
*   **Title**: A clear, descriptive title.
*   **Summary**: A one-paragraph explanation of the feature.
*   **Motivation**: Why are we doing this? What problem does it solve?
*   **Proposed Design**: The technical details of how the feature will work. Include APIs, data structures, and algorithms.
*   **Alternatives Considered**: What other approaches did you evaluate, and why were they rejected?
*   **Compatibility**: How does this impact existing users? Does it require a migration?
*   **Security Implications**: Does this introduce any new attack vectors?
