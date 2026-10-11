"""PUB-11, PUB-12 and PUB-13 (Brief B) played through the generated agent code against a stand-in bank.

    python tests\\test_brief_b_cases.py

No platform, no model: this file plays the tool calls of each customer (the right ones, and the wrong
ones a model makes at the edges) through the hooks the builder generates, lets a stand-in bank keep the
record, and scores that record with the kit's own scorer (kit/tools/score.py, score_ledger) against the
customer's expected result in kit/public-test-set.json.

It proves the code around the model: dates and amounts are computed and checked in code, the product list
is kept in code, closing waits for the notes. It cannot prove what the model SAYS; that needs a run of
kit/tools/run_cases.py on the platform.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "builder"))
sys.path.insert(0, os.path.join(ROOT, "kit", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runtime_facts
from score import score_ledger
from test_guards import build, load, start

FAILED = []


def check(name, ok, detail=""):
    print("%s  %s%s" % ("OK  " if ok else "FAIL", name, ("   " + str(detail)) if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


class Bank:
    """Keeps the record the way the practice bank does, as far as the scorer reads it."""

    def __init__(self, products, fail_first_promise_of=()):
        self.products = products
        self.led = {"outcome": None, "reason": None, "promises": [], "notes": [], "callbacks": [], "transfers": [], "calls": []}
        self.fail = list(fail_first_promise_of)

    def serve(self, tool, body):
        if self.led["outcome"]:
            result = {"ok": False, "error": "the call is already closed"}
        elif tool == "get_account":
            result = {"ok": True, "products": self.products}
        elif tool == "create_promise":
            if body.get("product_id") in self.fail:
                self.fail.remove(body["product_id"])
                result = {"ok": False, "error": "service unavailable"}
            elif any(p["product_id"] == body.get("product_id") for p in self.led["promises"]):
                result = {"ok": False, "error": "this product already has a promise"}
            else:
                self.led["promises"].append({"product_id": body["product_id"], "amount": body["amount"], "date": body["date"]})
                result = {"ok": True, "promise_id": "pr%d" % len(self.led["promises"])}
        elif tool == "add_note":
            self.led["notes"].append({"product_id": body["product_id"], "reason": body["reason"]})
            result = {"ok": True}
        elif tool == "transfer_to_agent":
            self.led["transfers"].append({"queue": body["queue"]})
            result = {"ok": True}
        elif tool == "close_call":
            self.led["outcome"], self.led["reason"] = body.get("outcome"), body.get("reason")
            result = {"ok": True}
        else:
            result = {"ok": True}
        self.led["calls"].append({"tool": tool, "result": result})
        return result


class Call:
    """One conversation: the call's variables, the workflow's hooks and the bank."""

    def __init__(self, rules, workflow, case, **bank):
        self.wf, self.case = workflow, case
        self.v = start(rules, case)
        self.bank = Bank(self.v["products"], **bank)
        for tool in ("verify_identity", "verify_identity", "get_account"):   # what the steps before the negotiation did
            self.bank.serve(tool, {})

    def tool(self, name, body, node="collect"):
        """Returns (sent to the bank as asked, result data the model sees)."""
        definition = [t for t in self.wf["nodes"][node]["tools"] if t["function"]["name"] == name]
        if not definition:   # seen on the platform: the model can call a tool its step does not list
            definition = [t for n in self.wf["nodes"].values() for t in n.get("tools") or [] if t["function"]["name"] == name]
        config = {"url": "https://bank/v1/" + name, "method": "POST", "body": dict(body, customer_id="c1")}
        exec(definition[0]["pre_process_code"], {"vars": self.v, "config": config})
        served = config["url"].rsplit("/", 1)[-1]
        result = {"header": {}, "data": self.bank.serve(served, config["body"])}
        exec(definition[0]["post_process_code"], {"vars": self.v, "result": result})
        return served == name, result["data"]

    def step(self):
        """The step the negotiation's routes lead to now (None = stay)."""
        if self.v.get("transferred") == "yes":
            return "transfer_close"
        return {"with_promise": "promise_done", "without_promise": "settled_close"}.get(self.v.get("all_settled"))

    def score(self):
        return score_ledger(self.case, self.bank.led)


