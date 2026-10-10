"""Step 3 of the fast builder: rules + prompts + tools -> the whole workflow. No model.

    workflow, report = compile_workflow(rules, prompts, tools)

The STRUCTURE is fixed here (steps, routes on tool results, counters, the date
engine, hooks). Every VALUE comes from the three inputs:
    rules    builder/rules.py      (from the brief)
    prompts  builder/prompts.py    (from the brief)
    tools    builder/tools_gen.py  (from the tool API file)
This file must not contain a number, date, name or sentence of any brief.

Steps of the call:
    setup, who, identity, get_account, account_facts, collect, promise_done, end
    from identity: close_failed (the identity check failed for good)
    from collect:  floor_gate, then collect_after_offer (the one offer of the lowest amount)
                   close_service_error (recording failed for good)
"""
import copy
import inspect
import json

import limits
import runtime_facts
from rules import TOOLS

STRUCTURE_NOTE_GATE = (
    "\n\nTHE LOWEST AMOUNT (FLOOR): you never offer it in your own words and never name it as an offer. When the rules"
    " above say to offer it, CALL transition_to_floor_gate. The system then says the offer itself, once.")
STRUCTURE_NOTE_AFTER = (
    "\n\nTHE LOWEST AMOUNT (FLOOR) HAS JUST BEEN OFFERED by the system, in the last sentence the customer heard. That was the"
    " only offer of it that is allowed here: do not offer it again and do not repeat it as a new offer. If the customer gives"
    " no clear answer to it, ask only whether they can pay that amount today, or on which day.")
GATE_DESCRIPTION = ("The customer says the full amount is too much, asks you to name an amount, or proposes less than the"
                    " lowest amount that can be recorded")

POST_HOOK = """try:
    if isinstance(result, dict):
        result.pop('header', None)
        _d = result.get('data')
        if not isinstance(_d, dict):
            _d = dict()
            _d[%(ok)r] = False
            _d['error'] = 'the service failed, nothing was recorded'
            result['data'] = _d
        for _f, _v, _t in %(cases)r:
            if _f == '*' or str(_d.get(_f)).lower() == str(_v).lower():
                _d['what_to_do_next'] = _t
                break
except Exception:
    pass
"""

PRE_HOOK = """try:
    def _date(a):
        import re
        a = str(a).strip()
        if re.match(r'^\\d{4}-\\d{2}-\\d{2}', a):
            return a[:10]
        mon = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
        low = a.lower().replace(',', ' ')
        y = mo = dy = None
        m = re.match(r'^(\\d{1,2})\\s*[./\\- ]\\s*(\\d{1,2})\\s*[./\\- ]\\s*(\\d{4})$', low)
        if m:
            dy, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            for w in re.findall(r'[a-z]+', low):
                if len(w) >= 3 and w[:3] in mon:
                    mo = mon.index(w[:3]) + 1
            ym = re.search(r'(\\d{4})', low)
            dm = re.search(r'(?<!\\d)(\\d{1,2})(?:st|nd|rd|th)?(?!\\d)', low)
            if ym:
                y = int(ym.group(1))
            if dm:
                dy = int(dm.group(1))
        if y and mo and dy:
            return '%%04d-%%02d-%%02d' %% (y, mo, dy)
        return a
    def _digits(a):
        import re
        d = re.sub(r'\\D', '', str(a))
        return d if d else str(a)
    def _time(a):
        import re
        m = re.match(r'^(\\d{1,2})[:.h](\\d{2})', str(a).strip())
        return ('%%02d:%%02d' %% (int(m.group(1)), int(m.group(2)))) if m else a
    _b = config.get('body')
    if isinstance(_b, dict):
        for _k in list(_b.keys()):
            _x = _b.get(_k)
            if _k not in %(required)r and (_x is None or str(_x).strip().lower() in ('', 'none', 'null') or '{' in str(_x)):
                _b.pop(_k, None)
        for _f, _v, _drop in %(solo)r:
            if str(_b.get(_f)) == str(_v):
                for _k in _drop:
                    _b.pop(_k, None)
        _kind = dict(%(kinds)r).get(str(_b.get(%(selector)r)))
        if _kind == 'date' and %(answer)r in _b:
            _b[%(answer)r] = _date(_b[%(answer)r])
        if _kind == 'digits' and %(answer)r in _b:
            _b[%(answer)r] = _digits(_b[%(answer)r])
        for _k in %(dates)r:
            if isinstance(_b.get(_k), str):
                _b[_k] = _date(_b[_k])
        for _k in %(times)r:
            if isinstance(_b.get(_k), str):
                _b[_k] = _time(_b[_k])
        for _k in %(numbers)r:
            if isinstance(_b.get(_k), str):
                try:
                    _b[_k] = float(_b[_k].replace(',', ''))
                except Exception:
                    pass
        _ids = [x for x in str(vars.get('product_ids') or '').split(',') if x]
        if %(id_field)r in _b and len(_ids) == 1:
            _b[%(id_field)r] = _ids[0]
except Exception:
    pass
"""

