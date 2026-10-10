"""Checks for tools_gen.py and trace.py. Run from the project root:

    python tests/test_prep.py

1. Tools generated from samples/sample_openapi.json must be identical to the
   api_call tools inside reference_workflow.json.
2. A trace written by trace.py must contain everything the submission needs.
"""
import copy
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "builder"))

import tools_gen  # noqa: E402
from trace import Trace, count_steps_and_tools  # noqa: E402

failures = []


def check(label, ok, detail=""):
    print(("OK    " if ok else "FAIL  ") + label)
    if not ok:
        failures.append(label)
        if detail:
            print(detail)


def reference_api_tools(workflow):
    found = {}
    holders = list(workflow["nodes"].values()) + list(workflow["global_nodes"].values()) + [workflow]
    for holder in holders:
        for tool in holder.get("tools") or []:
            if tool["function"]["handler"] == tools_gen.API_CALL_HANDLER:
                found[tool["function"]["name"]] = tool
    return found


def normalised(function):
    # The panel adds a list of allowed HTTP methods to tools saved from the UI
    # (close_call in the reference); it carries no information from the API.
    function = copy.deepcopy(function)
    function["parameters"]["properties"]["method"].pop("enum", None)
    return function


with open(os.path.join(ROOT, "reference_workflow.json"), encoding="utf-8") as f:
    reference = json.load(f)

# --- tools_gen ---------------------------------------------------------------
warnings = []
generated = tools_gen.generate_tools(
    tools_gen.load_spec(os.path.join(ROOT, "samples", "sample_openapi.json")), warnings=warnings
)
expected = reference_api_tools(reference)

check("same tool names as the reference", sorted(generated) == sorted(expected),
      "generated: %s\nreference: %s" % (sorted(generated), sorted(expected)))
check("no warnings for the sample", warnings == [], str(warnings))
for name in sorted(expected):
    if name not in generated:
        continue
    got, want = normalised(generated[name]["function"]), normalised(expected[name]["function"])
    check("tool %s matches the reference" % name, got == want,
          "generated: %s\nreference: %s" % (json.dumps(got, sort_keys=True), json.dumps(want, sort_keys=True)))
    check("tool %s has the reference config" % name,
          generated[name]["config"] == {"timeoutMs": 12000, "maxRetries": 0})

# A spec the reference does not cover: other field names, format hints, no operationId.
other_spec = {
    "servers": [{"url": "https://example.test/api/"}],
    "paths": {"/schedule_callback": {"post": {
        "summary": "Book a callback.",
        "requestBody": {"content": {"application/json": {"schema": {
            "type": "object",
            "required": ["customer_id", "when"],
            "properties": {
                "customer_id": {"type": "string"},
                "when": {"type": "string", "format": "date-time", "description": "Callback time."},
                "day": {"type": "string", "format": "date"},
                "channel": {"type": ["string", "null"], "enum": ["phone", "sms", None]},
            },
        }}}},
    }}},
}
other = tools_gen.generate_tools(other_spec)["schedule_callback"]["function"]
body = other["parameters"]["properties"]["body"]
check("name falls back to the path", other["name"] == "schedule_callback")
check("url is server + path", other["parameters"]["properties"]["url"]["default"] == "https://example.test/api/schedule_callback")
check("customer_id is not required from the model", body["required"] == ["when"])
check("format is added to the description", body["properties"]["when"]["description"] == "Callback time. (format: date-time)")
check("date format is spelled out", body["properties"]["day"]["description"] == "(YYYY-MM-DD)")
check("nullable enum is cleaned", body["properties"]["channel"] == {"type": "string", "enum": ["phone", "sms"], "default": "{{channel}}"})

# --- trace -------------------------------------------------------------------
check("reference has 2 steps and 5 tools", count_steps_and_tools(reference) == (2, 5),
      str(count_steps_and_tools(reference)))

with tempfile.TemporaryDirectory() as tmp:
    trace = Trace(brief_path="briefs/brief-a.md", tool_api_path="tools/openapi.json", out_dir=tmp, name="test")
    trace.model_call("some-model", "extract rules from the brief")
    trace.workflow_created("wf_123", agent_id="ag_456", workflow=reference)
    trace.self_test("happy path", True)
    trace.self_test("wrong person", False, "agent mentioned the debt")
    path = trace.finish()
    with open(path, encoding="utf-8") as f:
        saved = json.load(f)

check("trace has start and end time", bool(saved["started_at"]) and bool(saved["ended_at"]))
check("trace has the model call", saved["model_calls"][0]["model"] == "some-model"
      and saved["model_calls"][0]["purpose"] == "extract rules from the brief")
check("trace has ids and counts", saved["workflows"][0]["workflow_id"] == "wf_123"
      and saved["workflows"][0]["agent_id"] == "ag_456"
      and saved["workflows"][0]["step_count"] == 2 and saved["workflows"][0]["tool_count"] == 5)
check("trace has self-test results", saved["summary"]["self_tests_passed"] == 1
      and saved["summary"]["self_tests_total"] == 2)

print()
print("ALL CHECKS PASSED" if not failures else "%d CHECK(S) FAILED" % len(failures))
sys.exit(1 if failures else 0)