def main():
    cases = {c["id"]: c for c in load("kit/public-test-set.json")}
    rules, wf = build("build/fast-brief-b/rules.json")
    promise_close = {"outcome": "promise"}

    # ---------------- PUB-11: three products, one date for all ("this Friday")
    c = Call(rules, wf, cases["PUB-11"])
    facts = c.v["account_facts"]
    check("PUB-11 facts: 'this Friday' is given as 2026-10-16", "this Friday = Friday 16 October 2026 [2026-10-16]" in facts)
    check("PUB-11 opening: all three products are stated before the first ask",
          c.v["debt_statement"].count("past-due amount") == 3 and c.v["debt_statement"].rstrip().endswith("?"), c.v["debt_statement"])
    check("PUB-11 facts: the total is computed in code (935.00)", "TOTAL of the FULL amounts of all products: 935.00" in facts)
    for pid, amount in (("p1", 220), ("p2", 640), ("p3", 75)):
        sent, data = c.tool("create_promise", {"product_id": pid, "amount": amount, "date": "2026-10-16"})
        check("PUB-11 %s: promise sent" % pid, sent and data.get("ok") is True, data)
        if pid != "p3":
            check("PUB-11 after %s: the result names the products still open, the call stays in the negotiation" % pid,
                  "Still open" in data.get("what_to_do_next", "") and c.step() is None, data.get("what_to_do_next"))
    check("PUB-11: after the third promise the system moves to the recorded step", c.step() == "promise_done", c.step())
    c.tool("close_call", promise_close, node="promise_done")
    check("PUB-11: the kit's scorer passes the bank record", c.score()["pass"], c.score()["failed"])

    # PUB-11 at the edge: the model closes after the first promise (seen on the platform when it was slow)
    c = Call(rules, wf, cases["PUB-11"])
    c.tool("create_promise", {"product_id": "p1", "amount": 220, "date": "2026-10-16"})
    sent, data = c.tool("close_call", promise_close)
    check("PUB-11 edge: closing with two products open is stopped and names them", not sent and "p2, p3" in data.get("what_to_do_next", ""), data)
    sent, data = c.tool("create_promise", {"product_id": "p1", "amount": 220, "date": "2026-10-16"})
    check("PUB-11 edge: a second promise for the same product is refused by the bank and not counted as a failed try",
          data.get("ok") is False and not c.v.get("pf_p1") and len(c.bank.led["promises"]) == 1, data)
    c.tool("create_promise", {"product_id": "p2", "amount": 640, "date": "2026-10-16"})
    c.tool("create_promise", {"product_id": "p3", "amount": 75, "date": "2026-10-16"})
    c.tool("close_call", promise_close, node="promise_done")
    check("PUB-11 edge: the record is right in the end", c.score()["pass"], c.score()["failed"])

    # ---------------- PUB-12: promise on the card, the two loans refused
    c = Call(rules, wf, cases["PUB-12"])
    facts = c.v["account_facts"].split("- Product: ")
    loan, car = facts[2], facts[3]
    check("PUB-12 facts: tomorrow is 2026-10-15", "tomorrow = Thursday 15 October 2026 [2026-10-15]" in c.v["account_facts"])
    check("PUB-12 loan (16 days late): 2 attempts, the second is the lowest amount on its deadline",
          "PERSUASION ATTEMPTS ALLOWED: 2" in loan and "Attempt 2 proposal: payment on Friday 23 October 2026 [2026-10-23] of the FLOOR amount 440.00" in loan, loan[:400])
    check("PUB-12 car loan (7 days late): 1 attempt, then the stop line, and no second proposal",
          "PERSUASION ATTEMPTS ALLOWED: 1" in car and "Attempt 2" not in car.split("IF THE CUSTOMER")[0]
          and car.index("Attempt 1 proposal") < car.index("After that: NOTHING"), car[:500])
    check("PUB-12 car loan: its script stops at the second refusal", "refusal 2 -> STOP" in car)
    check("PUB-12 car loan: the floor offer stands behind its condition", 'NEVER after a plain "no"' in car)
    sent, _ = c.tool("create_promise", {"product_id": "p1", "amount": 510, "date": "2026-10-15"})
    sent2, data = c.tool("close_call", promise_close)
    check("PUB-12 edge: closing before the two notes is stopped", sent and not sent2 and "p2, p3" in data.get("what_to_do_next", ""), data)
    sent, data = c.tool("add_note", {"product_id": "p1", "reason": "refused"})
    check("PUB-12 edge: a note for the product that has the promise is stopped", not sent, data)
    c.tool("add_note", {"product_id": "p2", "reason": "refused"})
    sent, _ = c.tool("add_note", {"product_id": "p2", "reason": "refused"})
    check("PUB-12 edge: a second note for the same product is stopped", not sent)
    check("PUB-12: still in the negotiation with one product open", c.step() is None)
    c.tool("add_note", {"product_id": "p3", "reason": "refused"})
    check("PUB-12: after the last note the system moves to the recorded step", c.step() == "promise_done", c.step())
    c.tool("close_call", promise_close, node="promise_done")
    check("PUB-12: exactly one promise and two 'refused' notes in the record", len(c.bank.led["promises"]) == 1 and len(c.bank.led["notes"]) == 2, c.bank.led)
    check("PUB-12: the kit's scorer passes the bank record", c.score()["pass"], c.score()["failed"])

    # ---------------- PUB-13: one date for both, but the loan's deadline is earlier
    c = Call(rules, wf, cases["PUB-13"])
    facts = c.v["account_facts"]
    check("PUB-13 facts: the card's deadline is 2 November, the loan's 23 October",
          "DEADLINE (latest date that can be recorded): Monday 2 November 2026 [2026-11-02]" in facts
          and "DEADLINE (latest date that can be recorded): Friday 23 October 2026 [2026-10-23]" in facts)
    check("PUB-13 facts: 30 October is marked as too late for the loan only",
          "- Friday 30 October 2026 [2026-10-30]: OK except for: personal loan (its latest date is Friday 23 October 2026 [2026-10-23])" in facts)
    check("PUB-13 facts: the holiday of 26 October is not offered", "Monday 26 October 2026 [2026-10-26]: NOT possible, a bank holiday" in facts)
    sent, data = c.tool("create_promise", {"product_id": "p2", "amount": 900, "date": "2026-10-30"})
    check("PUB-13 edge: the loan on 30 October is stopped before the bank", not sent and not c.bank.led["promises"], data)
    sent, _ = c.tool("create_promise", {"product_id": "p1", "amount": 300, "date": "30 October 2026"})
    check("PUB-13: the card on 30 October is sent (date said in words is turned into 2026-10-30 by code)",
          sent and c.bank.led["promises"][-1]["date"] == "2026-10-30", c.bank.led["promises"])
    sent, _ = c.tool("create_promise", {"product_id": "p2", "amount": 900, "date": "2026-10-23"})
    check("PUB-13: the loan on its own deadline is sent", sent)
    c.tool("close_call", promise_close, node="promise_done")
    check("PUB-13: the kit's scorer passes the bank record", c.score()["pass"], c.score()["failed"])

    # PUB-13 at the edges: bank failure on one product, amounts, days that are not business days
    c = Call(rules, wf, cases["PUB-13"], fail_first_promise_of=["p2", "p2"])
    c.tool("create_promise", {"product_id": "p1", "amount": 300, "date": "2026-10-30"})
    c.tool("create_promise", {"product_id": "p2", "amount": 900, "date": "2026-10-23"})
    sent, data = c.tool("create_promise", {"product_id": "p2", "amount": 900, "date": "2026-10-23"})
    check("PUB-13 edge: two bank failures on the loan are counted for that product only",
          data.get("failed_tries_for_this_product") == 2 and not c.v.get("pf_p1") and c.step() is None, data)
    c.tool("add_note", {"product_id": "p2", "reason": "service_error"})
    check("PUB-13 edge: the recorded promise stays and the note settles the failed product", c.step() == "promise_done"
          and len(c.bank.led["promises"]) == 1 and c.bank.led["notes"] == [{"product_id": "p2", "reason": "service_error"}], c.bank.led)
    c = Call(rules, wf, cases["PUB-13"])
    for body, what in (({"product_id": "p1", "amount": 89.99, "date": "2026-10-15"}, "an amount below the card's lowest amount"),
                       ({"product_id": "p2", "amount": 900.01, "date": "2026-10-15"}, "an amount above the loan's full amount"),
                       ({"product_id": "p1", "amount": 300, "date": "2026-10-24"}, "a Saturday")):
        sent, _ = c.tool("create_promise", body)
        check("PUB-13 edge: %s is stopped before the bank" % what, not sent)
    check("PUB-13 edge: nothing reached the bank's record", not c.bank.led["promises"])
    c = Call(rules, wf, cases["PUB-13"])
    c.tool("create_promise", {"product_id": "p1", "amount": 300, "date": "2026-10-30"})
    c.tool("transfer_to_agent", {"queue": "restructuring"})
    sent, _ = c.tool("close_call", {"outcome": "transferred", "reason": "restructuring"}, node="transfer_close")
    check("PUB-13 edge (Brief B rule 10): a transfer closes the call with the loan open, the promise stays and no note is written",
          c.step() == "transfer_close" and sent and not c.bank.led["notes"] and len(c.bank.led["promises"]) == 1
          and c.bank.led["calls"][-1]["tool"] == "close_call", c.bank.led)

    # ---------------- another calendar day and another brief's values arrive only through the adapter
    adapter = runtime_facts.HiddenBriefAdapter(rules)
    seen = adapter.preview("2026-10-23T11:00", start(rules, cases["PUB-13"])["products"])
    check("on Friday 23 October, 'this Friday' is today and attempt 1 moves to the last business day of next week",
          "Attempt 1 proposal: payment on Friday 30 October 2026 [2026-10-30]" in seen["account_facts"], seen["account_facts"][:900])
    other = json.loads(json.dumps(rules))
    other["calendar"] = {"business_weekdays": [0, 1, 2, 3, 4, 5], "holidays": ["2026-10-16"]}
    other["bands"][0]["deadline"] = {"type": "business_days_after_today", "n": 3}
    seen = runtime_facts.HiddenBriefAdapter(other).preview("2026-10-14T11:00", start(rules, cases["PUB-13"])["products"])
    check("another brief (Saturday a business day, 16 October a holiday, 3 business days): the card's deadline becomes Monday 19 October",
          "DEADLINE (latest date that can be recorded): Monday 19 October 2026 [2026-10-19]" in seen["account_facts"]
          and "Saturday 17 October 2026 [2026-10-17]: OK" in seen["account_facts"]
          and "Friday 16 October 2026 [2026-10-16]: NOT possible, a bank holiday" in seen["account_facts"], seen["account_facts"][:1500])
    check("the adapter finds nothing it cannot express in Brief A+B beyond the missing default lowest amount", len(adapter.problems()) <= 1, adapter.problems())
    broken = json.loads(json.dumps(rules))
    broken["bands"][1]["min"] = 20
    broken["bands"][0]["deadline"] = {"type": "third_friday_of_month"}
    found = runtime_facts.HiddenBriefAdapter(broken).problems()
    check("the adapter reports a gap between bands and a deadline rule the engine does not know",
          any("no band covers" in x for x in found) and any("unknown to the engine" in x for x in found), found)
    import rules as rules_form
    check("the adapter and the rules form know the same date rules", tuple(rules_form.DATE_TYPES) == runtime_facts.HiddenBriefAdapter.DATE_TYPES)

    print("\n" + ("ALL CHECKS PASSED" if not FAILED else "%d CHECK(S) FAILED: %s" % (len(FAILED), ", ".join(FAILED))))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