FACTS_TAIL = """
try:
    _run(vars, %(params)r)
except Exception:
    for _k, _v in (('call_facts', ''), ('account_facts', 'ACCOUNT FACTS: NOT LOADED.'), ('debt_statement', %(intro)r),
                   ('floor_offer_line', ''), ('account_state', 'missing'), ('product_ids', '')):
        if not vars.get(_k):
            vars[_k] = _v
"""


def _checked(code):
    if "{{" in code or "}}" in code:
        raise ValueError("generated code must not contain double braces")
    compile(code, "<generated>", "exec")  # a syntax error must stop the build, not the call
    return code


def _body(tool):
    return tool["function"]["parameters"]["properties"]["body"]


def facts_code(rules):
    account = rules["account"]
    params = {
        "now_variable": rules["now_variable"],
        "holidays": rules["calendar"].get("holidays") or [],
        "business_weekdays": rules["calendar"]["business_weekdays"],
        "callback": rules["callback"],
        "account_tool": TOOLS["account"],
        "products_field": account["products_field"], "id_field": account["id_field"], "type_field": account["type_field"],
        "amount_field": account["amount_field"], "lateness_field": account["lateness_field"],
        "names": account["names"], "floor_fields": account["floor_fields"],
        "intro": account["intro"], "product_sentence": account["product_sentence"], "first_ask": account["first_ask"],
        "bands": rules["bands"], "proposals": rules["proposals"],
        "floor_offer_line": rules["floor_offer"]["line"],
    }
    # indent=1 keeps closing braces on separate lines, so the text never holds two in a row
    source = inspect.getsource(runtime_facts._run)
    return _checked(source + FACTS_TAIL % {"params": json.dumps(params, indent=1), "intro": account["intro"]})


PASSED_TEXT = "The check has passed. The system now continues and tells the customer why the bank is calling."
RECORDED_TEXT = "It IS recorded. The system now tells the customer that it is recorded. Do not call this tool again for it."
CONFIRMED_DESCRIPTION = "The person on the line has confirmed that they are the customer"
STRUCTURE_NOTE_WHO = (
    "\n\nWHEN THE CUSTOMER IS CONFIRMED: CALL transition_to_identity. The system then asks the first security question"
    " itself. Do not ask a security question in your own words, and never treat what the person says here as a security answer.")


def post_hook(rules, tool_name):
    cases = [[str(f), str(v), str(t)] for f, v, t in (rules.get("tool_results") or {}).get(tool_name) or []]
    # Results the structure reacts to by itself get a fixed text, first in the list.
    fixed = {TOOLS["identity"]: (rules["identity"]["passed"], PASSED_TEXT),
             TOOLS["promise"]: (rules["promise"]["ok"], RECORDED_TEXT)}.get(tool_name)
    if fixed:
        cases.insert(0, [str(fixed[0]["field"]), str(fixed[0]["value"]), fixed[1]])
    return _checked(POST_HOOK % {"ok": rules["promise"]["ok"]["field"], "cases": cases})


def pre_hook(rules, tool_name, tool):
    body = _body(tool)
    fields = body.get("properties") or {}
    identity = rules["identity"] if tool_name == TOOLS["identity"] else {}
    solo = []
    if tool_name == TOOLS["close"]:
        # a value that the endings table always lists alone must be sent alone
        endings = [e["fields"] for e in rules["endings"]]
        for field in {f for e in endings for f in e}:
            for value in {e[field] for e in endings if field in e}:
                same = [e for e in endings if e.get(field) == value]
                others = {f for e in same for f in e if f != field}
                if not others:
                    drop = sorted(f for f in fields if f != field and f not in body.get("required", [])
                                  and f not in rules.get("call_variables", []))
                    if drop:
                        solo.append([field, value, drop])
    describes = lambda name, word: word in str(fields[name].get("description", "")).upper()
    return _checked(PRE_HOOK % {
        "required": list(body.get("required", [])),
        "solo": solo,
        "kinds": [[q["value"], q["kind"]] for q in identity.get("questions", [])],
        "selector": identity.get("question_field", ""),
        "answer": identity.get("answer_field", ""),
        "dates": [f for f in fields if describes(f, "YYYY-MM-DD") and f != identity.get("answer_field")],
        "times": [f for f in fields if describes(f, "HH:MM")],
        "numbers": [f for f in fields if fields[f].get("type") in ("number", "integer")],
        "id_field": rules["account"]["id_field"],
    })


