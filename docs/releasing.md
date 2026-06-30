# Release process

SketchLog versions its Python package, npm packages, Go module, container,
Helm chart, API contract, and website together. Release tags are immutable.
Never delete, move, or reuse a published tag or registry version.

## Registry prerequisites

- PyPI has a trusted publisher for repository `SBALAVIGNESH123/sketchlog`,
  workflow `release.yml`, and environment `pypi`.
- The npm `sketchlog` organization exists. Repository secret `NPM_TOKEN` is a
  granular token with read/write access to the `@sketchlog` scope and **Bypass
  2FA** enabled for CI publishing.
- GitHub Actions has package write access to GHCR.

Rotate a registry credential immediately if it is printed, pasted into a
ticket, or otherwise exposed.

## Prepare and validate

1. Update every coupled version and the changelog.
2. Run `python scripts/check_versions.py --tag vX.Y.Z`.
3. Merge through the protected `main` branch.
4. Wait for the exact merge commit's `CI` push workflow to pass.
5. Confirm that neither `vX.Y.Z` nor `clients/go/vX.Y.Z` already exists.

## Create coupled tags

Create the lightweight Go module tag first. The release preflight verifies it
before any registry publication. Then create the annotated root release tag:

```bash
git fetch origin main --tags
release_sha="$(git rev-parse origin/main)"
git tag "clients/go/vX.Y.Z" "$release_sha"
git push origin "refs/tags/clients/go/vX.Y.Z"
git tag -a "vX.Y.Z" "$release_sha" -m "SketchLog vX.Y.Z"
git push origin "refs/tags/vX.Y.Z"
```

Do not tag a feature branch or a commit without a successful `main` push run.

## Publication gates

The `Build and Publish` workflow must pass all of these before it creates the
GitHub release:

- tag/version and exact-`main` CI preflight;
- npm identity/scope and coupled Go tag preflight;
- Python wheels for every supported interpreter and platform plus the sdist;
- PyPI, npm, GHCR image, and OCI Helm publication;
- container vulnerability scan, SBOM/provenance generation, and signatures;
- clean-environment smoke tests against every public registry.

Verify the resulting GitHub release links to the exact commit and includes the
wheel, sdist, checksum, SBOM, and provenance artifacts.

## Failed or partial publication

Registry versions are immutable. If any registry accepted a version before a
later job failed, fix the cause, increment the patch version, and run the full
procedure again. Never overwrite an accepted package, chart, image tag, or
release tag.
