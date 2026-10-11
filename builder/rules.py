"""Step 1 of the fast builder: brief files -> rules in ONE fixed JSON shape.

    python builder/rules.py <brief.md> [<more briefs>...] --tools <openapi.json> -o rules.json

A model fills the shape; code checks every tool, field and allowed value against the
tool API file. compile.py then builds the workflow from this file with no model.

The shape describes a call with these steps: who is on the line, identity check,
account lookup, negotiation of a promise, recording, closing. Values, names, sentences,
dates and limits are all data in the file. A rule that does not fit a slot goes to
"rules" (it still reaches the prompt of its step) and is listed in "unmapped".
"""
import argparse
import asyncio
import datetime
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.environ.get("BUILDER_MODEL", "claude-opus-5-5")
HTTP_METHODS = ("get", "post", "put", "patch", "delete")

# The kit: "The seven tool names stay the same. The fields and the allowed values can change."
TOOLS = {"identity": "verify_identity", "account": "get_account", "promise": "create_promise", "note": "add_note",
         "callback": "schedule_callback", "transfer": "transfer_to_agent", "close": "close_call"}
STEPS = ("opening", "third_party", "identity", "negotiation", "after_promise", "everywhere")
DATE_TYPES = ("today", "next_business_day", "business_days_after_today", "calendar_days_after_today",
              "last_business_day_of_week", "last_business_day_of_month", "deadline")
ANSWER_KINDS = ("date", "digits", "text")
AMOUNTS = ("full", "floor", "full_or_floor")

SHAPE = r"""
{
 "language": "language of the call",
 "say_name_as": "how to address the customer, written with call variables in double braces",
 "now_variable": "the call variable that holds the call's date and time",
 "call_variables": ["every call variable"],

 "opening": {
  "line": "the first words of the call, complete, including the question that checks who is on the line. Call variables in double braces. Only what may be said before the identity check",
  "confirm_asks": 2
 },

 "identity": {
  "question_field": "request field of verify_identity that says which question is answered",
  "answer_field": "request field of verify_identity that carries the answer",
  "questions": [{"value": "value of question_field", "ask": "the question in words", "kind": "date | digits | text", "send_as": "format the tool wants"}],
  "passed": {"field": "response field that shows the whole check passed", "value": true},
  "wrong": {"field": "response field that shows a wrong answer", "value": false},
  "wrong_limit": 3,
  "exhausted": {"field": "response field that shows no tries are left, if the tool API file has one, else empty", "value": 0},
  "failed_line": "what the agent says when the check has failed for good, complete, with thanks and goodbye",
  "failed_ending": {"request field of close_call": "value"}
 },

 "account": {
  "products_field": "response field of get_account that lists the products",
  "id_field": "", "type_field": "", "amount_field": "field with the amount owed", "lateness_field": "field that decides the band",
  "names": [{"type": "value of type_field, or * for any other", "say": "how to name it; may use {fields} of the product in single braces"}],
  "floor_fields": [{"type": "value of type_field, or *", "field": "product field that holds the lowest amount that can be recorded"}],
  "intro": "what the agent says right after the identity check, before the products, complete sentence(s)",
  "product_sentence": "one sentence that states one product, using {name} {amount} {lateness}",
  "first_ask": "the first question that asks for payment"
 },

 "calendar": {"business_weekdays": [0, 1, 2, 3, 4], "holidays": ["YYYY-MM-DD"]},

 "bands": [{"name": "", "min": 1, "max": 7, "deadline": {"type": "business_days_after_today", "n": 12}, "attempts": 1}],

 "proposals": [{"attempt": 1, "date": {"type": "last_business_day_of_week"}, "amount": "full | floor | full_or_floor", "bands": ["band names it applies to; empty = all"]}],

 "floor_offer": {"limit": 1, "line": "the one offer of the lowest amount, as a complete sentence with what the brief requires to be said with it, using {floor} and {name}"},

 "callback": {"days": ["today", "next_business_day"], "from": "HH:MM", "to": "HH:MM", "other_time_asks": 1},

 "promise": {
  "ok": {"field": "response field of create_promise that shows success", "value": true},
  "failure_limit": 2,
  "recorded_line": "what the agent says right after a promise is recorded, complete, including every sentence the brief requires there and thanks and goodbye",
  "service_error_line": "what the agent says when recording failed for good, complete, with thanks and goodbye",
  "service_error_ending": {"request field of close_call": "value"},
  "ending": {"request field of close_call": "value"}
 },

 "notes": {"used": false, "every_product_without_promise": false, "when": "empty, or when the brief requires add_note"},

 "endings": [{"when": "the situation", "fields": {"request field of close_call": "value"}, "rule": "rule number"}],

 "tool_descriptions": {"tool name": "one instruction: what the tool does and exactly when to CALL it, from the brief"},

 "tool_results": {"tool name": [["response field, or * for any other result", "value that field has", "what this result means and what the agent must do next, from the brief"]]},

 "rules": [{"id": "rule number or short name", "step": "opening | third_party | identity | negotiation | after_promise | everywhere", "text": "the rule, complete, in the brief's words"}],

 "unmapped": ["id of every rule in rules whose content fits none of the slots above"]
}
"""

