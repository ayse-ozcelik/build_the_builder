"""The checks that run inside the phone agent before a promise or a note is sent to the bank.

    python tests\\test_guards.py

Compiles the workflow of Brief A+B from its saved rules file, then runs the generated
code (the facts step and the tool hooks) here, on the practice customers' accounts,
the way the platform runs it. No call to Freya, to the bank or to a model.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "builder"))
import compile as compile_step
import prompts as prompts_step
import tools_gen

FAILED = []


def check(name, ok, detail=""):
    print("%s  %s%s" % ("OK  " if ok else "FAIL", name, ("   " + str(detail)) if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


def load(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return json.load(f)


def build(rules_path):
    rules = load(rules_path)
    tools = tools_gen.generate_tools(tools_gen.load_spec(os.path.join(ROOT, "kit", "tools", "openapi.json")),
                                     context_vars=tuple(rules["call_variables"]))
    workflow, _ = compile_step.compile_workflow(rules, prompts_step.verbatim_prompts(rules), tools)
    return rules, workflow


def start(rules, case):
    """The call's variables right after the account was loaded."""
    v = dict(case["variables"], customer_id="c1")
    v["products"] = [dict(p, product_id="p%d" % (i + 1)) for i, p in enumerate(case["products"])]
    exec(compile_step.facts_code(rules), {"vars": v})
    return v


def call(workflow, tool_name, v, body, bank_answer, node="collect"):
    """Run one tool call the way the platform does: pre-hook, the bank, post-hook. Returns (sent, result data)."""
    tool = [t for t in workflow["nodes"][node]["tools"] if t["function"]["name"] == tool_name][0]
    config = {"url": "https://bank/" + tool_name, "method": "POST", "body": dict(body, customer_id="c1")}
    exec(tool["pre_process_code"], {"vars": v, "config": config})
    sent = not config["url"].endswith("/get_account")
    result = {"header": {}, "data": dict(bank_answer) if sent else {"ok": True, "products": v["products"]}}
    exec(tool["post_process_code"], {"vars": v, "result": result})
    return sent, result["data"]


