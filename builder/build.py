"""The builder: brief files + tool API file in, phone agent on Freya out. One command, no manual step.

    python builder/with_env.py python builder/build.py <brief.md> [<more briefs>...] --tools <openapi.json> [--test-brief A]

Steps:
1. tools_gen: the tool API file -> Freya tool definitions          (code)
2. extract:   the brief files -> structured rule list (spec)       (model call, checked by code)
3. compile:   spec + tools -> workflow, saved to Freya and tested  (Claude session with the freya-phone-agent skill)
4. checks:    every limit of the spec is accounted for; export; trace

Everything for one run is written to build/<label>/, the trace to traces/.
"""
import argparse
import asyncio
import dataclasses
import glob
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.path.join(ROOT, "kit")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import extract as extract_step  # noqa: E402
import tools_gen  # noqa: E402
from save_workflow import SLOT, STATE_FILE  # noqa: E402
from trace import Trace  # noqa: E402

MODEL = os.environ.get("BUILDER_MODEL", "claude-opus-5-5")

COMPILE_PROMPT = """Use the freya-phone-agent skill to build a phone agent. Work from this folder: {root}

INPUT (the only sources of rules and values):
- Brief files, in order (a later file changes an earlier one): {briefs}
- The tool API file: {tools_api}
- {run}/spec.json: the rule list already extracted from the brief files, with every limit
- {run}/tools.json: the Freya tool definitions already generated from the tool API file
- kit/docs/workflow-format.md and kit/docs/tools-and-code.md: the platform format
- builder/limits.py: how a limit is built (read its docstring)

DO NOT read kit/out, experiments, kit/results, kit/traces, traces, kit/public-test-set.json or another
build/ folder. They hold answers for other runs. An agent fitted to them fails on new customers.

WHAT TO PRODUCE
1. {run}/make_workflow.py, a Python script that writes {run}/workflow.json.
   - Load the tools from {run}/tools.json. Never retype a field name or an allowed value. On top of a
     loaded tool you may set the description, hooks, response_mapping and config.
   - Apply EVERY limit in spec.json "limits" with builder/limits.py (apply_tool_limit when a tool result
     shows the counted event, add_counted_step when the counted thing is something the agent says or does).
     Where n depends on a condition, or the counted thing is something the caller does, count it in code
     another way, but never ask the model to count.
   - Also write {run}/limits_applied.json: a list with one entry per limit id in spec.json:
     {{"id": "", "how": "tool_limit | counted_step | code_state | prompt_only", "where": "node or tool", "why": ""}}.
     "prompt_only" is allowed only with a reason why code cannot count it.
   - Leave agent_config out. No null anywhere.
2. Save it: python builder/save_workflow.py {run}/workflow.json
   It prints workflow_id and agent_id. It always updates the same workflow. Never create another one.
3. Test it: {test_command}
   A run takes 2 to 11 minutes. Run it in the foreground and wait. Never end your turn while a test runs.
4. For each FAIL line, find the cause in the call (see docs/api.md: freya.get_call(call_id)["messages"],
   the call ids are in the --out file), fix make_workflow.py, save and test again.
   At most {max_tests} test runs in total. Stop earlier when everything passes.

RULES
- Use `python`, not `python3`. Run one test at a time.
- Never edit the agent on the platform by hand. Change the script and save.
- You have about 25 minutes in total. Leave time to save the last version.
- Finish with one line: the agent_id and how many customers passed in the last run.
"""


def run_dir_for(briefs):
    label = os.path.splitext(os.path.basename(briefs[-1]))[0]
    path = os.path.join(ROOT, "build", label)
    os.makedirs(path, exist_ok=True)
    return label, path


