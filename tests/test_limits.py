"""Checks for builder/limits.py. Run from the project root:

    python tests/test_limits.py

The names and numbers below are made up for the test. They are not from any brief.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "builder"))

import limits  # noqa: E402

failures = []


def check(label, ok, detail=""):
    print(("OK    " if ok else "FAIL  ") + label)
    if not ok:
        failures.append(label)
        if detail:
            print(detail)


def run_hook(code, variables, data):
    result = {"status": "success", "data": data}
    exec(code, {"vars": variables, "result": result})
    return result["data"]


def tool(name, hook=""):
    t = {"type": "function", "function": {"name": name, "handler": "__function__:api_call"}}
    if hook:
        t["post_process_code"] = hook
    return t


# --- counting tool results ----------------------------------------------------
hook = limits.counter_hook("bad_tries", "accepted", False, 2, "LIMIT REACHED")
state = {}
check("a matching result counts 1", run_hook(hook, state, {"accepted": False}).get("what_to_do_next") is None and state["bad_tries"] == 1)
check("a non-matching result does not count", run_hook(hook, state, {"accepted": True}) == {"accepted": True} and state["bad_tries"] == 1)
check("string booleans count too", run_hook(hook, state, {"accepted": "false"})["what_to_do_next"] == "LIMIT REACHED" and state["bad_tries"] == 2)
exec(hook, {"vars": {}, "result": "not a dict"})
exec(hook, {"result": {"data": {"accepted": False}}})  # no `vars` at all
check("the hook never raises", True)

workflow = {"nodes": {
    "ask": {"name": "ask", "tools": [tool("check_code", "result.pop('header', None)\n"), tool("other")],
            "transitions": [{"target_node": "next", "description": "ok", "condition": {"operator": "always"}}]},
    "next": {"name": "next"},
    "give_up": {"name": "give_up"},
}}
limits.apply_tool_limit(workflow, "ask", "check_code", "bad_tries", "accepted", False, 2, "give_up", "LIMIT REACHED")
ask = workflow["nodes"]["ask"]
check("the limit transition is first", ask["transitions"][0]["target_node"] == "give_up" and len(ask["transitions"]) == 2)
check("it fires at the limit, not before", ask["transitions"][0]["condition"]["conditions"] == [
    {"variable": "bad_tries", "operator": "greater_than", "operand": 1}])
check("the existing hook is kept", ask["tools"][0]["post_process_code"].startswith("result.pop('header', None)\n"))
check("other tools are untouched", "post_process_code" not in ask["tools"][1])
state, data = {}, {"header": 1, "data": {"accepted": False}}
exec(ask["tools"][0]["post_process_code"], {"vars": state, "result": data})
check("the combined hook runs both parts", "header" not in data and state["bad_tries"] == 1)

for bad in (0, -1, 1.5, True):
    try:
        limits.limit_transition("c", bad, "give_up")
        check("limit %r is rejected" % (bad,), False)
    except ValueError:
        check("limit %r is rejected" % (bad,), True)
for args in (("nowhere", "check_code"), ("ask", "no_such_tool")):
    try:
        limits.apply_tool_limit(workflow, args[0], args[1], "c", "f", 1, 1, "give_up")
        check("unknown %s is rejected" % args[1], False)
    except KeyError:
        check("unknown %s/%s is rejected" % args, True)

# --- counting how often a step is entered -------------------------------------
limits.add_counted_step(workflow, "offer_gate", "offers_made", 1, within="next", over="give_up")
gate = workflow["nodes"]["offer_gate"]
code = gate["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
state = {}
exec(code, {"vars": state})
check("first entry is allowed", state == {"offers_made": 1, "offers_made_state": "open"})
exec(code, {"vars": state})
check("second entry is over the limit", state == {"offers_made": 2, "offers_made_state": "used_up"})
check("over the limit goes to the other node", gate["transitions"][0]["target_node"] == "give_up"
      and gate["transitions"][0]["condition"]["conditions"][0]["operand"] == 1)
check("the gate ends with an always transition", gate["transitions"][-1] == {
    "target_node": "next", "description": "always", "condition": {"operator": "always"}})
check("passthrough node with a default", gate["passthrough"] is True and gate["passthrough_default_transition"] == "next")

dumped = json.dumps(workflow)
check("no null in the workflow", "null" not in dumped)
check("no double braces in generated code", "{{" not in code and "{{" not in hook)

print()
print("ALL CHECKS PASSED" if not failures else "%d CHECK(S) FAILED" % len(failures))
sys.exit(1 if failures else 0)