def main():
    cases = {c["id"]: c for c in load("kit/public-test-set.json")}
    rules, wf = build("build/fast-brief-b/rules.json")
    ok = {"ok": True, "promise_id": "pr1"}

    # PUB-13: card (early band, deadline 2 November) and loan (late band, deadline 23 October), call on 14 October
    v = start(rules, cases["PUB-13"])
    check("loan: a date after its own deadline is not sent", not call(wf, "create_promise", v, {"product_id": "p2", "amount": 900, "date": "2026-10-30"}, ok)[0])
    sent, data = call(wf, "create_promise", v, {"product_id": "p1", "amount": 300, "date": "2026-10-30"}, ok)
    check("card: the same date is inside its deadline and is sent", sent and str(data.get("ok")) == "True", data)
    check("a stopped call is not counted as a failed try", not v.get("pf_p2"), v.get("pf_p2"))
    sent, data = call(wf, "create_promise", v, {"product_id": "p2", "amount": 900, "date": "2026-10-23"}, ok)
    check("loan: its deadline is sent", sent)
    check("both products settled, with a promise", v.get("all_settled") == "with_promise", v.get("all_settled"))

    v = start(rules, cases["PUB-13"])
    for date, why in (("2026-10-17", "a Saturday"), ("2026-10-26", "a bank holiday"), ("2026-10-13", "yesterday")):
        sent, data = call(wf, "create_promise", v, {"product_id": "p1", "amount": 300, "date": date}, ok)
        check("%s is not sent, and the model is told nothing was recorded" % why,
              not sent and str(data.get("ok")) == "False" and "Nothing was recorded" in data.get("what_to_do_next", ""), data)
    sent, _ = call(wf, "create_promise", v, {"product_id": "p1", "amount": 300, "date": "2026-10-17"}, {"ok": False, "error": "x"})
    check("after three stopped calls the check lets a call through (a call never hangs on it)", sent)

    v = start(rules, cases["PUB-13"])
    check("an amount below the lowest amount is not sent", not call(wf, "create_promise", v, {"product_id": "p1", "amount": 50, "date": "2026-10-15"}, ok)[0])
    check("an amount above the full amount is not sent", not call(wf, "create_promise", v, {"product_id": "p1", "amount": 301, "date": "2026-10-15"}, ok)[0])
    check("the lowest amount itself is sent", call(wf, "create_promise", v, {"product_id": "p1", "amount": "90.00", "date": "2026-10-15"}, ok)[0])

    v = start(rules, cases["PUB-13"])
    sent, data = call(wf, "add_note", v, {"product_id": "p7", "reason": "refused"}, {"ok": True})
    check("a note for a product that does not exist is not sent", not sent and "p1, p2" in data.get("error", ""), data)

    # PUB-12: one promise, two refusals
    v = start(rules, cases["PUB-12"])
    call(wf, "create_promise", v, {"product_id": "p1", "amount": 510, "date": "2026-10-15"}, ok)
    check("no note for a product that already has a promise", not call(wf, "add_note", v, {"product_id": "p1", "reason": "refused"}, {"ok": True})[0])
    sent, data = call(wf, "add_note", v, {"product_id": "p2", "reason": "refused"}, {"ok": True})
    check("after the first note the result names the product that is still open", sent and "p3" in data.get("what_to_do_next", ""), data)
    check("no second note for the same product", not call(wf, "add_note", v, {"product_id": "p2", "reason": "dispute"}, {"ok": True})[0])
    call(wf, "add_note", v, {"product_id": "p3", "reason": "refused"}, {"ok": True})
    check("one promise and two notes: settled, with a promise", v.get("all_settled") == "with_promise", v.get("all_settled"))
    check("the closing step is told what was recorded, in product order",
          v.get("settle_summary") == "p1: a recorded promise; p2: a note (reason refused); p3: a note (reason refused)", v.get("settle_summary"))

    # no promise at all: only notes
    v = start(rules, cases["PUB-13"])
    call(wf, "add_note", v, {"product_id": "p1", "reason": "paid"}, {"ok": True})
    call(wf, "add_note", v, {"product_id": "p2", "reason": "dispute"}, {"ok": True})
    check("two notes and no promise: settled, without a promise", v.get("all_settled") == "without_promise", v.get("all_settled"))
    check("the summary carries each note's reason", "p1: a note (reason paid); p2: a note (reason dispute)" == v.get("settle_summary"), v.get("settle_summary"))

    # closing: not while a product is open, except after a transfer
    close = {"outcome": "no_promise", "reason": "refused"}
    for node in ("settled_close", "who"):
        v = start(rules, cases["PUB-12"])
        call(wf, "add_note", v, {"product_id": "p2", "reason": "refused"}, {"ok": True})
        sent, data = call(wf, "close_call", v, close, {"ok": True}, node=node)
        check("closing with two products still open is not sent (the %s step's close_call)" % node,
              not sent and str(data.get("ok")) == "False" and "p1, p3" in data.get("what_to_do_next", ""), data)
    call(wf, "close_call", v, close, {"ok": True}, node="who")
    check("after two stopped closings the call can be closed (it never hangs)", call(wf, "close_call", v, close, {"ok": True}, node="who")[0])
    v = start(rules, cases["PUB-12"])
    call(wf, "transfer_to_agent", v, {"queue": "restructuring"}, {"ok": True})
    check("after a transfer the call closes with products open and no notes", call(wf, "close_call", v, {"outcome": "transferred", "reason": "restructuring"}, {"ok": True}, node="transfer_close")[0])
    v = start(rules, cases["PUB-12"])
    for pid in ("p1", "p2", "p3"):
        call(wf, "add_note", v, {"product_id": pid, "reason": "refused"}, {"ok": True})
    check("with every product noted the call closes", call(wf, "close_call", v, close, {"ok": True}, node="settled_close")[0])
    v = dict(cases["PUB-02"]["variables"], customer_id="c1")
    check("before the account is loaded (someone else on the line) closing is never stopped",
          call(wf, "close_call", v, {"outcome": "not_reached", "reason": "wrong_number"}, {"ok": True}, node="who")[0])

    # a failed promise is counted per product; a note for it settles the product
    v = start(rules, cases["PUB-13"])
    for _ in range(2):
        sent, data = call(wf, "create_promise", v, {"product_id": "p2", "amount": 900, "date": "2026-10-23"}, {"ok": False, "error": "service unavailable"})
    check("two failed tries of one product are counted", data.get("failed_tries_for_this_product") == 2, data)
    check("a failed product is still open", "p2" not in str(v.get("settled")))

    # Brief A alone: one product, no notes
    rules_a, wf_a = build("build/fast-brief-a/rules.json")
    names = lambda node: [t["function"]["name"] for t in wf_a["nodes"][node]["tools"]]
    check("Brief A: the negotiation keeps its closing tool and has no note tool", "close_call" in names("collect") and "add_note" not in names("collect"))
    check("Brief B: the negotiation has the note tool and no closing tool",
          [t["function"]["name"] for t in wf["nodes"]["collect"]["tools"]].count("close_call") == 0 and "transfer_close" in wf["nodes"])
    v = start(rules_a, cases["PUB-07"])
    check("Brief A: closing is never stopped (the brief has no notes)", call(wf_a, "close_call", v, close, {"ok": True})[0])
    v = start(rules_a, cases["PUB-07"])
    facts = v["account_facts"]
    check("one product: the attempt list ends with the stop line", "After that: NOTHING" in facts)
    sent, _ = call(wf_a, "create_promise", v, {"product_id": "wrong", "amount": cases["PUB-07"]["products"][0]["past_due"],
                                               "date": v["ok_dates_p1"].split(",")[0]}, ok)
    check("one product: the product id is filled in by code", sent and v.get("last_product") == "p1", v.get("last_product"))
    last = v["ok_dates_p1"].split(",")[-1]
    check("one product: the deadline is sent", call(wf_a, "create_promise", start(rules_a, cases["PUB-07"]),
                                                    {"product_id": "p1", "amount": cases["PUB-07"]["products"][0]["past_due"], "date": last}, ok)[0])

    print("\n" + ("ALL CHECKS PASSED" if not FAILED else "%d CHECK(S) FAILED: %s" % (len(FAILED), ", ".join(FAILED))))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