async def compile_session(prompt, log_path, trace):
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, ToolUseBlock, query
    options = ClaudeAgentOptions(
        cwd=ROOT, model=MODEL, skills=["freya-phone-agent"], setting_sources=["project", "local"],
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash"], permission_mode="dontAsk", max_turns=150,
        # A test run can take 11 minutes; keep it in the foreground (same settings as the kit's example).
        env={"BASH_DEFAULT_TIMEOUT_MS": "900000", "BASH_MAX_TIMEOUT_MS": "1200000", "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"},
    )
    trace.model_call(MODEL, "compile session: write the workflow from spec and tools, save it, test it, fix failures")
    result = None
    with open(log_path, "a", encoding="utf-8") as log:
        async for message in query(prompt=prompt, options=options):
            log.write(json.dumps({"type": type(message).__name__, **dataclasses.asdict(message)}, default=str) + "\n")
            log.flush()
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        print(block.text, flush=True)
                    elif isinstance(block, ToolUseBlock):
                        print("  [%s] %s" % (block.name, str(block.input)[:120]), flush=True)
            elif isinstance(message, ResultMessage):
                result = message
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build a Freya phone agent from brief files.")
    parser.add_argument("briefs", nargs="+")
    parser.add_argument("--tools", required=True, help="the tool API file (openapi.json)")
    parser.add_argument("--test-brief", default="", help="practice cases to self-test on: A, B, or 'all'")
    parser.add_argument("--max-tests", type=int, default=3)
    args = parser.parse_args(argv)

    label, run = run_dir_for(args.briefs)
    rel = lambda p: os.path.relpath(os.path.abspath(p), ROOT).replace("\\", "/")
    trace = Trace(brief_path=", ".join(rel(b) for b in args.briefs), tool_api_path=rel(args.tools), name="build-%s-%s" % (label, time.strftime("%Y%m%d-%H%M%S")))
    log_path = trace.path.replace(".json", ".messages.jsonl")
    status = "failed"
    try:
        # 1. tools
        warnings = []
        tools = tools_gen.generate_tools(tools_gen.load_spec(args.tools), warnings=warnings)
        with open(os.path.join(run, "tools.json"), "w", encoding="utf-8") as f:
            json.dump(tools, f, indent=1)
        trace.note("tools_gen: %d tools (%s)%s" % (len(tools), ", ".join(tools), "; warnings: " + "; ".join(warnings) if warnings else ""))

        # 2. spec
        spec = extract_step.extract(args.briefs, args.tools, model=MODEL, trace=trace)
        with open(os.path.join(run, "spec.json"), "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=1, ensure_ascii=False)
        trace.note("extract: %d stages, %d limits (%s), %d endings" % (
            len(spec["stages"]), len(spec["limits"]), ", ".join(l["id"] for l in spec["limits"]), len(spec["endings"])))
        print("spec: %d stages, %d limits, %d endings" % (len(spec["stages"]), len(spec["limits"]), len(spec["endings"])), flush=True)

        # 3. compile, save, test
        out = "%s/test" % rel(run)
        brief_flag = "" if args.test_brief in ("", "all") else " --brief " + args.test_brief
        test_command = ("python kit/tools/run_cases.py <agent_id>%s --out %s-<n>.json" % (brief_flag, out)) if args.test_brief else \
            "no practice customers are given for this brief: test with chat turns (kit/docs/api.md, 'Talk to your workflow turn by turn') on callers you write yourself"
        prompt = COMPILE_PROMPT.format(root=ROOT, briefs=", ".join(rel(b) for b in args.briefs), tools_api=rel(args.tools),
                                       run=rel(run), test_command=test_command, max_tests=args.max_tests)
        result = asyncio.run(compile_session(prompt, log_path, trace))
        if result is not None:
            trace.note("compile session: %s turns, %.0f s, $%.2f" % (result.num_turns, result.duration_ms / 1000, result.total_cost_usd or 0))

        # 4. checks
        with open(os.path.join(run, "workflow.json"), encoding="utf-8") as f:
            workflow = json.load(f)
        with open(STATE_FILE, encoding="utf-8") as f:
            ids = json.load(f)[SLOT]
        trace.workflow_created(ids["workflow_id"], agent_id=ids["agent_id"], workflow=workflow)

        applied = {}
        applied_path = os.path.join(run, "limits_applied.json")
        if os.path.exists(applied_path):
            with open(applied_path, encoding="utf-8") as f:
                applied = {a.get("id"): a for a in json.load(f)}
        missing = [l["id"] for l in spec["limits"] if l["id"] not in applied]
        prompt_only = [i for i, a in applied.items() if a.get("how") == "prompt_only"]
        trace.self_test("every limit of the spec is accounted for", not missing,
                        "missing: %s" % ", ".join(missing) if missing else "%d limits; left to the prompt: %s" % (len(applied), ", ".join(prompt_only) or "none"))

        for path in sorted(glob.glob(os.path.join(run, "test-*.json"))):
            with open(path, encoding="utf-8") as f:
                test = json.load(f)
            failed = [r["id"] for r in test.get("final", []) if not r["pass"]]
            trace.self_test("practice customers, %s" % os.path.basename(path), not failed,
                            "%s of %s passed%s" % (test.get("passed"), test.get("total"), "; failed: " + ", ".join(failed) if failed else ""))

        sys.path.insert(0, KIT)
        import freya
        export = os.path.join(ROOT, "submission", label + ".workflow.json")
        freya.export_workflow(ids["workflow_id"], export)
        trace.note("exported to " + rel(export))
        status = "ok" if not missing else "incomplete"
    except Exception as error:
        trace.note("build failed: %s: %s" % (type(error).__name__, str(error)[:1500]))
        raise
    finally:
        print("\ntrace: %s (%s)" % (trace.finish(status), status), flush=True)


if __name__ == "__main__":
    main()
