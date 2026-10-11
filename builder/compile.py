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
                   settled_close (every product dealt with, no promise), transfer_close (when every product
                   without a promise needs a note, the negotiation itself has no closing tool)
"""
import copy
import inspect
import json

import limits
import runtime_facts
from rules import TOOLS

STRUCTURE_NOTE_GATE = (
    "\n\nTHE LOWEST AMOUNT (FLOOR): when the rules above say to offer it, make that offer only in the way the FLOOR OFFER"
    " line of the facts below says, and no more often than it allows."
    "\nSEVERAL PRODUCTS: every product must end with either a recorded promise or, where the rules require one, a note."
    " The system keeps the list and tells you after each tool result which products are still open. When none is open,"
    " the system continues by itself.")
STRUCTURE_NOTE_NO_CLOSE = (
    "\nCLOSING: you cannot close the call in this step. The system moves to the closing step by itself once every product"
    " has a recorded promise or a note, or once a transfer was made. So when the call has to end here without a transfer,"
    " first deal with every open product as the rules say.")
TRANSFER_CLOSE_TEXT = (
    "STEP: the call has just been transferred. Do not negotiate any more. Thank the customer, say goodbye and CALL %s with"
    " the values the closing table gives for this transfer.")
SETTLED_CLOSE_TEXT = (
    "STEP: every product has been dealt with and no promise was recorded. Do not negotiate any more. Thank the customer,"
    " say goodbye and CALL %s with the values the closing table gives for this situation."
    " WHAT WAS RECORDED, in the order of the products: {{settle_summary}}.")
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
            if _d.get('guarded'):
                break
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
        if %(id_field)r in _b:
            vars['last_product'] = str(_b.get(%(id_field)r))
except Exception:
    pass
"""

# Before a promise or a note goes to the bank, code checks it against the facts of the call: the product exists,
# the date is one that can be recorded for that product, the amount lies between its lowest and its full amount,
# a product that is already settled gets no second note. A call that fails the check is NOT sent: the request is
# turned into the harmless account lookup, and GUARD_POST gives the model the reason instead of the lookup's answer.
# After 3 stopped calls in one conversation the check lets everything through, so a call can never hang on it.
GUARD_PRE = """try:
    vars['guard_block'] = ''
    _b = config.get('body')
    _ids = [x for x in str(vars.get('product_ids') or '').split(',') if x]
    _why = ''
    if isinstance(_b, dict) and _ids:
        _pid = str(_b.get(%(id_field)r) or '')
        _done = [x for x in str(vars.get('settled') or '').split(',') if x]
        if _pid not in _ids:
            _why = 'There is no product ' + _pid + '. The products of this customer are: ' + ', '.join(_ids) + '.'
        elif %(kind)r == 'note':
            if %(no_second)r and _pid in _done:
                _why = 'Product ' + _pid + ' already has ' + str(vars.get('rec_' + _pid) or 'a promise or a note') + '. It gets no further note.'
        else:
            _dates = [x for x in str(vars.get('ok_dates_' + _pid) or '').split(',') if x]
            _date = str(_b.get(%(date_field)r) or '')
            if %(date_field)r and _dates and _date not in _dates:
                _why = ('The date ' + _date + ' cannot be recorded for product ' + _pid + '. Find that date in DATE CALENDAR and'
                        ' check the DEADLINE of this product, then tell the customer which date is possible.')
            elif %(amount_field)r and vars.get('floor_' + _pid) and vars.get('full_' + _pid):
                _a = float(_b.get(%(amount_field)r))
                if _a < float(vars.get('floor_' + _pid)) - 0.005 or _a > float(vars.get('full_' + _pid)) + 0.005:
                    _why = ('The amount ' + str(_b.get(%(amount_field)r)) + ' cannot be recorded for product ' + _pid + '. Only amounts from '
                            + str(vars.get('floor_' + _pid)) + ' up to ' + str(vars.get('full_' + _pid)) + ' can.')
        if %(kind)r == 'note':
            _vals = []
            for _k in %(enum_fields)r:
                if _b.get(_k) is not None:
                    _vals.append(_k + ' ' + str(_b.get(_k)))
            vars['note_values'] = ', '.join(_vals)
    if _why:
        _why = _why + ' Nothing was recorded: do not say that anything is recorded. Correct it first.'
        _n = int(float(vars.get('guard_blocks') or 0)) + 1
        vars['guard_blocks'] = _n
        if _n <= %(max)d:
            _safe = dict()
            for _k in %(keep)r:
                if _k in _b:
                    _safe[_k] = _b.get(_k)
            config['url'] = %(safe_url)r
            config['body'] = _safe
            vars['guard_block'] = _why
except Exception:
    pass
"""

