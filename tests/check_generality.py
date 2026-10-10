"""Does the fast builder read the brief, or does it know Brief A?

    python tests/check_generality.py make     write build/mutated/brief-m.md: Brief A with other numbers, dates, hours and sentences
    python tests/check_generality.py check    after `fastbuild.py build/mutated/brief-m.md ... --no-save`: look at build/fast-brief-m/

`check` fails when a value that exists only in the original Brief A shows up in the
compiled workflow, or when the date engine does not follow the changed rules.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (text in Brief A, replacement). Every one must match exactly once.
CHANGES = [
    ("2026-10-26, 2026-11-11, 2026-11-26, 2026-12-25, 2027-01-01", "2026-10-20, 2026-11-05, 2026-12-24, 2027-01-01, 2027-01-06"),
    ("| 1 to 7 | early | the 12th business day after today | 1 |", "| 1 to 10 | early | the 9th business day after today | 1 |"),
    ("| 8 to 45 | late | the 7th business day after today | 2 |", "| 11 to 60 | late | the 5th business day after today | 2 |"),
    ("1 to 45 days late on one product", "1 to 60 days late on one product"),
    ("from 10:00 to 19:00", "from 09:00 to 17:00"),
    ("\"You will receive a text message confirming this promise.\"", "\"We will send you an e-mail that confirms this promise.\""),
    ("say you are a digital assistant calling from Freya Bank", "say you are the virtual assistant of Freya Bank"),
    ("say that you are Freya Bank's digital assistant", "say that you are Freya Bank's virtual assistant"),
    ("first the date of birth, then the last four digits of the phone number on file",
     "first the last four digits of the phone number on file, then the date of birth"),
    ("Attempt 1: propose the last business day of this week. If today is that day, propose the last business day of next week.",
     "Attempt 1: propose the next business day."),
    ("use the mobile app or a branch", "call the service line"),
]
# Values that exist only in the original. None may appear in what was built from the changed brief.
ONLY_IN_ORIGINAL = ["2026-10-26", "2026-11-11", "2026-11-26", "2026-12-25", "text message", "digital assistant", "19:00", "mobile app or"]


def make():
    with open(os.path.join(ROOT, "kit", "briefs", "brief-a.md"), encoding="utf-8") as f:
        text = f.read()
    for old, new in CHANGES:
        assert text.count(old) == 1, "expected exactly one match for: " + old
        text = text.replace(old, new)
    out = os.path.join(ROOT, "build", "mutated", "brief-m.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print("wrote", out, "with", len(CHANGES), "changes")


def check():
    run = os.path.join(ROOT, "build", "fast-brief-m")
    with open(os.path.join(run, "workflow.json"), encoding="utf-8") as f:
        raw = f.read()
    workflow = json.loads(raw)
    with open(os.path.join(run, "rules.json"), encoding="utf-8") as f:
        rules = json.load(f)
    failures = []

    def expect(label, ok, detail=""):
        print(("OK    " if ok else "FAIL  ") + label + (("  | " + str(detail)) if detail and not ok else ""))
        if not ok:
            failures.append(label)

    for value in ONLY_IN_ORIGINAL:
        expect("no %r in the workflow" % value, value not in raw)
    expect("holidays are the new ones", rules["calendar"]["holidays"] == ["2026-10-20", "2026-11-05", "2026-12-24", "2027-01-01", "2027-01-06"], rules["calendar"]["holidays"])
    expect("bands are the new ones", [(b["min"], b["max"], b["deadline"].get("n"), b["attempts"]) for b in rules["bands"]] == [(1, 10, 9, 1), (11, 60, 5, 2)], rules["bands"])
    expect("callback hours are the new ones", (rules["callback"]["from"], rules["callback"]["to"]) == ("09:00", "17:00"), rules["callback"])
    expect("questions are in the new order", [q["kind"] for q in rules["identity"]["questions"]] == ["digits", "date"], rules["identity"]["questions"])
    expect("the first spoken question is the first of the new order", workflow["nodes"]["identity"]["on_enter"]["text"] == rules["identity"]["questions"][0]["ask"])
    expect("attempt 1 is the next business day", rules["proposals"][0]["date"]["type"] == "next_business_day", rules["proposals"][0])
    expect("the new sentence is spoken after a promise", "e-mail" in workflow["nodes"]["promise_done"]["on_enter"]["text"], workflow["nodes"]["promise_done"]["on_enter"]["text"])
    expect("the opening names the virtual assistant", "virtual assistant" in workflow["nodes"]["who"]["on_enter"]["text"], workflow["nodes"]["who"]["on_enter"]["text"])

    code = workflow["nodes"]["account_facts"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
    variables = {"now": "2026-10-14T11:00", "products": [
        {"product_id": "p1", "type": "personal_loan", "past_due": 600.0, "days_late": 9, "instalment_amount": 200.0}]}
    exec(code, {"vars": variables})
    facts = variables["account_facts"]
    # 9 days late is "early" under the new bands (it was "late"); 20 October is now a holiday
    expect("9 days late gets 1 attempt", "PERSUASION ATTEMPTS ALLOWED: 1." in facts)
    expect("deadline is the 9th business day, skipping the new holiday (28 October)", "DEADLINE (latest date that can be recorded): Wednesday 28 October 2026" in facts,
           [l for l in facts.splitlines() if "DEADLINE" in l])
    expect("20 October is not possible", "[2026-10-20]: NOT possible, a bank holiday" in facts)
    expect("26 October is possible again", "[2026-10-26]: OK" in facts)
    expect("attempt 1 proposes 15 October", "Attempt 1 proposal: payment on Thursday 15 October 2026" in facts, [l for l in facts.splitlines() if "Attempt 1" in l])
    expect("callback window ends at 17:00", "not after 17:00" in variables["call_facts"])
    print()
    print("ALL CHECKS PASSED" if not failures else "%d CHECK(S) FAILED" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(make() if sys.argv[1:] == ["make"] else check())
