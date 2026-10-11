"""Is an exported workflow (what gets submitted) the agent the CURRENT code builds from the same rules file?

    python tests\\check_submission.py                       fast-brief-a and fast-brief-b
    python tests\\check_submission.py <export.json> <rules.json>
    python tests\\check_submission.py --final [submission.json]   the final case: fields, files, commit, tools on the mock

Compiles the workflow again from the rules file (no model, no platform) and looks for every value of it
in the export. The platform adds fields of its own (agent_config, defaults); those are ignored. A value
of ours that is missing or different in the export is reported.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "builder"))
import compile as compile_step
import prompts as prompts_step
import tools_gen

PAIRS = [("submission/fast-brief-a.workflow.json", "build/fast-brief-a/rules.json"),
         ("submission/fast-brief-b.workflow.json", "build/fast-brief-b/rules.json")]


def differences(ours, theirs, where, out):
    """Every place where a value we built is not in the export as we built it."""
    if isinstance(ours, dict):
        if not isinstance(theirs, dict):
            out.append("%s: the export has %s where we built an object" % (where, type(theirs).__name__))
            return
        for key, value in ours.items():
            if key not in theirs:
                out.append("%s.%s: missing in the export" % (where, key))
            else:
                differences(value, theirs[key], where + "." + str(key), out)
    elif isinstance(ours, list):
        if not isinstance(theirs, list) or len(ours) != len(theirs):
            out.append("%s: %d entries built, %s in the export" % (where, len(ours), len(theirs) if isinstance(theirs, list) else "no list"))
            return
        for index, value in enumerate(ours):
            name = value.get("function", {}).get("name") or value.get("target_node") if isinstance(value, dict) else None
            differences(value, theirs[index], "%s[%s]" % (where, name or index), out)
    elif isinstance(ours, (int, float)) and not isinstance(ours, bool) and isinstance(theirs, (int, float)):
        if float(ours) != float(theirs):
            out.append("%s: built %r, export has %r" % (where, ours, theirs))
    elif ours != theirs:
        text = lambda v: (str(v)[:60] + "...") if len(str(v)) > 60 else str(v)
        out.append("%s: built %r, export has %r" % (where, text(ours), text(theirs)))


def check(export_path, rules_path):
    with open(os.path.join(ROOT, rules_path), encoding="utf-8") as f:
        rules = json.load(f)
    with open(os.path.join(ROOT, export_path), encoding="utf-8") as f:
        export = json.load(f)
    tools = tools_gen.generate_tools(tools_gen.load_spec(os.path.join(ROOT, "kit", "tools", "openapi.json")),
                                     context_vars=tuple(rules["call_variables"]))
    built, _ = compile_step.compile_workflow(rules, prompts_step.verbatim_prompts(rules), tools)
    found = []
    for key in ("workflow_id", "agent_id", "workflow"):
        if not export.get(key):
            found.append("the export has no %s" % key)
    theirs = export.get("workflow") or {}
    differences(built, theirs, "workflow", found)
    extra = sorted(set(theirs.get("nodes") or {}) - set(built["nodes"]))
    if extra:
        found.append("the export has steps the code does not build: " + ", ".join(extra))
    print("%s  %s  (exported %s, %d steps, %d tool definitions)" % (
        "OK  " if not found else "DIFF", export_path, export.get("updated_at"), len(theirs.get("nodes") or {}),
        sum(len(n.get("tools") or []) for n in (theirs.get("nodes") or {}).values())))
    for line in found[:25]:
        print("      - " + line)
    if len(found) > 25:
        print("      ... and %d more" % (len(found) - 25))
    return not found


FINAL_KEYS = ("team", "members", "repo", "commit", "run_command", "workflow", "trace", "mock_url")


def tool_urls(value, found):
    """Every address a tool of the workflow calls."""
    if isinstance(value, dict):
        url = value.get("url")
        if isinstance(url, dict) and isinstance(url.get("default"), str) and url["default"].startswith("http"):
            found.append(url["default"])
        for item in value.values():
            tool_urls(item, found)
    elif isinstance(value, list):
        for item in value:
            tool_urls(item, found)
    return found


def check_final(path):
    """Is a submission.json complete: every field filled, its files there and committed, the tools on the mock?"""
    import subprocess
    import urllib.request
    found, say = [], lambda line: found.append(line)
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        sub = json.load(f)
    for key in FINAL_KEYS:
        value = sub.get(key)
        if not value or "TODO" in json.dumps(value) or "your-" in json.dumps(value) or "example.com" in json.dumps(value):
            say("%s is not filled in" % key)
    for member in sub.get("members") or []:
        if not member.get("name") or "@" not in str(member.get("email")):
            say("a member needs a name and an e-mail address")
    git = lambda *args: subprocess.run(("git",) + args, cwd=ROOT, capture_output=True, text=True)
    commit = str(sub.get("commit") or "")
    if git("cat-file", "-e", commit + "^{commit}").returncode != 0:
        say("commit %r is not a commit of this repository" % commit)
    for key in ("workflow", "trace"):
        name = str(sub.get(key) or "")
        if not os.path.isfile(os.path.join(ROOT, name)):
            say("%s: file %s does not exist" % (key, name))
        elif git("cat-file", "-e", "%s:%s" % (commit, name)).returncode != 0:
            say("%s: %s is not in commit %s" % (key, name, commit[:8]))
        elif git("diff", "--quiet", commit, "--", name).returncode != 0:
            say("%s: %s differs from what commit %s holds" % (key, name, commit[:8]))
    mock = str(sub.get("mock_url") or "").rstrip("/")
    urls = []
    try:
        with open(os.path.join(ROOT, str(sub.get("workflow"))), encoding="utf-8") as f:
            export = json.load(f)
        workflow = export.get("workflow") or export
        if not workflow.get("nodes") or workflow.get("initial_node") not in workflow["nodes"]:
            say("the workflow file holds no workflow with a first step")
        urls = tool_urls(workflow, [])
        if not urls:
            say("no tool of the workflow calls an address")
        for url in sorted({u for u in urls if not u.startswith(mock + "/")}):
            say("a tool calls %s, which is not on the mock" % url)
    except (OSError, ValueError) as error:
        say("the workflow file cannot be read: %s" % error)
    try:
        with urllib.request.urlopen(urllib.request.Request(mock + "/health", headers={"User-Agent": "Mozilla/5.0"}), timeout=20) as answer:
            if answer.status != 200:
                say("the mock answers /health with HTTP %s" % answer.status)
    except (OSError, ValueError) as error:
        say("the mock does not answer at %s/health: %s" % (mock, error))
    if git("status", "--porcelain", "--untracked-files=no").stdout.strip():
        say("there are uncommitted changes in tracked files")
    if not git("remote").stdout.strip():
        say("this repository has no remote: the repo address cannot be checked from here")
    print("%s  %s  (%d tool addresses, mock %s)" % ("OK  " if not found else "OPEN", path, len(urls), mock))
    for line in found:
        print("      - " + line)
    print("\n" + ("THE SUBMISSION IS COMPLETE" if not found else "THE SUBMISSION IS NOT COMPLETE"))
    return 0 if not found else 1


def main(argv):
    if argv[:1] == ["--final"]:
        return check_final(argv[1] if len(argv) > 1 else "submission.json")
    pairs = [tuple(argv)] if len(argv) == 2 else PAIRS
    results = [check(export, rules) for export, rules in pairs]
    print("\n" + ("ALL EXPORTS MATCH THE CURRENT CODE" if all(results) else "AN EXPORT DIFFERS FROM THE CURRENT CODE"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
