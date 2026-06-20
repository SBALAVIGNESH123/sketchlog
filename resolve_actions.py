import subprocess
import sys

actions = [
    "actions/checkout@v4.2.2",
    "actions/setup-python@v5.6.0",
    "pypa/cibuildwheel@v4.1.0",
    "actions/upload-artifact@v4.6.0",
    "actions/download-artifact@v4.1.8",
    "pypa/gh-action-pypi-publish@release/v1",
    "softprops/action-gh-release@v2.2.1"
]

for a in actions:
    repo, ref = a.split('@')
    url = f"https://github.com/{repo}"

    # Use ls-remote and get both the tag and its peeled commit (^{})
    out = subprocess.check_output(['git', 'ls-remote', url, ref, f'refs/tags/{ref}', f'refs/tags/{ref}^{{}}']).decode()

    resolved_sha = None

    # Try to find peeled tag first
    for line in out.splitlines():
        if line.endswith('^{}'):
            resolved_sha = line.split()[0]
            break

    # Fallback to direct ref (for branches like release/v1 or unannotated tags)
    if not resolved_sha:
        for line in out.splitlines():
            if ref in line:
                resolved_sha = line.split()[0]
                break

    if resolved_sha:
        print(f"{repo}@{resolved_sha} # {ref}")
    else:
        print(f"Error: Could not resolve {a}", file=sys.stderr)
        sys.exit(1)