def model_tool(rules, tools, name, writes=True):
    """A tool the model may call: generated definition + description, hooks and config from the rules."""
    tool = copy.deepcopy(tools[name])
    description = (rules.get("tool_descriptions") or {}).get(name)
    if description:
        tool["function"]["description"] = description
    tool["config"] = {"timeoutMs": 12000, "maxRetries": 0 if writes else 1}
    tool["pre_process_code"] = pre_hook(rules, name, tool)
    tool["post_process_code"] = post_hook(rules, name)
    return tool


def engine_call(tools, name, call_variables, fixed=None):
    """A call the engine makes itself (no model). `fixed` bakes values into body fields."""
    tool = copy.deepcopy(tools[name])
    tool["config"] = {"timeoutMs": 12000, "maxRetries": 2}
    body = _body(tool)
    kept = {}
    for field, schema in body["properties"].items():
        if fixed and field in fixed:
            kept[field] = {"type": schema.get("type", "string"), "default": fixed[field]}
        elif field in call_variables:
            kept[field] = schema  # filled by the platform, such as the customer id
    body["properties"], body["required"] = kept, []
    return tool


def equals_true(variable, value=True):
    spellings = ("true", "True") if value is True else ("false", "False") if value is False else (str(value),)
    return [{"variable": variable, "operator": "equals", "operand": s} for s in spellings]


def response_var(tool, field):
    return "%s_response.data.%s" % (tool, field)


