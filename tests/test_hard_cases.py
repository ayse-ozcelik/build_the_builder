"""Four hard customers (HARD-01 .. HARD-04) played through the generated agent code against a stand-in bank.

    python tests\\test_hard_cases.py

No platform, no model, like test_brief_b_cases.py: the tool calls of each customer (the right ones and the
wrong ones a model makes under pressure) go through the hooks the builder generates, a stand-in bank keeps
the record, and the kit's scorer reads it.

    HARD-01  refuses the lowest amount too and says he lost his job: no promise; support team or a note
    HARD-02  insists on a Sunday, then on a bank holiday: nothing reaches the bank until the day is a business day
    HARD-03  the promise service fails on the first calls: as many tries as the BRIEF allows, never one more,
             and never "recorded" before the bank said so
    HARD-04  three products: one paid today, one at its lowest amount next week, one refused

The customers are made up here on the accounts of the practice set; what the record must look like is
worked out from Brief A and Brief B. How many tries a failed promise gets is the brief's number
(rules: promise.failure_limit), so HARD-03 is played twice: with this brief's number and with another one.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "builder"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compile as compile_step
import prompts as prompts_step
import tools_gen
from test_brief_b_cases import Call
from test_guards import build, load

FAILED = []


def check(name, ok, detail=""):
    print("%s  %s%s" % ("OK  " if ok else "FAIL", name, ("   " + str(detail)) if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


def rebuilt(rules, **promise):
    """The same brief with other values in its promise rules, compiled again."""
    other = json.loads(json.dumps(rules))
    other["promise"].update(promise)
    tools = tools_gen.generate_tools(tools_gen.load_spec(os.path.join(ROOT, "kit", "tools", "openapi.json")),
                                     context_vars=tuple(other["call_variables"]))
    return other, compile_step.compile_workflow(other, prompts_step.verbatim_prompts(other), tools)[0]


def customer(case, **expected):
    """A practice customer's account with another story: only the expected record changes."""
    made = json.loads(json.dumps(case))
    made["expected"] = dict({"outcome": None, "reason": None, "promises": [], "notes": [], "callbacks": [], "transfers": []}, **expected)
    return made


def step(call):
    """Where the routes of the negotiation lead now (None = stay). Recording failed for good comes first."""
    if call.v.get("promise_dead") == "yes" and "close_service_error" in [t["target_node"] for t in call.wf["nodes"]["collect"]["transitions"]]:
        return "close_service_error"
    return call.step()


def said_recorded(data):
    return "IS recorded" in str(data.get("what_to_do_next", "")) or data.get("ok") is True


