"""Builder trace: the log file the hackathon asks for with every submission.

It records start/end time, every model call (model + one-line purpose),
the workflow/agent ids we created (with step and tool counts) and the
self-test results. The file is rewritten after every event, so a crash
in the middle of a build still leaves a usable trace.

Usage:
    trace = Trace(brief_path="briefs/brief-a.md", tool_api_path="tools/openapi.json")
    trace.model_call("claude-opus-5-5", "extract rules from the brief")
    trace.workflow_created(workflow_id="wf_1", agent_id="ag_1", workflow=workflow_json)
    trace.self_test("happy path promise", passed=True)
    trace.finish()

Never pass API keys or other secrets into the trace: it is committed and submitted.
"""
import json
import os
import time
from datetime import datetime, timezone

DEFAULT_TRACE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "traces")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def count_steps_and_tools(workflow):
    """Return (step_count, tool_count) for a Freya workflow JSON."""
    nodes = list(workflow.get("nodes", {}).values()) + list(workflow.get("global_nodes", {}).values())
    tool_names = set()
    for holder in nodes + [workflow]:
        for tool in holder.get("tools") or []:
            tool_names.add(tool.get("function", {}).get("name"))
    tool_names.discard(None)
    return len(nodes), len(tool_names)


class Trace:
    def __init__(self, brief_path=None, tool_api_path=None, out_dir=None, name=None):
        self._t0 = time.monotonic()
        started = _now()
        if name is None:
            name = "trace_" + started.replace(":", "").replace("-", "").split("+")[0]
        out_dir = out_dir or DEFAULT_TRACE_DIR
        os.makedirs(out_dir, exist_ok=True)
        self.path = os.path.join(out_dir, name + ".json")
        self.data = {
            "started_at": started,
            "ended_at": None,
            "duration_seconds": None,
            "status": "running",
            "brief": brief_path,
            "tool_api": tool_api_path,
            "model_calls": [],
            "workflows": [],
            "self_tests": [],
            "notes": [],
        }
        self.save()

    def model_call(self, model, purpose):
        """Log one LLM call made by the builder."""
        self.data["model_calls"].append({"at": _now(), "model": model, "purpose": purpose})
        self.save()

    def workflow_created(self, workflow_id, agent_id=None, workflow=None, step_count=None, tool_count=None):
        """Log a created/updated workflow. Pass the workflow JSON to get the counts filled in."""
        if workflow is not None:
            step_count, tool_count = count_steps_and_tools(workflow)
        self.data["workflows"].append({
            "at": _now(),
            "workflow_id": workflow_id,
            "agent_id": agent_id,
            "step_count": step_count,
            "tool_count": tool_count,
        })
        self.save()

    def self_test(self, name, passed, detail=""):
        """Log the result of one self-test."""
        self.data["self_tests"].append({"at": _now(), "name": name, "passed": bool(passed), "detail": detail})
        self.save()

    def note(self, message):
        """Log anything else worth keeping (warnings, retries, decisions)."""
        self.data["notes"].append({"at": _now(), "message": message})
        self.save()

    def finish(self, status="ok"):
        """Close the trace. Use status="failed" when the build crashed."""
        self.data["ended_at"] = _now()
        self.data["duration_seconds"] = round(time.monotonic() - self._t0, 1)
        self.data["status"] = status
        tests = self.data["self_tests"]
        self.data["summary"] = {
            "model_call_count": len(self.data["model_calls"]),
            "self_tests_passed": sum(1 for t in tests if t["passed"]),
            "self_tests_total": len(tests),
        }
        self.save()
        return self.path

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)