def compile_workflow(rules, prompts, tools):
    """Return (workflow, report). `report` says how each counted limit was built."""
    ok = rules["promise"]["ok"]
    identity, promise = rules["identity"], rules["promise"]
    T = TOOLS
    code = facts_code(rules)

    def code_node(name, target):
        return {"name": name, "node_type": "code", "passthrough": True, "passthrough_default_transition": target,
                "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
                "pre_actions": [{"type": "function", "function": {"name": "code_exec", "handler": "__function__:code_exec",
                                 "parameters": {"type": "object", "properties": {"code": {"type": "string", "default": code},
                                                                                 "timeout_ms": {"type": "number", "default": 10000}}}}}],
                "transitions": [{"target_node": target, "description": "always", "condition": {"operator": "always"}}]}

    def conversation(name, task, tool_names, transitions, line):
        return {"name": name, "node_type": "conversation", "task_message": task, "on_enter": {"type": "tts", "text": line},
                "on_enter_first_entry_only": True, "max_node_turns": 80,
                "tools": [model_tool(rules, tools, n, writes=(n != T["account"])) for n in tool_names],
                "transitions": transitions}

    def fixed_close(name, line, ending):
        return {"name": name, "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "end",
                "on_enter": {"type": "tts", "text": line}, "disable_inactivity_monitor": True,
                "pre_actions": [engine_call(tools, T["close"], rules["call_variables"], fixed=ending)],
                "transitions": [{"target_node": "end", "description": "always", "condition": {"operator": "always"}}]}

    closed = lambda: {"target_node": "end", "description": "the call was closed",
                      "condition": {"logic": "or", "conditions": equals_true(response_var(T["close"], ok["field"]), ok["value"])}}
    recorded = lambda: {"target_node": "promise_done", "description": "the promise was recorded",
                        "condition": {"logic": "or", "conditions": equals_true(response_var(T["promise"], ok["field"]), ok["value"])}}

    account_node = {
        "name": "get_account", "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "account_facts",
        "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True, "suppress_tool_results": True,
        "pre_actions": [engine_call(tools, T["account"], rules["call_variables"])],
        "response_mapping": [{"path": "data." + rules["account"]["products_field"], "variable": "products"}],
        "transitions": [{"target_node": "account_facts", "description": "always", "condition": {"operator": "always"}}]}

    negotiation = prompts["negotiation"]
    nodes = {
        "setup": code_node("setup", "who"),
        # The identity tool exists only after the person is confirmed, so it cannot be used on anyone else.
        "who": conversation("who", prompts["who"] + STRUCTURE_NOTE_WHO + "\n\n{{call_facts}}",
                            [T["callback"], T["transfer"], T["close"]],
                            [closed(), {"target_node": "identity", "description": CONFIRMED_DESCRIPTION, "is_tool_call": True,
                                        "condition": {"operator": "matches_intent", "description": CONFIRMED_DESCRIPTION}}],
                            rules["opening"]["line"]),
        "identity": conversation("identity", prompts["identity"], [T["identity"], T["transfer"], T["close"]],
                                 [closed(), {"target_node": "get_account", "description": "the identity check passed",
                                             "condition": {"logic": "or", "conditions": equals_true(
                                                 response_var(T["identity"], identity["passed"]["field"]), identity["passed"]["value"])}}],
                                 identity["questions"][0]["ask"]),
        "get_account": account_node,
        "account_facts": code_node("account_facts", "collect"),
        "collect": conversation("collect", negotiation + STRUCTURE_NOTE_GATE + "\n\n{{account_facts}}",
                                [T["promise"], T["transfer"], T["account"], T["close"]], [closed(), recorded()], "{{debt_statement}}"),
        "collect_after_offer": conversation("collect_after_offer", negotiation + STRUCTURE_NOTE_AFTER + "\n\n{{account_facts}}",
                                            [T["promise"], T["transfer"], T["account"], T["close"]], [closed(), recorded()],
                                            "{{floor_offer_line}}"),
        "promise_done": conversation("promise_done", prompts["after_promise"], [T["transfer"], T["close"]], [closed()],
                                     promise["recorded_line"]),
        "close_failed": fixed_close("close_failed", identity["failed_line"], identity["failed_ending"]),
        "close_service_error": fixed_close("close_service_error", promise["service_error_line"], promise["service_error_ending"]),
        "end": {"name": "end", "node_type": "end_call", "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
                "transitions": [], "terminal": {"type": "function", "function": {
                    "name": "call_closed", "handler": "__function__:end_call",
                    "parameters": {"type": "object", "properties": {"message_mode": {"type": "string", "default": "none"}}}}}},
    }
    workflow = {"initial_node": "setup", "system_prompt": prompts["system"], "nodes": nodes, "global_nodes": {}}

    # ---- limits: counted by code, never by the model
    report = []
    exhausted = identity.get("exhausted") or {}
    if exhausted.get("field"):
        # the bank keeps the count itself: route on its answer (it knows which tries do not count)
        nodes["identity"]["transitions"].insert(0, {
            "target_node": "close_failed", "description": "no more tries are left",
            "condition": {"logic": "or", "conditions": equals_true(response_var(T["identity"], exhausted["field"]), exhausted.get("value"))}})
        report.append({"limit": "identity.wrong_limit", "n": identity["wrong_limit"], "how": "route on the bank's own count (%s)" % exhausted["field"], "where": "identity"})
    else:
        limits.apply_tool_limit(workflow, "identity", T["identity"], counter="wrong_answers", field=identity["wrong"]["field"],
                                equals=identity["wrong"]["value"], limit=identity["wrong_limit"], target="close_failed")
        report.append({"limit": "identity.wrong_limit", "n": identity["wrong_limit"], "how": "tool_limit", "where": "identity"})
    for node in ("collect", "collect_after_offer"):
        limits.apply_tool_limit(workflow, node, T["promise"], counter="promise_failures", field=ok["field"],
                                equals=not ok["value"], limit=promise["failure_limit"], target="close_service_error")
    report.append({"limit": "promise.failure_limit", "n": promise["failure_limit"], "how": "tool_limit", "where": "collect, collect_after_offer"})
    limits.add_counted_step(workflow, "floor_gate", counter="floor_offers", limit=rules["floor_offer"]["limit"],
                            within="collect_after_offer", over="collect_after_offer")
    nodes["collect"]["transitions"].append({"target_node": "floor_gate", "description": GATE_DESCRIPTION, "is_tool_call": True,
                                            "condition": {"operator": "matches_intent", "description": GATE_DESCRIPTION}})
    report.append({"limit": "floor_offer.limit", "n": rules["floor_offer"]["limit"], "how": "counted_step (fixed line, said on entry)", "where": "floor_gate"})
    for name, where in (("opening.confirm_asks", "who"), ("callback.other_time_asks", "who")):
        section, key = name.split(".")
        report.append({"limit": name, "n": rules[section].get(key), "how": "prompt_only", "where": where})
    for band in rules["bands"]:
        report.append({"limit": "attempts (band %s)" % band["name"], "n": band["attempts"],
                       "how": "prompt_only, with the allowed number and each proposal computed in code", "where": "collect"})
    return workflow, report