GUARD_POST = """try:
    _g = str(vars.get('guard_block') or '')
    vars['guard_block'] = ''
    if _g and isinstance(result, dict):
        _d = result.get('data')
        if isinstance(_d, dict) and %(products_field)r in _d:
            _n = dict()
            _n[%(ok)r] = %(not_ok)r
            _n['guarded'] = 'yes'
            _n['error'] = 'NOT SENT TO THE BANK. ' + _g
            _n['what_to_do_next'] = 'This call was stopped before it reached the bank, so nothing failed at the bank. ' + _g
            result['data'] = _n
except Exception:
    pass
"""

# The same for closing, when every product without a promise needs a note: a close_call that comes while a product
# is still open is not sent. (Seen on the platform: the model can call close_call in a step that does not list it,
# and it then closed before the note was written.) After a transfer nothing is checked. Lets through after 2 stops.
CLOSE_GUARD_PRE = """try:
    vars['guard_block'] = ''
    _b = config.get('body')
    _ids = [x for x in str(vars.get('product_ids') or '').split(',') if x]
    _done = [x for x in str(vars.get('settled') or '').split(',') if x]
    _open = [x for x in _ids if x not in _done]
    if isinstance(_b, dict) and _open and str(vars.get('transferred') or '') != 'yes':
        _n = int(float(vars.get('close_blocks') or 0)) + 1
        vars['close_blocks'] = _n
        if _n <= %(max)d:
            _safe = dict()
            for _k in %(keep)r:
                if _k in _b:
                    _safe[_k] = _b.get(_k)
            config['url'] = %(safe_url)r
            config['body'] = _safe
            vars['guard_block'] = ('The call is NOT closed. These products have neither a recorded promise nor a note yet: '
                                   + ', '.join(_open) + '. Deal with each of them first as the rules say, one tool call per'
                                   ' message. After the last one the system moves on to the closing step by itself.')
except Exception:
    pass
"""

TRANSFER_HOOK = """try:
    _d = result.get('data') if isinstance(result, dict) else None
    if isinstance(_d, dict) and str(_d.get(%(ok)r)).lower() == %(okv)r:
        vars['transferred'] = 'yes'
except Exception:
    pass
"""

