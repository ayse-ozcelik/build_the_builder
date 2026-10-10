"""Run a command with the keys from .env loaded into its environment.

    python builder/with_env.py python tools/run_cases.py <agent_id> --brief A

Reads .env (project root) only while running, hands the values to the command
it starts, and never prints a value: only variable NAMES appear in messages.
The command runs in the current folder, so start it from where the command
expects to be (for kit commands: from inside kit/).
"""
import os
import subprocess
import sys

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
REQUIRED = ("FREYA_API_KEY", "FREYA_BANK_TEAM_KEY")


def load_env(path=ENV_PATH):
    """Return {name: value} from a file of NAME=value lines."""
    values = {}
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            name, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[name.strip()] = value
    return values


def check():
    """Print which names .env sets. Names and counts only, never a value."""
    values = load_env()
    for name in sorted(set(values) | set(REQUIRED)):
        state = "set" if values.get(name) else ("EMPTY" if name in values else "MISSING")
        print("%-24s %s" % (name, state))
    with open(ENV_PATH, encoding="utf-8-sig") as f:
        lines = [line.strip() for line in f]
    odd = sum(1 for line in lines if line and not line.startswith("#") and "=" not in line)
    if odd:
        print("%d line(s) have no '=' and were skipped (a value on a line of its own?)" % odd)


def main(argv):
    if not argv:
        sys.exit(__doc__)
    if not os.path.exists(ENV_PATH):
        sys.exit("no .env file at " + ENV_PATH)
    if argv == ["--check"]:
        return check()
    values = load_env()
    missing = [name for name in REQUIRED if not values.get(name)]
    if missing:
        sys.exit(".env has no value for: " + ", ".join(missing))
    env = dict(os.environ)
    env.update({name: value for name, value in values.items() if value})
    env.setdefault("PYTHONUTF8", "1")  # Windows: read the kit's files as UTF-8
    return subprocess.run(argv, env=env).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
