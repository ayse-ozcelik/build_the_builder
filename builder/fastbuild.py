"""The fast builder: brief files + tool API file in, phone agent on Freya out.

    python builder/with_env.py python builder/fastbuild.py <brief.md> [<more briefs>...] --tools <openapi.json> [--test-brief A] [--reps 3]

1. rules.py    a model fills the fixed rules form from the brief          (one model call, checked by code)
2. prompts.py  a model writes the text of each conversation step          (short model calls, in parallel)
3. compile.py  code builds the whole workflow from the rules and prompts  (no model)
4. save, one chat turn, optional practice customers, export, trace        (no model)

The model calls run in a temporary folder that holds only the brief files and the
tool API file; they have no shell and cannot read outside it.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.path.join(ROOT, "kit")
BLOCKED_TOOLS = ["Bash", "PowerShell", "WebFetch", "WebSearch", "Agent", "Task", "NotebookEdit"]
WORKSPACE_SETTINGS = {"permissions": {"blockReadsOutsideWorkingDirectories": True, "deny": BLOCKED_TOOLS}}


def make_workspace(briefs, tool_api):
    """A new folder with only the brief files and the tool API file."""
    ws = tempfile.mkdtemp(prefix="freya-fast-")
    os.makedirs(os.path.join(ws, "briefs"))
    for brief in briefs:
        shutil.copy(brief, os.path.join(ws, "briefs", os.path.basename(brief)))
    os.makedirs(os.path.join(ws, "tool_api"))
    shutil.copy(tool_api, os.path.join(ws, "tool_api", os.path.basename(tool_api)))
    os.makedirs(os.path.join(ws, ".claude"))
    with open(os.path.join(ws, ".claude", "settings.json"), "w", encoding="utf-8") as f:
        json.dump(WORKSPACE_SETTINGS, f, indent=1)
    return ws


def chat_turn(freya, workflow_id, rules):
    """One chat turn against the saved workflow: does it start and answer at all?"""
    variables = {name: "test" for name in rules.get("call_variables", [])}
    variables[rules["now_variable"]] = time.strftime("%Y-%m-%dT%H:%M")
    result = freya.api("POST", "/api/v2/chat/message", {"workflowId": workflow_id, "message": "Hello?", "customerVariables": variables})
    if result.get("callId"):
        try:
            freya.api("POST", "/api/v2/chat/end", {"callId": result["callId"]})
        except RuntimeError:
            pass
    return str(result.get("response") or "")


def run_practice(agent_id, test_brief, reps, out_path):
    """Run the kit's practice customers. Returns the result dict of run_cases.py."""
    command = [sys.executable, "-u", os.path.join(KIT, "tools", "run_cases.py"), agent_id, "--out", out_path, "--reps", str(reps)]
    if test_brief != "all":
        command += ["--brief", test_brief]
    run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3000)
    print(run.stdout, flush=True)
    if run.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError("the practice run failed: " + (run.stderr or run.stdout)[-800:])
    with open(out_path, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import compile as compile_step
    import prompts as prompts_step
    import rules as rules_step
    import save_workflow
    import tools_gen
    from trace import Trace

    parser = argparse.ArgumentParser(description="Build a Freya phone agent from brief files (fast, compile-based).")
    parser.add_argument("briefs", nargs="+")
    parser.add_argument("--tools", required=True, help="the tool API file (openapi.json)")
    parser.add_argument("--test-brief", default="", help="practice cases to test on after the build: A, B or 'all'")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--chat-turn", action="store_true", help="also send one chat turn after saving (can time out)")
    parser.add_argument("--no-save", action="store_true", help="stop after compiling: nothing is sent to Freya")
    parser.add_argument("--rules", help="reuse a rules file instead of asking the model (for experiments)")
    args = parser.parse_args(argv)

    started = time.time()
    label = "fast-" + os.path.splitext(os.path.basename(args.briefs[-1]))[0]
    run_dir = os.path.join(ROOT, "build", label)
    os.makedirs(run_dir, exist_ok=True)
    rel = lambda p: os.path.relpath(os.path.abspath(p), ROOT).replace("\\", "/")
    trace = Trace(brief_path=", ".join(rel(b) for b in args.briefs), tool_api_path=rel(args.tools),
                  name="%s-%s" % (label, time.strftime("%Y%m%d-%H%M%S")))
    ws = make_workspace(args.briefs, args.tools)
    status, clock = "failed", time.time()

    def done(step):
        nonlocal clock
        trace.note("%s took %.0f s" % (step, time.time() - clock))
        print("%-22s %4.0f s" % (step, time.time() - clock), flush=True)
        clock = time.time()

    def keep(name, value):
        with open(os.path.join(run_dir, name), "w", encoding="utf-8") as f:
            json.dump(value, f, indent=1, ensure_ascii=False)

    try:
        if args.rules:
            with open(args.rules, encoding="utf-8") as f:
                rules = json.load(f)
            trace.note("rules reused from " + rel(args.rules))
        else:
            rules = rules_step.extract(["briefs/" + os.path.basename(b) for b in args.briefs],
                                       "tool_api/" + os.path.basename(args.tools), ws, trace=trace)
        keep("rules.json", rules)
        trace.note("rules: %d rules (%d fit no slot and go to the prompts only), %d endings, %d bands" % (
            len(rules["rules"]), len(rules.get("unmapped") or []), len(rules["endings"]), len(rules["bands"])))
        done("1 rules form")

        prompts = prompts_step.write_prompts(rules, ws, trace=trace)
        keep("prompts.json", prompts)
        done("2 step texts")

        warnings = []
        tools = tools_gen.generate_tools(tools_gen.load_spec(args.tools), context_vars=tuple(rules["call_variables"]), warnings=warnings)
        keep("tools.json", tools)
        workflow, report = compile_step.compile_workflow(rules, prompts, tools)
        keep("workflow.json", workflow)
        keep("limits_report.json", report)
        for warning in warnings:
            trace.note("tools_gen warning: " + warning)
        for entry in report:
            trace.note("limit %(limit)s = %(n)s: %(how)s (%(where)s)" % entry)
        done("3 compile")
        found = save_workflow.problems_in(workflow)
        if found:
            raise ValueError("the compiled workflow has problems:\n- " + "\n- ".join(found))
        if args.no_save:
            trace.note("--no-save: compiled only, nothing was sent to Freya")
            status = "ok"
            return

        ids = save_workflow.save(workflow)
        trace.workflow_created(ids["workflow_id"], agent_id=ids["agent_id"], workflow=workflow)
        sys.path.insert(0, KIT)
        import freya
        if args.chat_turn:
            try:
                reply = chat_turn(freya, ids["workflow_id"], rules)
                trace.self_test("one chat turn: the workflow starts and answers", bool(reply.strip()), reply[:200])
            except RuntimeError as error:
                # The chat route timed out (504) on a workflow that then passed its practice run, so it proves little.
                trace.self_test("one chat turn: the workflow starts and answers", False, "no answer: " + str(error)[:160])
                print("chat turn gave no answer: %s" % str(error)[:160], flush=True)
        done("4 save")
        build_seconds = time.time() - started
        trace.note("build time without practice customers: %.0f s" % build_seconds)

        if args.test_brief:
            test = run_practice(ids["agent_id"], args.test_brief, args.reps, os.path.join(run_dir, "test.json"))
            failed = [r["id"] for r in test["final"] if not r["pass"]]
            trace.self_test("practice customers (%s, %d run(s) each)" % (args.test_brief, args.reps), not failed,
                            "%s of %s passed%s" % (test["passed"], test["total"], "; failed: " + ", ".join(failed) if failed else ""))
            done("5 practice customers")

        export = os.path.join(ROOT, "submission", label + ".workflow.json")
        freya.export_workflow(ids["workflow_id"], export)
        trace.note("exported to " + rel(export))
        status = "ok"
        print("build time without practice customers: %.0f s" % build_seconds, flush=True)
    except Exception as error:
        trace.note("build failed: %s: %s" % (type(error).__name__, str(error)[:1500]))
        raise
    finally:
        shutil.rmtree(ws, ignore_errors=True)
        print("trace: %s (%s, %.0f s in total)" % (trace.finish(status), status, time.time() - started), flush=True)


if __name__ == "__main__":
    main()