# After a promise or a note: which products are settled? Code keeps this list, not the model.
# kind is 'promise' or 'note'. A product is settled once it has a recorded promise or a note.
SETTLE_HOOK = """try:
    _d = result.get('data') if isinstance(result, dict) else None
    if isinstance(_d, dict):
        _ids = [x for x in str(vars.get('product_ids') or '').split(',') if x]
        _pid = str(vars.get('last_product') or vars.get(%(args)r) or '')
        if len(_ids) == 1:
            _pid = _ids[0]
        _done = [x for x in str(vars.get('settled') or '').split(',') if x]
        _ok = str(_d.get(%(ok)r)).lower() == %(okv)r
        _fails = 0
        if _ok:
            if _pid not in _done:
                _done.append(_pid or ('unknown' + str(len(_done))))
            if %(kind)r == 'promise':
                vars['promises_made'] = int(float(vars.get('promises_made') or 0)) + 1
                vars['rec_' + _pid] = 'a recorded promise'
            else:
                vars['rec_' + _pid] = 'a note (' + str(vars.get('note_values') or '') + ')'
            _sum = []
            for _x in _ids:
                _sum.append(_x + ': ' + str(vars.get('rec_' + _x) or 'nothing recorded'))
            vars['settle_summary'] = '; '.join(_sum)
        elif %(kind)r == 'promise' and not _d.get('guarded') and 'already' not in str(_d.get('error') or '').lower():
            _fails = int(float(vars.get('pf_' + _pid) or 0)) + 1
            vars['pf_' + _pid] = _fails
            _d['failed_tries_for_this_product'] = _fails
            _d['tries_allowed_for_one_product'] = %(limit)d
            if _fails >= %(limit)d and len(_ids) <= 1:
                vars['promise_dead'] = 'yes'
        vars['settled'] = ','.join(_done)
        _open = [x for x in _ids if x not in _done]
        if _ids and len(_done) >= len(_ids):
            vars['all_settled'] = 'with_promise' if int(float(vars.get('promises_made') or 0)) > 0 else 'without_promise'
        elif _ok and len(_ids) > 1:
            _d['what_to_do_next'] = (str(_d.get('what_to_do_next') or '') + ' Still open (neither a promise nor a note): '
                                     + ', '.join(_open) + '. If the customer has already confirmed what happens with one of them,'
                                     ' CALL its tool NOW, before you say anything. One tool call per message. '
                                     + str(vars.get('script_' + _open[0]) or ''))
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
        "floor_offer_line": rules["floor_offer"]["line"], "floor_offer_limit": rules["floor_offer"]["limit"],
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
        for field in sorted({f for e in endings for f in e}):  # sorted: the same rules give the same workflow
            for value in sorted({e[field] for e in endings if field in e}, key=str):
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
    kind = {TOOLS["promise"]: "promise", TOOLS["note"]: "note"}.get(name)
    ok = rules["promise"]["ok"]
    account = tools[TOOLS["account"]]["function"]["parameters"]["properties"]
    keep = sorted(f for f in account["body"].get("properties", {}) if f in rules.get("call_variables", []))
    guard_post = _checked(GUARD_POST % {"products_field": rules["account"]["products_field"], "ok": ok["field"],
                                        "not_ok": (not ok["value"]) if isinstance(ok["value"], bool) else ""})
    notes = rules.get("notes") or {}
    if name == TOOLS["transfer"]:
        tool["post_process_code"] += _checked(TRANSFER_HOOK % {"ok": ok["field"], "okv": str(ok["value"]).lower()})
    if name == TOOLS["close"] and notes.get("used") and notes.get("every_product_without_promise"):
        tool["pre_process_code"] += _checked(CLOSE_GUARD_PRE % {"keep": keep, "safe_url": account["url"]["default"], "max": 2})
        tool["post_process_code"] = guard_post + tool["post_process_code"]
    if kind:
        fields = _body(tool).get("properties") or {}
        dates = [f for f in fields if "YYYY-MM-DD" in str(fields[f].get("description", "")).upper()]
        amounts = [f for f in fields if fields[f].get("type") in ("number", "integer")]
        tool["pre_process_code"] += _checked(GUARD_PRE % {
            "id_field": rules["account"]["id_field"], "kind": kind,
            # a field is checked only when the tool API file leaves no doubt which one it is
            "date_field": dates[0] if len(dates) == 1 else "", "amount_field": amounts[0] if len(amounts) == 1 else "",
            "enum_fields": sorted(f for f in fields if fields[f].get("enum")),
            "no_second": bool(notes.get("every_product_without_promise")),
            "keep": keep, "safe_url": account["url"]["default"], "max": 3})
        tool["post_process_code"] = guard_post + tool["post_process_code"]
        tool["post_process_code"] += _checked(SETTLE_HOOK % {
            "args": "%s_args.%s" % (name, rules["account"]["id_field"]), "ok": ok["field"], "okv": str(ok["value"]).lower(),
            "kind": kind, "limit": rules["promise"]["failure_limit"]})
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
    on_state = lambda target, variable, value, text: {
        "target_node": target, "description": text,
        "condition": {"logic": "or", "conditions": [{"variable": variable, "operator": "equals", "operand": value}]}}
    # Leaving the negotiation is decided by code: all products settled, or recording failed for good.
    notes = rules.get("notes") or {}
    # When every product without a promise needs a note, the model cannot close from the negotiation at all:
    # it would be able to close before the notes are written.
    note_all = bool(notes.get("used") and notes.get("every_product_without_promise"))
    transferred = {"target_node": "transfer_close", "description": "the call was transferred",
                   "condition": {"logic": "or", "conditions": equals_true(response_var(T["transfer"], ok["field"]), ok["value"])}}
    settled = lambda: ([transferred] if note_all else
                       [on_state("close_service_error", "promise_dead", "yes", "recording failed for good")]) + [
        closed(),
        on_state("promise_done", "all_settled", "with_promise", "every product is settled and a promise was recorded"),
        on_state("settled_close", "all_settled", "without_promise", "every product is settled and no promise was recorded")]
    negotiation_tools = [T["promise"], T["transfer"], T["account"]] + ([] if note_all else [T["close"]])
    if notes.get("used"):
        negotiation_tools.insert(1, T["note"])
    close_only = lambda name, text: {
        "name": name, "node_type": "conversation", "task_message": text % T["close"], "on_enter": {"type": "llm"},
        "max_node_turns": 80, "tools": [model_tool(rules, tools, T["close"])], "transitions": [closed()]}

    account_node = {
        "name": "get_account", "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "account_facts",
        "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True, "suppress_tool_results": True,
        "pre_actions": [engine_call(tools, T["account"], rules["call_variables"])],
        "response_mapping": [{"path": "data." + rules["account"]["products_field"], "variable": "products"}],
        "transitions": [{"target_node": "account_facts", "description": "always", "condition": {"operator": "always"}}]}

    negotiation = prompts["negotiation"] + (STRUCTURE_NOTE_NO_CLOSE if note_all else "")
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
                                negotiation_tools, settled(), "{{debt_statement}}"),
        "collect_after_offer": conversation("collect_after_offer", negotiation + STRUCTURE_NOTE_AFTER + "\n\n{{account_facts}}",
                                            negotiation_tools, settled(), "{{floor_offer_line}}"),
        "promise_done": conversation("promise_done", prompts["after_promise"], [T["transfer"], T["close"]], [closed()],
                                     promise["recorded_line"]),
        "settled_close": close_only("settled_close", SETTLED_CLOSE_TEXT),
        "close_failed": fixed_close("close_failed", identity["failed_line"], identity["failed_ending"]),
        "close_service_error": fixed_close("close_service_error", promise["service_error_line"], promise["service_error_ending"]),
        "end": {"name": "end", "node_type": "end_call", "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
                "transitions": [], "terminal": {"type": "function", "function": {
                    "name": "call_closed", "handler": "__function__:end_call",
                    "parameters": {"type": "object", "properties": {"message_mode": {"type": "string", "default": "none"}}}}}},
    }
    if note_all:
        nodes["transfer_close"] = close_only("transfer_close", TRANSFER_CLOSE_TEXT)
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
    report.append({"limit": "promise.failure_limit", "n": promise["failure_limit"],
                   "how": "counted per product in the tool's hook; one product: route to the closing step", "where": "collect, collect_after_offer"})
    report.append({"limit": "a promise or a note is checked before it is sent (product, date, amount, no second note)", "n": 3,
                   "how": "checked in the tool's hook against the dates and amounts computed in code; a call that fails is not sent",
                   "where": "collect, collect_after_offer"})
    if note_all:
        report.append({"limit": "no closing while a product has neither a promise nor a note", "n": 2,
                       "how": "checked in the hook of close_call; a close that comes too early is not sent", "where": "every step"})
    report.append({"limit": "every product gets a promise or a note", "n": 1,
                   "how": "list kept in the tool hooks; route when none is open"
                          + ("; no closing tool in the negotiation" if note_all else ""), "where": "collect, collect_after_offer"})
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
