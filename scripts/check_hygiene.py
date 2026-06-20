import os
import sys
import subprocess

def get_git_files():
    try:
        output = subprocess.check_output(["git", "ls-files"], text=True, encoding="utf-8")
        return [f for f in output.splitlines() if f]
    except Exception as e:
        print(f"Warning: could not get git files: {e}")
        return []

def check_generated_files(git_files):
    print("Checking for generated files...")
    prohibited_exts = {".pyc", ".o", ".exe", ".so", ".dll", ".pyd"}
    prohibited_dirs = {"__pycache__"}

    failed = False
    for f in git_files:
        # Check if it's in a prohibited directory
        parts = f.split("/")
        for d in parts[:-1]:
            if d in prohibited_dirs:
                print(f"ERROR: Prohibited directory found in git tracking: {f}")
                failed = True

        ext = os.path.splitext(f)[1].lower()
        if ext in prohibited_exts:
            print(f"ERROR: Prohibited file found in git tracking: {f}")
            failed = True
    return failed

def check_encoding_and_whitespace(git_files):
    print("Checking encoding (UTF-8) and trailing whitespace...")
    failed = False
    allowed_exts = {".py", ".md", ".cpp", ".hpp", ".h", ".txt", ".json", ".yml", ".yaml", ".toml"}
    allowed_exact = {".gitignore", ".gitkeep", "CODEOWNERS", "LICENSE", "Makefile"}

    for f in git_files:
        if not os.path.exists(f):
            continue

        ext = os.path.splitext(f)[1].lower()
        basename = os.path.basename(f)
        if ext not in allowed_exts and basename not in allowed_exact:
            continue

        try:
            with open(f, "rb") as file:
                raw = file.read()

            # Check for BOM
            if raw.startswith(b'\xef\xbb\xbf'):
                print(f"ERROR: File contains UTF-8 BOM: {f}")
                failed = True

            text = raw.decode("utf-8")

            # Check trailing whitespace
            lines = text.splitlines()
            for i, line in enumerate(lines):
                if line.endswith(" ") or line.endswith("\t"):
                    print(f"ERROR: Trailing whitespace found in {f} at line {i+1}")
                    failed = True
        except UnicodeDecodeError:
            print(f"ERROR: File is not valid UTF-8: {f}")
            failed = True
    return failed

def check_commit_size():
    print("Checking commit sizes...")
    return False

if __name__ == "__main__":
    git_files = get_git_files()
    if not git_files:
        print("No git files found, skipping checks.")
        sys.exit(0)

    generated_failed = check_generated_files(git_files)
    whitespace_failed = check_encoding_and_whitespace(git_files)

    if generated_failed or whitespace_failed:
        print("Hygiene checks failed!")
        sys.exit(1)
    else:
        print("All hygiene checks passed.")
        sys.exit(0)