PROMPT = """You are filling in a fixed form about a phone agent. Read every one of these files completely.

BRIEF FILES (in this order; when two disagree, the later file or the later-dated document wins):
%(briefs)s

TOOL API FILE:
%(tools)s

Answer with ONE JSON object in exactly this shape and nothing else (no prose, no code fence):
%(shape)s
How to fill it:
- Take every value from the files. Never fill a slot from general knowledge. Numbers are numbers, not words.
- Tool names, field names and allowed values must be written exactly as in the tool API file.
- "date" objects have a "type", one of: %(date_types)s.
  business_days_after_today and calendar_days_after_today also take "n". "deadline" means the deadline of the product's band.
  last_business_day_of_week means this week's, or next week's when today already is that day.
- "wrong_limit", "confirm_asks", "failure_limit", "attempts", "limit", "other_time_asks" are how many times the thing may happen in total.
- "notes.every_product_without_promise": true only when the brief requires add_note for EVERY product that ends the call
  without a recorded promise (a transfer may be an exception). Otherwise false.
- "tool_results": for each tool list the results the agent must react to, most specific first, and end with ["*", "", "..."].
  A failed call must say that nothing was recorded and that the agent must not claim otherwise.
- "rules": EVERY rule of the brief, each exactly once, with the step where the agent needs it:
  opening (the first sentence and finding out who is on the line), third_party (someone else answers),
  identity (the security check), negotiation (from stating the debt to the confirmed promise),
  after_promise (after a promise is recorded), everywhere (holds in the whole call, and the closing table).
- Lines the agent says must be complete sentences a caller can hear as they are.
"""


def _operations(api):
    for path, item in api.get("paths", {}).items():
        for method in HTTP_METHODS:
            op = item.get(method)
            if isinstance(op, dict):
                yield (op.get("operationId") or path.strip("/").split("/")[-1]), op


def _props(schema):
    return (schema or {}).get("properties") or {}


def api_index(api):
    """{tool: {"request": {field: schema}, "response": {field: schema}}} from an OpenAPI file."""
    index = {}
    for name, op in _operations(api):
        request, response = {}, {}
        for media in ((op.get("requestBody") or {}).get("content") or {}).values():
            request.update(_props(media.get("schema")))
        for answer in (op.get("responses") or {}).values():
            for media in (answer.get("content") or {}).values():
                response.update(_props(media.get("schema")))
        index[name] = {"request": request, "response": response}
    return index


def parse_json(text):
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("the model's answer contains no JSON object")
    return json.loads(text[start:end + 1])


