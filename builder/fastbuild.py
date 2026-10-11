"""The fast builder: brief files + tool API file in, phone agent on Freya out.

    python builder/with_env.py python builder/fastbuild.py <brief.md> [<more briefs>...] --tools <openapi.json> [--test-brief A] [--reps 3]

1. rules.py    a model fills the fixed rules form from the brief          (one model call, checked by code)
2. prompts.py  a model writes the text of each conversation step          (short model calls, in parallel)
3. compile.py  code builds the whole workflow from the rules and prompts  (no model)
4. save, one chat turn, optional practice customers, export, trace        (no model)

Testing while developing: ONE run per customer (--reps 1, the default) on the 10 to 13 practice customers.
Three runs each (--reps 3) only as the last check before a submission. A test never holds the build:
after --test-seconds it is given up and its simulation is cancelled (builder/simulations.py), a run that
shows a platform outage (no tool call in any call) is not counted and is repeated, and requests wait out
a short outage with pauses of 10, 20 and 40 seconds (builder/freya_retry.py).

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
import threading
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


def run_practice(agent_id, test_brief, reps, out_path, max_seconds, style=""):
    """Run the kit's practice customers and show their lines as they come. Returns the result dict of run_cases.py.

    Raises TimeoutError when the run is not finished after `max_seconds` (a simulation can stay 'running' on the
    platform for hours after its calls are over), so a build never waits longer than that for its test.
    """
    # the kit's run_cases.py, started through freya_retry.py so that a short outage of the platform does not end it
    command = [sys.executable, "-u", os.path.join(ROOT, "builder", "freya_retry.py"), agent_id, "--out", out_path, "--reps", str(reps)]
    if test_brief != "all":
        command += ["--brief", test_brief]
    if style:
        command += ["--style", style]
    if os.path.exists(out_path):
        os.remove(out_path)  # an older result must never be read as the result of this run
    run = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           encoding="utf-8", errors="replace")
    lines = []

    def show():
        for line in run.stdout:
            lines.append(line)
            print("   " + line.rstrip(), flush=True)

    reader = threading.Thread(target=show, daemon=True)
    reader.start()
    try:
        run.wait(timeout=max_seconds)
    except subprocess.TimeoutExpired:
        run.kill()
        raise TimeoutError("not finished after %d s; last line: %s" % (max_seconds, (lines[-1].strip() if lines else "(no output)")[:200]))
    reader.join(5)
    if run.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError("the practice run failed: " + "".join(lines)[-800:])
    with open(out_path, encoding="utf-8") as f:
        return json.load(f)


def is_outage(test):
    """A run in which no customer got a single tool call and no agent turn was timed tested the platform, not the agent."""
    rows = [r for rep in (test.get("all_rows") or [test.get("rows") or []]) for r in rep]
    return bool(rows) and all(not r.get("tools") and not r.get("slowest_turn_s") for r in rows)


def free_the_workspace(agent_id, trace):
    """After a test that did not come back: stop its simulation so that the next test does not wait behind it."""
    try:
        import simulations
        for sim_id, stopped, text in simulations.cancel_unfinished(agent_id):
            trace.note("simulation %s %s (%s)" % (sim_id, "cancelled" if stopped else "could NOT be cancelled", text))
            print("simulation %s: %s" % (sim_id, "cancelled" if stopped else "NOT cancelled, " + text), flush=True)
    except Exception as error:  # looking after the platform must never cost the build
        trace.note("could not look for unfinished simulations: %s" % str(error)[:200])


def main(argv=None):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import compile as compile_step
    import freya_retry
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
    parser.add_argument("--style", default="", help="how the practice customers talk (see kit/tools/run_cases.py --style)")
    parser.add_argument("--test-seconds", type=int, default=900,
                        help="stop waiting for the practice customers after this long; the build still exports and ends")
    parser.add_argument("--outage-reruns", type=int, default=2,
                        help="how often to repeat a test that shows an outage of the platform (no tool call, no timed turn)")
    parser.add_argument("--chat-turn", action="store_true", help="also send one chat turn after saving (can time out)")
    parser.add_argument("--prompts", choices=("verbatim", "model"), default="verbatim",
                        help="verbatim: the brief's rules word for word (same every build). model: a model rewrites them")
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
        import runtime_facts
        for line in runtime_facts.HiddenBriefAdapter(rules).problems():
            trace.note("facts engine: " + line)
            print("facts engine: " + line, flush=True)
        done("1 rules form")

        if args.prompts == "model":
            prompts = prompts_step.write_prompts(rules, ws, trace=trace)
        else:
            prompts = prompts_step.verbatim_prompts(rules)
            trace.note("step texts: fixed instructions plus the brief's rules word for word (no model call)")
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

        freya_retry.install()  # saving and exporting wait out a short outage of the platform
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
                # A 504 here does not mean the workflow is broken: the turn may still be running on the platform.
                trace.self_test("one chat turn: the workflow starts and answers", False, "no answer (proves nothing about the agent): " + str(error)[:160])
                print("chat turn gave no answer: %s" % str(error)[:160], flush=True)
        done("4 save")
        build_seconds = time.time() - started
        trace.note("build time without practice customers: %.0f s" % build_seconds)

        if args.test_brief:
            name = "practice customers (%s, %d run(s) each%s)" % (args.test_brief, args.reps, ", style " + args.style if args.style else "")
            for attempt in range(1 + max(0, args.outage_reruns)):
                try:
                    test = run_practice(ids["agent_id"], args.test_brief, args.reps, os.path.join(run_dir, "test.json"),
                                        args.test_seconds, args.style)
                except (TimeoutError, RuntimeError) as error:
                    # The agent is saved and can still be exported: a test that hangs or cannot start must not cost the build.
                    trace.self_test(name, False, "no result: " + str(error))
                    print("practice customers: no result, %s" % error, flush=True)
                    free_the_workspace(ids["agent_id"], trace)
                    break
                if is_outage(test) and attempt < args.outage_reruns:
                    # Says nothing about the agent: keep the agent as it is, wait, run the same test again.
                    trace.note("practice run %d shows a platform OUTAGE (no tool call, no timed turn): not counted, repeated in 60 s" % (attempt + 1))
                    print("OUTAGE: no customer got a tool call. The agent is unchanged; same test again in 60 s.", flush=True)
                    time.sleep(60)
                    continue
                failed = [r["id"] for r in test["final"] if not r["pass"]]
                outage = " (platform OUTAGE: no tool call in any call, says nothing about the agent)" if is_outage(test) else ""
                trace.self_test(name, not failed, "%s of %s passed%s%s" % (
                    test["passed"], test["total"], "; failed: " + ", ".join(failed) if failed else "", outage))
                break
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
