"""The builder: brief files + tool API file in, phone agent on Freya out. One command, no manual step.

    python builder/with_env.py python builder/build.py <brief.md> [<more briefs>...] --tools <openapi.json> [--test-brief A]
    python builder/with_env.py python builder/build.py <case folder> [--tools <openapi.json>]

A case folder is taken whole, with its subfolders: every file in it is a brief file. Without --tools the
tool API file is the one OpenAPI file (*.json with "openapi" and "paths") found in the folder.

Steps:
1. workspace: a new temporary folder with ONLY the brief files, the tool API file, the skills,
              the platform docs and builder/limits.py. The model works there and nowhere else.
2. tools_gen: the tool API file -> Freya tool definitions          (code)
3. extract:   the brief files -> structured rule list (spec)       (model call, checked by code)
4. compile:   the model writes out/make_workflow.py                (model session, file tools only)
5. the builder itself runs that script, checks the result, saves it to Freya and tests it;
   failures go back to the model as a feedback file. Repeat up to --max-tests test runs.
6. trace, export, copy the outputs to build/<label>/.

Isolation: the model has no shell and cannot read outside the workspace, so it cannot open the
practice test set, earlier answers or other builds. The practice customers are run by this
program, outside the workspace. The model sees only why a failed case failed.
"""
import argparse
import asyncio
import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.path.join(ROOT, "kit")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import extract as extract_step  # noqa: E402
import save_workflow  # noqa: E402
import tools_gen  # noqa: E402
from trace import Trace  # noqa: E402

MODEL = os.environ.get("BUILDER_MODEL", "claude-opus-5-5")
TIME_BUDGET_SECONDS = 22 * 60  # no new round starts after this; the rule is 30 minutes per brief
FILE_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep"]
BLOCKED_TOOLS = ["Bash", "PowerShell", "WebFetch", "WebSearch", "Agent", "Task", "NotebookEdit"]
WORKSPACE_SETTINGS = {"permissions": {"blockReadsOutsideWorkingDirectories": True, "deny": BLOCKED_TOOLS}}
SECRET_PREFIXES = ("FREYA_", "ANTHROPIC_")
LARGE_FILE_BYTES = 150 * 1024  # a file above this is data to look things up in, not a text to read from end to end
LARGE_NOTE = ("\n- LARGE DATA FILES in briefs/ (do not read them whole: they are data; read the first lines to see the"
              " shape and search them with Grep when you need one record): {names}")

COMPILE_PROMPT = """Use the freya-phone-agent skill to build a phone agent.

This folder holds everything you may use. There is nothing else, and you have no shell:
- briefs/: the brief files, {order}: {briefs}{large}
- {tools_api}: the tool API file
- spec.json: the rule list already extracted from the brief files, with every limit
- tools.json: the Freya tool definitions already generated from the tool API file
- kit/docs/workflow-format.md, kit/docs/tools-and-code.md, kit/docs/api.md: the platform format
- builder/limits.py: how a limit is built (read its docstring)

WRITE out/make_workflow.py. The builder runs it as `python out/make_workflow.py` from this folder.
It must write out/workflow.json and out/limits_applied.json, with paths taken from its own location.
- Load the tools from tools.json. Never retype a field name or an allowed value. On top of a loaded
  tool you may set the description, hooks, response_mapping and config, and the default of a body field
  (a call variable as {{{{name}}}}, or a fixed value) so that the workflow fills it instead of the model.
  A loaded tool may already have pre_process_code: it puts the flat body fields into the nested body the
  API expects. Keep that code as the LAST part of the tool's pre_process_code; put your own code before it.
- Apply EVERY limit in spec.json "limits" with builder/limits.py (apply_tool_limit when a tool result
  shows the counted event, add_counted_step when the counted thing is something the agent says or does).
  Where n depends on a condition, or the counted thing is something the caller does, count it in code
  another way, but never ask the model to count.
- out/limits_applied.json: a list with one entry per limit id in spec.json:
  {{"id": "", "how": "tool_limit | counted_step | code_state | prompt_only", "where": "node or tool", "why": ""}}.
  "prompt_only" is allowed only with a reason why code cannot count it.
- Let the script test its own code (dates, amounts, counters) with asserts when it runs. If it raises,
  you get the error back.
- Leave agent_config out. No null anywhere.

You cannot run anything yourself. When you stop, the builder runs your script, checks and saves the
workflow and tests it on customers you cannot see. If something fails you get a feedback file and
another turn. Finish each turn with one line saying what you wrote or changed.
"""