def validate(rules, api):
    """Return a list of problems. Empty means compile.py can use the rules."""
    problems = []
    index = api_index(api)
    say = problems.append

    def need(obj, path, kind):
        value = obj
        for key in path.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        ok = isinstance(value, kind) and not (kind is int and isinstance(value, bool)) and value not in ("", [], {})
        if kind is int and ok and value < 1:
            ok = False
        if not ok:
            say("%s is missing or not a valid %s" % (path, kind.__name__))
        return value if ok else None

    def request_field(tool, field, where, value=None):
        fields = index.get(tool, {}).get("request", {})
        if field not in fields:
            say("%s: %s has no request field %r" % (where, tool, field))
        elif value is not None and "enum" in fields[field] and value not in fields[field]["enum"]:
            say("%s: %r is not an allowed value of %s.%s" % (where, value, tool, field))

    def response_field(tool, field, where):
        if field not in index.get(tool, {}).get("response", {}):
            say("%s: %s has no response field %r" % (where, tool, field))

    def date_ok(date, where):
        if not isinstance(date, dict) or date.get("type") not in DATE_TYPES:
            say("%s: date type must be one of %s" % (where, ", ".join(DATE_TYPES)))
        elif date["type"].endswith("after_today") and not isinstance(date.get("n"), int):
            say("%s: %s needs a whole number n" % (where, date["type"]))

    def ending_ok(fields, where):
        if not isinstance(fields, dict) or not fields:
            say("%s has no fields" % where)
            return
        for field, value in fields.items():
            request_field(TOOLS["close"], field, where, value)

    for tool in TOOLS.values():
        if tool not in index:
            say("the tool API file has no tool %s" % tool)
    for path in ("language", "say_name_as", "now_variable", "opening.line", "identity.failed_line", "account.intro",
                 "account.product_sentence", "account.first_ask", "floor_offer.line", "promise.recorded_line",
                 "promise.service_error_line", "callback.from", "callback.to"):
        need(rules, path, str)
    for path in ("opening.confirm_asks", "identity.wrong_limit", "floor_offer.limit", "promise.failure_limit"):
        need(rules, path, int)
    for path in ("identity.questions", "account.names", "account.floor_fields", "bands", "proposals", "endings", "rules",
                 "calendar.business_weekdays", "callback.days"):
        need(rules, path, list)
    if problems:
        return problems

    identity, account, promise = rules["identity"], rules["account"], rules["promise"]
    request_field(TOOLS["identity"], identity.get("question_field"), "identity.question_field")
    request_field(TOOLS["identity"], identity.get("answer_field"), "identity.answer_field")
    for q in identity["questions"]:
        request_field(TOOLS["identity"], identity.get("question_field"), "identity.questions", q.get("value"))
        if q.get("kind") not in ANSWER_KINDS:
            say("identity question %r: kind must be one of %s" % (q.get("value"), ", ".join(ANSWER_KINDS)))
        if not q.get("ask"):
            say("identity question %r has no 'ask'" % q.get("value"))
    for key in ("passed", "wrong"):
        response_field(TOOLS["identity"], (identity.get(key) or {}).get("field"), "identity." + key)
    if (identity.get("exhausted") or {}).get("field"):
        response_field(TOOLS["identity"], identity["exhausted"]["field"], "identity.exhausted")
    for path in ("account.intro", "account.product_sentence", "account.first_ask", "floor_offer.line"):
        section, key = path.split(".")
        if "{{" in rules[section][key] or "}}" in rules[section][key]:
            say("%s must not use double braces (it is filled by code: use single braces)" % path)
    ending_ok(identity.get("failed_ending"), "identity.failed_ending")

    response_field(TOOLS["account"], account.get("products_field"), "account.products_field")
    products = index[TOOLS["account"]]["response"].get(account.get("products_field"), {})
    product_fields = _props(products.get("items"))
    for key in ("id_field", "type_field", "amount_field", "lateness_field"):
        if account.get(key) not in product_fields:
            say("account.%s: %r is not a field of a product in the tool API file" % (key, account.get(key)))
    for entry in account["floor_fields"]:
        if entry.get("field") not in product_fields:
            say("account.floor_fields: %r is not a field of a product" % entry.get("field"))
    for entry in account["names"]:
        for used in re.findall(r"\{(\w+)\}", entry.get("say") or ""):
            if used not in product_fields:
                say("account.names: {%s} is not a field of a product" % used)

    for day in (rules["calendar"].get("holidays") or []):
        try:
            datetime.date.fromisoformat(day)
        except (TypeError, ValueError):
            say("calendar.holidays: %r is not a date" % (day,))
    names = set()
    for band in rules["bands"]:
        names.add(band.get("name"))
        date_ok(band.get("deadline"), "band %s" % band.get("name"))
        if not all(isinstance(band.get(k), (int, float)) and not isinstance(band.get(k), bool) for k in ("min", "max")):
            say("band %s: min and max must be numbers" % band.get("name"))
        if not isinstance(band.get("attempts"), int) or band["attempts"] < 0:
            say("band %s: attempts must be a whole number" % band.get("name"))
    for proposal in rules["proposals"]:
        date_ok(proposal.get("date"), "proposal %s" % proposal.get("attempt"))
        if proposal.get("amount") not in AMOUNTS:
            say("proposal %s: amount must be one of %s" % (proposal.get("attempt"), ", ".join(AMOUNTS)))
        for band in proposal.get("bands") or []:
            if band not in names:
                say("proposal %s: unknown band %r" % (proposal.get("attempt"), band))

    response_field(TOOLS["promise"], (promise.get("ok") or {}).get("field"), "promise.ok")
    ending_ok(promise.get("service_error_ending"), "promise.service_error_ending")
    ending_ok(promise.get("ending"), "promise.ending")
    for ending in rules["endings"]:
        ending_ok(ending.get("fields"), "ending %r" % (ending.get("when"),))

    for tool, text in (rules.get("tool_descriptions") or {}).items():
        if tool not in index:
            say("tool_descriptions: unknown tool %r" % tool)
    for tool, cases in (rules.get("tool_results") or {}).items():
        if tool not in index:
            say("tool_results: unknown tool %r" % tool)
            continue
        for case in cases:
            if not (isinstance(case, list) and len(case) == 3 and case[2]):
                say("tool_results.%s: each case is [field, value, instruction]" % tool)
            elif case[0] != "*":
                response_field(tool, case[0], "tool_results.%s" % tool)
    seen = set()
    for rule in rules["rules"]:
        if rule.get("step") not in STEPS:
            say("rule %r: step must be one of %s" % (rule.get("id"), ", ".join(STEPS)))
        if not rule.get("text"):
            say("rule %r has no text" % rule.get("id"))
        if rule.get("id") in seen:
            say("rule %r appears twice" % rule.get("id"))
        seen.add(rule.get("id"))
    for rule_id in rules.get("unmapped") or []:
        if rule_id not in seen:
            say("unmapped: %r is not the id of a rule" % (rule_id,))
    for path in ("opening.line", "say_name_as"):
        value = rules
        for key in path.split("."):
            value = value[key]
        for used in re.findall(r"\{\{\s*(\w+)\s*\}\}", value):
            if used not in rules.get("call_variables", []):
                say("%s uses {{%s}}, which is not a call variable" % (path, used))
    return problems


