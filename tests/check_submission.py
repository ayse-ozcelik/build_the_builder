"""Is an exported workflow (what gets submitted) the agent the CURRENT code builds from the same rules file?

    python tests\\check_submission.py                       fast-brief-a and fast-brief-b
    python tests\\check_submission.py <export.json> <rules.json>

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


def main(argv):
    pairs = [tuple(argv)] if len(argv) == 2 else PAIRS
    results = [check(export, rules) for export, rules in pairs]
    print("\n" + ("ALL EXPORTS MATCH THE CURRENT CODE" if all(results) else "AN EXPORT DIFFERS FROM THE CURRENT CODE"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