def main():
    cases = {c["id"]: c for c in load("kit/public-test-set.json")}
    rules, wf = build("build/fast-brief-b/rules.json")
    rules_a, wf_a = build("build/fast-brief-a/rules.json")
    limit = rules["promise"]["failure_limit"]
    promise = lambda pid, amount, date: {"product_id": pid, "amount": amount, "date": date}
    note = lambda pid, reason: {"product_id": pid, "reason": reason}

    # ---------------- HARD-01: refuses the lowest amount, "I lost my job, there is no money"
    # one product (Brief A rule 32): declines the support team -> no promise, closed as hardship. PUB-05's own record.
    c = Call(rules_a, wf_a, cases["PUB-05"])
    low = float(c.v["floor_p1"])
    sent, data = c.tool("create_promise", promise("p1", low - 1, c.v["ok_dates_p1"].split(",")[0]))
    check("HARD-01 one product: an amount below the lowest amount is stopped before the bank", not sent and not said_recorded(data), data)
    sent, _ = c.tool("close_call", {"outcome": "no_promise", "reason": "hardship"})
    check("HARD-01 one product: declines support -> closed as hardship, no promise (the kit's scorer on PUB-05)",
          sent and c.score()["pass"], c.score()["failed"])
    # one product: accepts the support team
    made = customer(cases["PUB-05"], outcome="transferred", reason="support", transfers=[{"queue": "support"}])
    c = Call(rules_a, wf_a, made)
    c.tool("transfer_to_agent", {"queue": "support"})
    c.tool("close_call", {"outcome": "transferred", "reason": "support"})
    check("HARD-01 one product: accepts support -> one transfer, closed as transferred / support, no promise", c.score()["pass"], c.score()["failed"])
    # three products (Brief B rule 8): declines support -> a hardship note for every product, then the closing step
    made = customer(cases["PUB-12"], outcome="no_promise", reason="hardship", notes=[note(p, "hardship") for p in ("p1", "p2", "p3")])
    c = Call(rules, wf, made)
    facts = c.v["account_facts"]
    check("HARD-01 facts: the attempts stand behind 'and no rule says to make no attempt'", "(and no rule says to make no attempt)" in facts)
    sent, data = c.tool("create_promise", promise("p2", float(c.v["floor_p2"]) - 40, "2026-10-23"))
    check("HARD-01 three products: 'I can only pay less than the lowest amount' is stopped before the bank", not sent and not c.bank.led["promises"], data)
    c.tool("add_note", note("p1", "hardship"))
    sent, data = c.tool("close_call", {"outcome": "no_promise", "reason": "hardship"})
    check("HARD-01 three products: closing after the first note is stopped and names the two open products",
          not sent and "p2, p3" in data.get("what_to_do_next", ""), data)
    c.tool("add_note", note("p2", "hardship"))
    check("HARD-01 three products: still in the negotiation with one product open", step(c) is None, step(c))
    c.tool("add_note", note("p3", "hardship"))
    check("HARD-01 three products: after the third note the system moves to the closing step without a promise", step(c) == "settled_close", step(c))
    check("HARD-01 three products: the closing step is told the reason of every note",
          c.v.get("settle_summary", "").count("reason hardship") == 3, c.v.get("settle_summary"))
    c.tool("close_call", {"outcome": "no_promise", "reason": "hardship"}, node="settled_close")
    check("HARD-01 three products: no promise, three hardship notes, closed as hardship", c.score()["pass"], c.score()["failed"])
    # three products: accepts the support team -> the transfer closes the call, no notes at all (Brief B rule 10)
    made = customer(cases["PUB-12"], outcome="transferred", reason="support", transfers=[{"queue": "support"}])
    c = Call(rules, wf, made)
    c.tool("transfer_to_agent", {"queue": "support"})
    check("HARD-01 three products: after the transfer the system moves to the transfer's closing step", step(c) == "transfer_close", step(c))
    c.tool("close_call", {"outcome": "transferred", "reason": "support"}, node="transfer_close")
    check("HARD-01 three products: accepts support -> closed as transferred / support with no note and no promise", c.score()["pass"], c.score()["failed"])

    # ---------------- HARD-02: insists on a Sunday, then on a bank holiday
    # PUB-13's card on 14 October: Sunday 25 October is followed by the holiday of 26 October, so the next day is the 27th.
    made = customer(cases["PUB-13"], outcome="promise", promises=[promise("p1", 300.0, "2026-10-27"), promise("p2", 900.0, "2026-10-23")])
    c = Call(rules, wf, made)
    facts = c.v["account_facts"]
    check("HARD-02 facts: the Sunday and the holiday both point to Tuesday 27 October",
          "Sunday 25 October 2026 [2026-10-25]: NOT possible, not a business day -> offer the next business day Tuesday 27 October 2026 [2026-10-27]" in facts
          and "Monday 26 October 2026 [2026-10-26]: NOT possible, a bank holiday -> offer the next business day Tuesday 27 October 2026 [2026-10-27]" in facts)
    sent, data = c.tool("create_promise", promise("p1", 300, "2026-10-25"))
    check("HARD-02 correction 1: the Sunday is stopped, nothing is recorded, and the result names 2026-10-27",
          not sent and not said_recorded(data) and "The next date that can be recorded for it is 2026-10-27" in data.get("what_to_do_next", ""), data)
    sent, data = c.tool("create_promise", promise("p1", 300, "26 October 2026"))
    check("HARD-02 correction 2: the holiday (said in words) is stopped and the result names 2026-10-27 again",
          not sent and "The next date that can be recorded for it is 2026-10-27" in data.get("what_to_do_next", ""), data)
    check("HARD-02: after two corrections nothing has reached the bank and no try is counted as a bank failure",
          not c.bank.led["promises"] and not c.v.get("pf_p1") and int(c.v.get("guard_blocks") or 0) == 2, c.v.get("guard_blocks"))
    sent, data = c.tool("create_promise", promise("p1", 300, "2026-10-27"))
    check("HARD-02: the business day is sent and recorded on the third call", sent and data.get("ok") is True, data)
    sent, data = c.tool("create_promise", promise("p2", 900, "2026-10-27"))
    check("HARD-02: the same day is after the loan's deadline; the result names its latest date 2026-10-23",
          not sent and "The latest date that can be recorded for it is 2026-10-23" in data.get("what_to_do_next", ""), data)
    c.tool("create_promise", promise("p2", 900, "2026-10-23"))
    c.tool("close_call", {"outcome": "promise"}, node="promise_done")
    check("HARD-02: only business days are in the bank's record", c.score()["pass"], c.score()["failed"])
    # another calendar: the same customer with Sunday as a business day
    other = json.loads(json.dumps(rules))
    other["calendar"]["business_weekdays"] = [0, 1, 2, 3, 4, 6]
    c = Call(other, wf, made)
    check("HARD-02 another calendar (Sunday a business day): the Sunday is sent", c.tool("create_promise", promise("p1", 300, "2026-10-25"))[0])

    # ---------------- HARD-03: the promise service fails on the first calls
    # this brief (A rule 37, B rule 11): `limit` tries. Three products, the loan fails twice.
    made = customer(cases["PUB-13"], outcome="promise", promises=[promise("p1", 300.0, "2026-10-30")], notes=[note("p2", "service_error")])
    c = Call(rules, wf, made, fail_first_promise_of=["p2"] * limit)
    c.tool("create_promise", promise("p1", 300, "2026-10-30"))
    for n in range(1, limit + 1):
        sent, data = c.tool("create_promise", promise("p2", 900, "2026-10-23"))
        check("HARD-03 try %d of %d fails: the result says nothing was recorded, and the call stays in the negotiation" % (n, limit),
              sent and not said_recorded(data) and "Nothing was recorded" in data.get("what_to_do_next", "")
              and data.get("failed_tries_for_this_product") == n and step(c) is None, data)
    sent, data = c.tool("create_promise", promise("p2", 900, "2026-10-23"))
    check("HARD-03 this brief: one try more than the brief allows is stopped before the bank (the bank would have recorded it)",
          not sent and len(c.bank.led["promises"]) == 1 and "Do not try again" in data.get("what_to_do_next", "")
          and "Correct it first" not in data.get("what_to_do_next", ""), data)
    c.tool("add_note", note("p2", "service_error"))
    c.tool("close_call", {"outcome": "promise"}, node="promise_done")
    check("HARD-03 this brief: the recorded promise stays, the failed product gets its note", c.score()["pass"], c.score()["failed"])
    # one product, this brief: the system closes by itself after the last allowed try
    c = Call(rules_a, wf_a, cases["PUB-10"], fail_first_promise_of=["p1"] * limit)
    for _ in range(limit):
        _, data = c.tool("create_promise", promise("p1", 365, "2026-10-16"))
    check("HARD-03 one product, this brief: after %d failures the system goes to its own service-error closing" % limit,
          step(c) == "close_service_error" and not said_recorded(data) and not c.bank.led["promises"], step(c))
    c = Call(rules_a, wf_a, cases["PUB-10"], fail_first_promise_of=["p1"])
    _, data = c.tool("create_promise", promise("p1", 365, "2026-10-16"))
    again = c.tool("create_promise", promise("p1", 365, "2026-10-16"))[1]
    c.tool("close_call", {"outcome": "promise"}, node="promise_done")
    check("HARD-03 one product: one failure, then recorded on the second try (the kit's scorer on PUB-10)",
          not said_recorded(data) and said_recorded(again) and c.score()["pass"], c.score()["failed"])
    # a brief that allows three tries: fails twice (500, 504), recorded on the third call
    for name, base, case, product in (("three products", rules, made, ("p2", 900, "2026-10-23")),
                                      ("one product", rules_a, cases["PUB-10"], ("p1", 365, "2026-10-16"))):
        three, wf3 = rebuilt(base, failure_limit=3)
        c = Call(three, wf3, case, fail_first_promise_of=[product[0]] * 2)
        seen = [c.tool("create_promise", promise(*product))[1] for _ in range(2)]
        check("HARD-03 %s, a brief with 3 tries: two failures say 'not recorded' and leave the call in the negotiation" % name,
              not any(said_recorded(d) for d in seen) and seen[-1].get("tries_allowed_for_one_product") == 3 and step(c) is None, seen[-1])
        sent, data = c.tool("create_promise", promise(*product))
        check("HARD-03 %s, a brief with 3 tries: the third call reaches the bank and only then the result says recorded" % name,
              sent and said_recorded(data) and [p["product_id"] for p in c.bank.led["promises"]] == [product[0]], data)
        check("HARD-03 %s, a brief with 3 tries: exactly three calls went to the bank for it" % name,
              sum(1 for x in c.bank.led["calls"] if x["tool"] == "create_promise") == 3)
    check("HARD-03: a tool that writes is never repeated by the platform itself (a repeated write could record twice)",
          all(t["config"]["maxRetries"] == 0 for n in wf["nodes"].values() for t in n.get("tools") or []
              if t["function"]["name"] in ("create_promise", "add_note", "close_call", "transfer_to_agent")))

    # ---------------- HARD-04: card paid today, loan at its lowest amount next week, car loan refused
    made = customer(cases["PUB-12"], outcome="promise", notes=[note("p3", "refused")],
                    promises=[promise("p1", 510.0, "2026-10-14"), promise("p2", 440.0, "2026-10-21")])
    c = Call(rules, wf, made)
    check("HARD-04 facts: the loan's lowest amount (440.00) and next Wednesday (2026-10-21) are given by code",
          "FLOOR amount: 440.00" in c.v["account_facts"] and "next Wednesday = Wednesday 21 October 2026 [2026-10-21]" in c.v["account_facts"])
    sent, data = c.tool("create_promise", promise("p1", 510, "2026-10-14"))
    check("HARD-04 p1: the full amount today is recorded; the result names p2 and p3 as open",
          sent and data.get("ok") is True and "p2, p3" in data.get("what_to_do_next", ""), data)
    sent, _ = c.tool("create_promise", promise("p2", 439.99, "2026-10-21"))
    check("HARD-04 p2 edge: one cent below the lowest amount is stopped", not sent)
    sent, _ = c.tool("create_promise", promise("p2", 440, "2026-10-28"))
    check("HARD-04 p2 edge: 'next week' pushed past the loan's deadline is stopped", not sent)
    sent, data = c.tool("create_promise", promise("p2", "440.00", "21 October 2026"))
    check("HARD-04 p2: the lowest amount next Wednesday is recorded; only p3 is open",
          sent and data.get("ok") is True and data.get("what_to_do_next", "").count("p3") >= 1 and "p2, p3" not in data.get("what_to_do_next", ""), data)
    sent, data = c.tool("close_call", {"outcome": "promise"})
    check("HARD-04 edge: closing with the refused product still without its note is stopped", not sent and "p3" in data.get("what_to_do_next", ""), data)
    sent, _ = c.tool("add_note", note("p2", "refused"))
    check("HARD-04 edge: a 'refused' note for a product that has a promise is stopped", not sent)
    check("HARD-04: still in the negotiation", step(c) is None, step(c))
    sent, _ = c.tool("add_note", note("p3", "refused"))
    check("HARD-04 p3: the 'refused' note is sent, and the system moves to the recorded step", sent and step(c) == "promise_done", step(c))
    check("HARD-04: the closing step is told what each product got",
          c.v.get("settle_summary") == "p1: a recorded promise; p2: a recorded promise; p3: a note (reason refused)", c.v.get("settle_summary"))
    c.tool("close_call", {"outcome": "promise"}, node="promise_done")
    writes = [x["tool"] for x in c.bank.led["calls"] if x["tool"] in ("create_promise", "add_note")]
    check("HARD-04: exactly two create_promise and one add_note reached the bank", writes == ["create_promise", "create_promise", "add_note"], writes)
    check("HARD-04: the kit's scorer passes the bank record", c.score()["pass"], c.score()["failed"])
    # the same customer when the model writes the note first
    c = Call(rules, wf, made)
    c.tool("add_note", note("p3", "refused"))
    c.tool("create_promise", promise("p2", 440, "2026-10-21"))
    check("HARD-04 other order: with the card still open the call stays in the negotiation", step(c) is None, step(c))
    c.tool("create_promise", promise("p1", 510, "2026-10-14"))
    c.tool("close_call", {"outcome": "promise"}, node="promise_done")
    check("HARD-04 other order (note first): the record is the same", step(c) == "promise_done" and c.score()["pass"], c.score()["failed"])

    print("\n" + ("ALL CHECKS PASSED" if not FAILED else "%d CHECK(S) FAILED: %s" % (len(FAILED), ", ".join(FAILED))))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
