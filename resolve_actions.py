import subprocess

actions = [
    "actions/checkout@v4.2.2",
    "actions/setup-python@v5.6.0",
    "pypa/cibuildwheel@v2.16.5",
    "actions/upload-artifact@v4.6.0",
    "actions/download-artifact@v4.1.8",
    "pypa/gh-action-pypi-publish@release/v1",
    "softprops/action-gh-release@v2.2.1"
]

for a in actions:
    repo, ref = a.split('@')
    url = f"https://github.com/{repo}"
    out = subprocess.check_output(['git', 'ls-remote', url, ref, f'refs/tags/{ref}']).decode()
    for line in out.splitlines():
        if 'refs/tags/' in line:
            sha = line.split()[0]
            print(f"{repo}@{sha} # {ref}")
            break
        elif ref == 'release/v1':
            sha = line.split()[0]
            print(f"{repo}@{sha} # {ref}")
            break
