import os
import sys
import json
import argparse

# Ensure sketchlog is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "python")))

import sketchlog

def main():
    parser = argparse.ArgumentParser(description="Check public API compatibility")
    parser.add_argument("--update", action="store_true", help="Update the baseline fixture with the current API")
    args = parser.parse_args()

    fixture_path = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "public_api_baseline.json")
    current_api = sorted(list(set(getattr(sketchlog, "__all__", []))))

    if args.update:
        with open(fixture_path, "w", encoding="utf-8") as f:
            json.dump(current_api, f, indent=2)
        print(f"Updated baseline API at {fixture_path}")
        return

    try:
        with open(fixture_path, "r", encoding="utf-8") as f:
            baseline_api = set(json.load(f))
    except FileNotFoundError:
        print(f"ERROR: Baseline file {fixture_path} not found. Run with --update to create it.")
        sys.exit(1)

    current_api_set = set(current_api)
    removed = baseline_api - current_api_set

    if removed:
        print(f"ERROR: Public API compatibility broken! Removed symbols: {removed}")
        print("To remove public symbols, you must bump the MAJOR version according to our compatibility guarantees.")
        sys.exit(1)

    print("API compatibility check passed. All baseline symbols are present.")

if __name__ == "__main__":
    main()
