"""Check that no key from .env is inside a file git is about to commit.

    python builder/check_secrets.py

Reads the values from .env while running, looks for them in every file git
tracks or has staged, and prints only file names and counts. A value itself is
never printed. Exit code 1 when something was found.
"""
import os
import subprocess
import sys

from with_env import ENV_PATH, load_env

ROOT = os.path.dirname(ENV_PATH)
SECRET_FILES = (".env", "env.sh")
MIN_LENGTH = 8  # shorter values (and URLs) are not secrets worth matching


def main():
    values = load_env() if os.path.exists(ENV_PATH) else {}
    secrets = {name: value.encode() for name, value in values.items()
               if len(value) >= MIN_LENGTH and not value.startswith("http")}
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    files = [f for f in listed.decode("utf-8", "replace").split("\0") if f]

    problems = 0
    for path in files:
        if os.path.basename(path) in SECRET_FILES or os.path.basename(path).startswith(".env."):
            print("SECRET FILE IS TRACKED: " + path)
            problems += 1
            continue
        try:
            with open(os.path.join(ROOT, path), "rb") as f:
                data = f.read()
        except OSError:
            continue
        for name, value in secrets.items():
            if value in data:
                print("VALUE OF %s FOUND IN: %s" % (name, path))
                problems += 1

    print("checked %d files against %d key(s): %s" % (
        len(files), len(secrets), "CLEAN" if not problems else "%d PROBLEM(S)" % problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