REPAIR_PROMPT = """The builder could not use your script:

{problem}

Fix out/make_workflow.py. Change only what this needs."""

FEEDBACK_PROMPT = """Test run {n}: {passed} of {total} customers passed.

Read {feedback}. For each failed customer it gives the reasons and what happened in the call
(what was said, which tools were called with what, which routes were taken).
Find the cause, then fix out/make_workflow.py. Prefer a fix in the structure or in code over one more
sentence in a prompt. Do not break what passed."""


def brief_files(paths):
    """The brief files behind the arguments: [(file, name inside briefs/)]. A folder gives every file in it."""
    found = []
    for path in paths:
        if not os.path.isdir(path):
            found.append((path, os.path.basename(path)))
            continue
        for folder, subfolders, names in os.walk(path):
            subfolders[:] = sorted(d for d in subfolders if not d.startswith(".") and d != "node_modules")
            for name in sorted(names):
                if not name.startswith("."):
                    full = os.path.join(folder, name)
                    found.append((full, os.path.relpath(full, path).replace(os.sep, "/")))
    return found


def find_tool_api(paths):
    """The one OpenAPI file inside the given folders. Raises ValueError when there is none or more than one."""
    found = []
    for full, _ in brief_files([p for p in paths if os.path.isdir(p)]):
        if full.lower().endswith(".json"):
            try:
                with open(full, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and "openapi" in data and "paths" in data:
                found.append(full)
    if len(found) != 1:
        raise ValueError("--tools is needed: %d OpenAPI files found in the case folder%s" % (
            len(found), (" (" + ", ".join(found) + ")") if found else ""))
    return found[0]


def make_workspace(briefs, tool_api):
    """A new folder with only what the model may see. `briefs` is the list brief_files() gives."""
    ws = tempfile.mkdtemp(prefix="freya-build-")
    os.makedirs(os.path.join(ws, "briefs"))
    for brief, name in briefs:
        target = os.path.join(ws, "briefs", name.replace("/", os.sep))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy(brief, target)
    os.makedirs(os.path.join(ws, "tool_api"))
    shutil.copy(tool_api, os.path.join(ws, "tool_api", os.path.basename(tool_api)))
    shutil.copytree(os.path.join(ROOT, ".claude", "skills"), os.path.join(ws, ".claude", "skills"))
    os.makedirs(os.path.join(ws, "kit", "docs"))
    for name in os.listdir(os.path.join(KIT, "docs")):
        shutil.copy(os.path.join(KIT, "docs", name), os.path.join(ws, "kit", "docs", name))
    os.makedirs(os.path.join(ws, "builder"))
    shutil.copy(os.path.join(ROOT, "builder", "limits.py"), os.path.join(ws, "builder", "limits.py"))
    os.makedirs(os.path.join(ws, "out"))
    os.makedirs(os.path.join(ws, "feedback"))
    with open(os.path.join(ws, ".claude", "settings.json"), "w", encoding="utf-8") as f:
        json.dump(WORKSPACE_SETTINGS, f, indent=1)
    return ws


def session_options(ws, resume=None):
    from claude_agent_sdk import ClaudeAgentOptions
    skills = sorted(os.listdir(os.path.join(ws, ".claude", "skills")))
    return ClaudeAgentOptions(cwd=ws, model=MODEL, skills=skills, setting_sources=["project"], allowed_tools=FILE_TOOLS,
                              disallowed_tools=BLOCKED_TOOLS, permission_mode="dontAsk", max_turns=100, resume=resume)


async def _session(prompt, ws, log_path, resume):
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock, query
    result = None
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(json.dumps({"type": "BuilderPrompt", "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "prompt": prompt}) + "\n")
        async for message in query(prompt=prompt, options=session_options(ws, resume)):
            log.write(json.dumps({"type": type(message).__name__, **dataclasses.asdict(message)}, default=str) + "\n")
            log.flush()
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        print(block.text, flush=True)
                    elif isinstance(block, ToolUseBlock):
                        print("  [%s] %s" % (block.name, str(block.input)[:110]), flush=True)
            elif isinstance(message, ResultMessage):
                result = message
    return result


class Compiler:
    """The model session that writes out/make_workflow.py, kept across rounds."""

    def __init__(self, ws, log_path, trace):
        self.ws, self.log_path, self.trace, self.session_id, self.cost = ws, log_path, trace, None, 0.0

    def ask(self, prompt, purpose):
        self.trace.model_call(MODEL, purpose)
        result = asyncio.run(_session(prompt, self.ws, self.log_path, self.session_id))
        if result is None:
            raise RuntimeError("the model session ended without a result")
        self.session_id = result.session_id
        self.cost += result.total_cost_usd or 0
        if result.permission_denials:
            self.trace.note("%s: %d tool call(s) refused by the workspace rules" % (purpose, len(result.permission_denials)))
        return result


def run_script(ws, spec):
    """Run the model's script. Return (workflow, limits_applied) or raise ValueError with what to tell the model."""
    for name in ("workflow.json", "limits_applied.json"):
        path = os.path.join(ws, "out", name)
        if os.path.exists(path):
            os.remove(path)
    if not os.path.exists(os.path.join(ws, "out", "make_workflow.py")):
        raise ValueError("out/make_workflow.py does not exist.")
    env = {k: v for k, v in os.environ.items() if not k.startswith(SECRET_PREFIXES)}
    env["PYTHONUTF8"] = "1"
    try:
        run = subprocess.run([sys.executable, os.path.join("out", "make_workflow.py")], cwd=ws, env=env,
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    except subprocess.TimeoutExpired:
        raise ValueError("`python out/make_workflow.py` did not finish in 180 seconds.")
    if run.returncode != 0:
        raise ValueError("`python out/make_workflow.py` failed:\n" + (run.stderr or run.stdout)[-3000:])
    try:
        with open(os.path.join(ws, "out", "workflow.json"), encoding="utf-8") as f:
            workflow = json.load(f)
        with open(os.path.join(ws, "out", "limits_applied.json"), encoding="utf-8") as f:
            applied = json.load(f)
    except (OSError, ValueError) as error:
        raise ValueError("the script ran but its output cannot be read: %s" % error)
    problems = save_workflow.problems_in(workflow)
    known = {a.get("id") for a in applied if isinstance(a, dict)}
    problems += ["limit %s of spec.json is missing from out/limits_applied.json" % l["id"] for l in spec["limits"] if l["id"] not in known]
    problems += ["limit %s is prompt_only without a reason" % a.get("id") for a in applied
                 if isinstance(a, dict) and a.get("how") == "prompt_only" and not a.get("why")]
    if problems:
        raise ValueError("the workflow was written but has problems:\n- " + "\n- ".join(problems))
    return workflow, applied


def run_practice(agent_id, test_brief, out_path):
    """Run the kit's practice customers (outside the workspace). Returns the result dict of run_cases.py."""
    command = [sys.executable, "-u", os.path.join(KIT, "tools", "run_cases.py"), agent_id, "--out", out_path]
    if test_brief != "all":
        command += ["--brief", test_brief]
    run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1500)
    if run.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError("the practice run failed: " + (run.stderr or run.stdout)[-800:])
    print(run.stdout, flush=True)
    with open(out_path, encoding="utf-8") as f:
        return json.load(f)


def call_summary(call):
    """One call as short lines: what was said, tool calls with their results, routes."""
    lines = []
    for m in call.get("messages", []):
        role, text = m.get("role"), m.get("message") or ""
        if role != "system":
            lines.append("%s: %s" % ("AGENT" if role == "assistant" else "CALLER", text))
            continue
        try:
            event = json.loads(text)
        except ValueError:
            continue
        kind = event.get("event")
        if kind == "node_enter":
            lines.append("   [enter node %s]" % event.get("node"))
        elif kind == "function_call_start":
            lines.append("   [call %s %s]" % (event.get("function"), json.dumps((event.get("args") or {}).get("body"))[:300]))
        elif kind == "function_call_complete":
            result = event.get("result")
            lines.append("   [result %s %s]" % (event.get("function"), json.dumps(result.get("data") if isinstance(result, dict) else result)[:500]))
        elif kind == "route_evaluated" and event.get("matched"):
            lines.append("   [route %s -> %s]" % (event.get("from_node"), event.get("target")))
        elif kind == "error":
            lines.append("   [ERROR %s]" % str(event.get("message"))[:300])
    lines.append("   [call ended: %s]" % call.get("endedReason"))
    return "\n".join(lines)


def write_feedback(ws, n, test, freya):
    """Failed customers of one test run -> feedback/round-<n>.txt in the workspace."""
    parts = []
    for row in test["rows"]:
        if row["pass"]:
            continue
        parts.append("=" * 70 + "\nFAILED: %s (%s)\nReasons:\n- %s" % (row["id"], row.get("family"), "\n- ".join(row["failed"])))
        if row.get("call_id"):
            try:
                parts.append("What happened in the call:\n" + call_summary(freya.get_call(row["call_id"])))
            except Exception as error:
                parts.append("(the call could not be read: %s)" % error)
    name = "feedback/round-%d.txt" % n
    with open(os.path.join(ws, name.replace("/", os.sep)), "w", encoding="utf-8") as f:
        f.write("\n\n".join(parts) + "\n")
    return name


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build a Freya phone agent from brief files.")
    parser.add_argument("briefs", nargs="+")
    parser.add_argument("--tools", default="", help="the tool API file (openapi.json); may be left out when a case folder holds exactly one")
    parser.add_argument("--test-brief", default="", help="practice cases to self-test on: A, B, or 'all'. Empty: no practice test")
    parser.add_argument("--max-tests", type=int, default=3)
    parser.add_argument("--keep-workspace", action="store_true")
    parser.add_argument("--reuse", default="", help="a build folder of an earlier run on the same briefs: its spec.json and "
                        "out/make_workflow.py are run again with tools generated anew from the tool API file (no model call). "
                        "For when only the address of the tool API changed")
    args = parser.parse_args(argv)

    started = time.time()
    folder = any(os.path.isdir(b) for b in args.briefs)
    if not args.tools:
        args.tools = find_tool_api(args.briefs)
    label = os.path.splitext(os.path.basename(os.path.abspath(args.briefs[-1])))[0]
    files = brief_files(args.briefs)
    run_dir = os.path.join(ROOT, "build", label)
    reused = None
    if args.reuse:  # read before the build folder is emptied: it may be the same folder
        with open(os.path.join(args.reuse, "spec.json"), encoding="utf-8") as f:
            reused_spec = json.load(f)
        with open(os.path.join(args.reuse, "out", "make_workflow.py"), encoding="utf-8") as f:
            reused = (reused_spec, f.read())
    if os.path.exists(run_dir):
        shutil.rmtree(run_dir)
    os.makedirs(run_dir)
    rel = lambda p: os.path.relpath(os.path.abspath(p), ROOT).replace("\\", "/")
    trace = Trace(brief_path=", ".join(rel(b) for b in args.briefs), tool_api_path=rel(args.tools),
                  name="build-%s-%s" % (label, time.strftime("%Y%m%d-%H%M%S")))
    log_path = trace.path.replace(".json", ".messages.jsonl")
    ws = make_workspace(files, args.tools)
    large = ["briefs/" + name for full, name in files if os.path.getsize(full) > LARGE_FILE_BYTES]
    ws_briefs = ["briefs/" + name for _, name in files if "briefs/" + name not in large]
    # a folder has no order of its own: there the documents' own dates decide which one is newer
    order = ("a folder in no particular order (when two disagree, the later-dated document wins)" if folder
             else "in this order (a later file changes an earlier one)")
    ws_tool_api = "tool_api/" + os.path.basename(args.tools)
    trace.note("workspace: a temporary folder holding only %s" % ", ".join(sorted(os.listdir(ws))))
    status = "failed"
    try:
        if reused:
            spec, script = reused
            given = tuple(v.get("name") for v in spec.get("call_variables") or [] if isinstance(v, dict) and v.get("name"))
            tools = tools_gen.generate_tools(tools_gen.load_spec(args.tools),
                                             context_vars=tuple(dict.fromkeys(tools_gen.CONTEXT_VARS + given)))
            for name, value in (("spec.json", spec), ("tools.json", tools)):
                with open(os.path.join(ws, name), "w", encoding="utf-8") as f:
                    json.dump(value, f, indent=1, ensure_ascii=False)
            with open(os.path.join(ws, "out", "make_workflow.py"), "w", encoding="utf-8") as f:
                f.write(script)
            trace.note("--reuse %s: no model call. The spec and the workflow script of that run are used unchanged; "
                       "tools.json is generated anew from %s (%s)" % (rel(args.reuse), rel(args.tools),
                                                                     tools_gen.load_spec(args.tools)["servers"][0]["url"]))
            workflow, applied = run_script(ws, spec)
            ids = save_workflow.save(workflow)
            trace.workflow_created(ids["workflow_id"], agent_id=ids["agent_id"], workflow=workflow)
            with open(os.path.join(run_dir, "workflow.json"), "w", encoding="utf-8") as f:
                json.dump(workflow, f, indent=1)
            sys.path.insert(0, KIT)
            import freya
            export = os.path.join(ROOT, "submission", label + ".workflow.json")
            freya.export_workflow(ids["workflow_id"], export)
            trace.note("exported to " + rel(export))
            status = "ok"
            return
        # tools and spec
        warnings = []
        spec = extract_step.extract(ws_briefs, ws_tool_api, model=MODEL, trace=trace, cwd=ws, ordered=not folder, large=large)
        with open(os.path.join(ws, "spec.json"), "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=1, ensure_ascii=False)
        # a tool field named like a call variable is filled by the platform, whatever this brief calls its variables
        given = tuple(v.get("name") for v in spec.get("call_variables") or [] if isinstance(v, dict) and v.get("name"))
        tools = tools_gen.generate_tools(tools_gen.load_spec(args.tools), warnings=warnings,
                                         context_vars=tuple(dict.fromkeys(tools_gen.CONTEXT_VARS + given)))
        with open(os.path.join(ws, "tools.json"), "w", encoding="utf-8") as f:
            json.dump(tools, f, indent=1)
        trace.note("tools_gen: %d tools (%s)%s" % (len(tools), ", ".join(tools), "; warnings: " + "; ".join(warnings) if warnings else ""))
        trace.note("extract: %d stages, %d limits (%s), %d endings" % (
            len(spec["stages"]), len(spec["limits"]), ", ".join(l["id"] for l in spec["limits"]), len(spec["endings"])))
        print("spec: %d stages, %d limits, %d endings" % (len(spec["stages"]), len(spec["limits"]), len(spec["endings"])), flush=True)

        sys.path.insert(0, KIT)
        import freya
        compiler = Compiler(ws, log_path, trace)
        prompt, purpose = COMPILE_PROMPT.format(briefs=", ".join(ws_briefs), order=order, tools_api=ws_tool_api,
                                                large=LARGE_NOTE.format(names=", ".join(large)) if large else ""), "write the workflow script from spec and tools"
        best, tests, ids, applied = None, 0, None, []
        while True:
            compiler.ask(prompt, purpose)
            for attempt in range(4):  # the script must run and pass the checks before anything is saved
                try:
                    workflow, applied = run_script(ws, spec)
                    ids = save_workflow.save(workflow)
                    break
                except (ValueError, RuntimeError) as error:
                    if attempt == 3:
                        raise RuntimeError("the script still fails after 3 repairs: %s" % str(error)[:800])
                    print("  repair needed: %s" % str(error)[:300], flush=True)
                    compiler.ask(REPAIR_PROMPT.format(problem=str(error)[:4000]), "repair the workflow script after an error")
            trace.workflow_created(ids["workflow_id"], agent_id=ids["agent_id"], workflow=workflow)
            if not args.test_brief:
                trace.note("no practice customers given: the agent was saved without a customer test")
                break
            tests += 1
            test = run_practice(ids["agent_id"], args.test_brief, os.path.join(run_dir, "test-%d.json" % tests))
            failed = [r["id"] for r in test["rows"] if not r["pass"]]
            trace.self_test("practice customers, run %d" % tests, not failed,
                            "%s of %s passed%s" % (test["passed"], test["total"], "; failed: " + ", ".join(failed) if failed else ""))
            if best is None or test["passed"] >= best[0]:
                best = (test["passed"], workflow, tests)
            if not failed or tests >= args.max_tests or time.time() - started > TIME_BUDGET_SECONDS:
                break
            feedback = write_feedback(ws, tests, test, freya)
            prompt = FEEDBACK_PROMPT.format(n=tests, passed=test["passed"], total=test["total"], feedback=feedback)
            purpose = "fix the workflow script after test run %d" % tests

        if best is not None and best[1] is not workflow:
            ids = save_workflow.save(best[1])
            workflow = best[1]
            trace.note("the last version scored lower: the version of test run %d was saved again" % best[2])
        prompt_only = [a["id"] for a in applied if a.get("how") == "prompt_only"]
        trace.self_test("every limit of the spec is accounted for", True,
                        "%d limits; left to the prompt: %s" % (len(applied), ", ".join(prompt_only) or "none"))
        trace.note("model sessions cost $%.2f" % compiler.cost)
        with open(os.path.join(run_dir, "workflow.json"), "w", encoding="utf-8") as f:
            json.dump(workflow, f, indent=1)
        export = os.path.join(ROOT, "submission", label + ".workflow.json")
        freya.export_workflow(ids["workflow_id"], export)
        trace.note("exported to " + rel(export))
        status = "ok"
    except Exception as error:
        trace.note("build failed: %s: %s" % (type(error).__name__, str(error)[:1500]))
        raise
    finally:
        for name in ("spec.json", "tools.json"):
            if os.path.exists(os.path.join(ws, name)):
                shutil.copy(os.path.join(ws, name), os.path.join(run_dir, name))
        for name in ("out", "feedback"):
            if os.path.isdir(os.path.join(ws, name)):
                shutil.copytree(os.path.join(ws, name), os.path.join(run_dir, name), dirs_exist_ok=True)
        if args.keep_workspace:
            print("workspace kept: " + ws, flush=True)
        else:
            shutil.rmtree(ws, ignore_errors=True)
        print("\ntrace: %s (%s, %.0f s)" % (trace.finish(status), status, time.time() - started), flush=True)


if __name__ == "__main__":
    main()
