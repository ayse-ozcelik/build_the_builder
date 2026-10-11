"""One-off check ON THE PLATFORM that a promise stopped by the hook is really not sent to the bank.

    python builder/with_env.py python experiments/guard_probe.py

Takes the compiled Brief B workflow and changes ONE thing: the date check answers "not possible" for
every date. Saves it to the one workflow slot. Then run one practice customer and look at the bank's
record: the promise must appear as account lookups (the stopped calls) and, after three of them, as
one create_promise (the check lets a call through after three stops).

This is an experiment, not part of the builder. Rebuild with fastbuild.py afterwards.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.path.join(ROOT, "kit")
sys.path.insert(0, KIT)
sys.path.insert(0, os.path.join(ROOT, "builder"))

MARK = "_date not in _dates"


def main():
    import save_workflow
    with open(os.path.join(ROOT, "build", "fast-brief-b", "workflow.json"), encoding="utf-8") as f:
        workflow = json.load(f)
    changed = 0
    for node in workflow["nodes"].values():
        for tool in node.get("tools") or []:
            code = tool.get("pre_process_code") or ""
            if tool["function"]["name"] == "create_promise" and MARK in code:
                tool["pre_process_code"] = code.replace(MARK, "True")
                changed += 1
    if not changed:
        sys.exit("the compiled workflow has no date check to change")
    print("date check forced to fail in %d tool(s)" % changed)
    print(save_workflow.save(workflow))


if __name__ == "__main__":
    main()
