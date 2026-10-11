"""Check a workflow file and save it to the ONE workflow this project uses on Freya.

    python builder/save_workflow.py <workflow.json>

Always updates the workflow in slot "bank-collections" (see kit/.freya-agents.json)
and publishes it. It never creates a second workflow. Prints {"workflow_id", "agent_id"}.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.path.join(ROOT, "kit")
SLOT = "bank-collections"
STATE_FILE = os.path.join(KIT, ".freya-agents.json")


def problems_in(workflow):
    """Things the platform rejects or drops without saying so."""
    found = []

    def walk(value, where):
        if value is None:
            found.append("null value at " + where)
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(item, where + "." + str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, "%s[%d]" % (where, index))

    walk(workflow, "workflow")
    if "agent_config" in workflow:
        found.append("agent_config must be left out")
    nodes = workflow.get("nodes") or {}
    if workflow.get("initial_node") not in nodes:
        found.append("initial_node is not a node")
    for name, node in nodes.items():
        kind = node.get("node_type")
        if kind != "conversation" and node.get("tools"):
            found.append("%s: tools only work on conversation nodes" % name)
        if kind == "conversation" and node.get("pre_actions"):
            found.append("%s: pre_actions do not work on conversation nodes" % name)
        transitions = node.get("transitions") or []
        if node.get("passthrough") and (not transitions or transitions[-1].get("condition", {}).get("operator") != "always"):
            found.append("%s: a passthrough node must end with an always transition" % name)
        for transition in transitions:
            target = transition.get("target_node")
            if target not in nodes and target != "__previous__":
                found.append("%s: transition to unknown node %s" % (name, target))
        for action in node.get("pre_actions") or []:
            code = ((action.get("function", {}).get("parameters", {}).get("properties", {}).get("code") or {}).get("default")) or ""
            if "{{" in code or "}}" in code:
                found.append("%s: code contains double braces" % name)
    return found


def save(workflow):
    """Check, validate, save and publish. Returns {"workflow_id", "agent_id", "version"}.

    Raises ValueError for a problem in the workflow, RuntimeError when the platform refuses it.
    """
    found = problems_in(workflow)
    if found:
        raise ValueError("NOT SAVED, fix these first:\n- " + "\n- ".join(found))
    with open(STATE_FILE, encoding="utf-8") as f:
        if SLOT not in json.load(f):
            raise ValueError("NOT SAVED: slot %s is missing from %s; refusing to create a new workflow" % (SLOT, STATE_FILE))
    if KIT not in sys.path:
        sys.path.insert(0, KIT)
    import freya
    try:
        freya.validate_workflow(SLOT, workflow)
    except (RuntimeError, OSError) as error:
        # The dry run is only an early check. When the platform itself fails on it (504 and the like, seen on
        # 11 October), go on to the real save, which checks the workflow again. A refusal (4xx) still stops here.
        if isinstance(error, RuntimeError) and not any("-> %d" % code in str(error) for code in (500, 502, 503, 504, 520, 522, 524)):
            raise
        print("platform: the dry run gave no answer (%s); saving without it" % str(error)[-40:].strip(), flush=True)
    saved = freya.save_workflow(SLOT, workflow, state_file=STATE_FILE)
    return {"workflow_id": saved["workflow_id"], "agent_id": saved["agent_id"], "version": saved.get("version")}


def main(argv):
    if len(argv) != 1:
        sys.exit(__doc__)
    with open(argv[0], encoding="utf-8") as f:
        workflow = json.load(f)
    try:
        print(json.dumps(save(workflow)))
    except ValueError as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main(sys.argv[1:])