async def _ask(prompt, cwd, effort):
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
    options = ClaudeAgentOptions(cwd=cwd, model=MODEL, allowed_tools=["Read"], permission_mode="dontAsk", effort=effort,
                                 disallowed_tools=["Bash", "PowerShell", "WebFetch", "WebSearch", "Agent", "Task"],
                                 setting_sources=["project"], max_turns=20)
    answer, cost = "", 0.0
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            answer, cost = message.result or "", message.total_cost_usd or 0.0
    return answer, cost


def extract(brief_paths, tool_api_path, cwd, trace=None, effort="medium", retries=1):
    """Fill the shape from the briefs in `cwd` (paths relative to it). Returns the checked rules."""
    with open(os.path.join(cwd, tool_api_path), encoding="utf-8") as f:
        api = json.load(f)
    prompt = PROMPT % {"briefs": "\n".join("- " + p.replace("\\", "/") for p in brief_paths),
                       "tools": "- " + tool_api_path.replace("\\", "/"), "shape": SHAPE, "date_types": ", ".join(DATE_TYPES)}
    problems = []
    for attempt in range(1 + retries):
        ask = prompt if not problems else prompt + "\nYour previous answer had these problems. Fix them:\n- " + "\n- ".join(problems)
        if trace:
            trace.model_call(MODEL, "fill the fixed rules form from the brief files" + (" (retry after check)" if problems else ""))
        started = time.time()
        answer, cost = asyncio.run(_ask(ask, cwd, effort))
        try:
            rules = parse_json(answer)
            problems = validate(rules, api)
        except ValueError as error:
            rules, problems = None, ["the answer was not valid JSON: %s" % error]
        if trace:
            trace.note("rules form: %.0f s, $%.2f, %d problem(s)" % (time.time() - started, cost, len(problems)))
        if not problems:
            return rules
    raise ValueError("the rules do not fit the tool API file:\n- " + "\n- ".join(problems))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fill the fixed rules form from brief files.")
    parser.add_argument("briefs", nargs="+")
    parser.add_argument("--tools", required=True)
    parser.add_argument("-o", "--out", required=True)
    args = parser.parse_args(argv)
    import fastbuild
    workspace = fastbuild.make_workspace(args.briefs, args.tools)
    try:
        started = time.time()
        rules = extract(["briefs/" + os.path.basename(b) for b in args.briefs], "tool_api/" + os.path.basename(args.tools), workspace)
    finally:
        import shutil
        shutil.rmtree(workspace, ignore_errors=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rules, f, indent=1, ensure_ascii=False)
    print("wrote %s in %.0f s: %d rules (%d unmapped), %d endings, %d bands" % (
        args.out, time.time() - started, len(rules["rules"]), len(rules.get("unmapped") or []), len(rules["endings"]), len(rules["bands"])), file=sys.stderr)


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    main()
