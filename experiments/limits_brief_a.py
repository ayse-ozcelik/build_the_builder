"""EXPERIMENT, not part of the builder: does builder/limits.py work on the real platform?

Takes the agent the kit's example builder made for Brief A, applies two limits with
builder/limits.py and writes the result to experiments/out/limits-test.workflow.json.
The Brief A values in this file (3 wrong answers, 1 floor offer) are test input only.

    python experiments/limits_brief_a.py

1. Wrong security answers: the copy has ONLY our counter to end the check (the
   original transition on the bank's own "answers left" field is removed), so a
   pass proves that a post-hook can write a variable and a transition can read it.
2. The floor offer: it becomes a fixed line behind a counting gate, so it can be
   said only once.
"""
import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "builder"))
import limits  # noqa: E402


def swap(text, old, new):
    assert text.count(old) == 1, "expected exactly one match for: " + old[:60]
    return text.replace(old, new)


with open(os.path.join(ROOT, "kit", "out", "brief-a.workflow.json"), encoding="utf-8") as f:
    workflow = json.load(f)
nodes = workflow["nodes"]

# --- 1. wrong answers ----------------------------------------------------------
opening = nodes["opening"]
before = len(opening["transitions"])
opening["transitions"] = [t for t in opening["transitions"] if t["target_node"] != "close_failed"]
assert len(opening["transitions"]) == before - 1
limits.apply_tool_limit(workflow, "opening", "verify_identity", counter="wrong_answers", field="verified",
                        equals=False, limit=3, target="close_failed")
opening["task_message"] += (
    "\n- An answer that comes together with a complaint or a question is still an answer: CALL verify_identity"
    " with it first, and deal with the rest after the result."
)

# --- 2. the floor offer ----------------------------------------------------------
# The facts code also prepares the offer as one fixed sentence.
OFFER_LINE = ("    vars['floor_offer_line'] = 'I can record a payment of ' + fm(floor) + ' for ' + S[0].split('.')[0]"
              " + ', and the rest stays overdue. Would that work for you?'\n")
for name in ("setup", "account_facts"):
    props = nodes[name]["pre_actions"][0]["function"]["parameters"]["properties"]
    props["code"]["default"] = swap(props["code"]["default"], "    vars['account_state'] = 'loaded'\n",
                                    OFFER_LINE + "    vars['account_state'] = 'loaded'\n")
    assert "{{" not in props["code"]["default"]

collect = nodes["collect"]
original_task = collect["task_message"]
OLD_TOO_MUCH = ('- If the customer says the full amount is too much (or says "you tell me"): offer the FLOOR amount, ONCE,'
                ' and say that the rest stays overdue. If they refuse the floor, that is a flat refusal (F).')
OLD_BELOW = ('- If the customer proposes less than the FLOOR: say that amount cannot be recorded and offer the FLOOR once.'
             ' If they do not accept the floor, that is a flat refusal (F).')

task = swap(original_task, OLD_TOO_MUCH,
            '- If the customer says the full amount is too much (or says "you tell me") and names no amount: do not name'
            ' a lower amount yourself. CALL transition_to_floor_gate. The system then makes the one allowed offer.')
collect["task_message"] = swap(task, OLD_BELOW,
            '- If the customer proposes less than the FLOOR: say that amount cannot be recorded and CALL'
            ' transition_to_floor_gate. The system then makes the one allowed offer.')

after = copy.deepcopy(collect)
after["name"] = "collect_after_offer"
after["on_enter"] = {"type": "tts", "text": "{{floor_offer_line}}"}
task = swap(original_task, OLD_TOO_MUCH,
            '- The FLOOR amount has just been offered to the customer, once, with the note that the rest stays overdue.'
            ' That was the only offer of it you may make: never offer it again and do not repeat the amount as a new'
            ' offer. If the customer gives no clear answer (for example "you tell me"), ask only whether they can make'
            ' that payment today. If they refuse it, that is a flat refusal (F).')
after["task_message"] = swap(task, OLD_BELOW,
            '- If the customer proposes less than the FLOOR: say that amount cannot be recorded. The FLOOR was already'
            ' offered; if they do not accept it, that is a flat refusal (F).')
nodes["collect_after_offer"] = after

limits.add_counted_step(workflow, "floor_gate", counter="floor_offers", limit=1,
                        within="collect_after_offer", over="collect_after_offer")
collect["transitions"].append({
    "target_node": "floor_gate",
    "description": "The customer says the full amount is too much, or asks you to name an amount, or proposes less than the floor",
    "is_tool_call": True,
    "condition": {"operator": "matches_intent",
                  "description": "The customer says the full amount is too much or asks you to name an amount"},
})

def has_none(value):
    if isinstance(value, dict):
        return any(has_none(v) for v in value.values())
    if isinstance(value, list):
        return any(has_none(v) for v in value)
    return value is None


assert not has_none(workflow), "the platform rejects null values"
out = os.path.join(ROOT, "experiments", "out", "limits-test.workflow.json")
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w", encoding="utf-8") as f:
    json.dump(workflow, f, indent=1)
print("nodes:", ", ".join(nodes))
print("opening transitions:", [t["target_node"] for t in opening["transitions"]])
print("collect transitions:", [t["target_node"] for t in collect["transitions"]])
print("written", out)
