"""Generates out/brief-a.workflow.json from the rules of briefs/brief-a.md and tools/openapi.json."""
import json, pathlib

BASE = "https://freya-bank-mock.freyavoice.workers.dev/v1/"
HOLIDAYS = ["2026-10-26", "2026-11-11", "2026-11-26", "2026-12-25", "2027-01-01"]
OUTCOMES = ["promise", "paid", "transferred", "no_promise", "not_verified", "not_reached"]
REASONS = ["restructuring", "human", "support", "refused", "hardship", "dispute", "service_error", "failed", "declined",
           "callback", "no_callback", "wrong_number", "deceased", "unclear"]
CLOSE_TABLE = """CLOSING TABLE (outcome / reason):
- promise / no reason: at least one promise was recorded.
- paid / no reason: the customer says this past-due amount is already paid.
- transferred / restructuring, human or support: after a successful transfer_to_agent (queue restructuring -> restructuring, queue general -> human, queue support -> support). A transfer after a recorded promise is still closed as transferred.
- no_promise / refused (attempts used up, or said twice they do not want to talk), hardship (hardship and declined the support team), dispute (says the debt or product is not theirs), service_error (the promise could not be recorded after two tries).
- not_verified / failed (three wrong answers), declined (would not answer the security questions).
- not_reached / callback, no_callback, wrong_number, deceased, unclear."""

# ---------------------------------------------------------------- code: date and account facts
FACTS_CODE = r'''
def _run(vars):
    import datetime, json, re
    HOL = set(__HOLIDAYS__)
    WD = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    MN = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
    one = datetime.timedelta(days=1)
    def isbd(d):
        return d.weekday() < 5 and d.isoformat() not in HOL
    def nbd(d):
        d = d + one
        while not isbd(d):
            d = d + one
        return d
    def kth(d, k):
        for _ in range(k):
            d = nbd(d)
        return d
    def say(d):
        return WD[d.weekday()] + ' ' + str(d.day) + ' ' + MN[d.month - 1] + ' ' + str(d.year)
    def both(d):
        return say(d) + ' [' + d.isoformat() + ']'
    def why(d):
        if d.isoformat() in HOL:
            return 'a bank holiday'
        return 'a weekend day'
    def fm(x):
        try:
            return '%.2f' % float(x)
        except Exception:
            return str(x)
    raw = str(vars.get('now') or '').strip().replace('Z', '')
    try:
        now = datetime.datetime.fromisoformat(raw[:16])
    except Exception:
        try:
            now = datetime.datetime.fromisoformat(raw[:10])
        except Exception:
            now = datetime.datetime.utcnow()
    today = now.date()
    hhmm = '%02d:%02d' % (now.hour, now.minute)
    nb = nbd(today)
    # ---- callback window
    L = ['NOW: ' + say(today) + ', ' + hhmm + '. Use only this date and time, never the real clock.', '',
         'CALLBACK WINDOW (the only slots you may schedule with schedule_callback):']
    if (now.hour, now.minute) < (19, 0):
        lo = hhmm if hhmm > '10:00' else '10:00'
        L.append('- Today, ' + both(today) + ': a time later than ' + hhmm + ', not before 10:00 and not after 19:00 (so later than ' + lo + ' at the earliest; 19:00 itself is allowed).')
    else:
        L.append('- Today is no longer possible (it is already ' + hhmm + ').')
    L.append('- The next business day, ' + both(nb) + ': from 10:00 to 19:00 (10:00 and 19:00 themselves are allowed).')
    L.append('Any other day, and any time before 10:00 or after 19:00, is OUTSIDE the window.')
    L.append('Coming days: ' + '; '.join((('tomorrow = ' if i == 1 else '') + both(today + one * i) + (' = the next business day, in the window' if today + one * i == nb else ' = OUTSIDE the window')) for i in range(1, 9)) + '.')
    vars['call_facts'] = '\n'.join(L)
    # ---- products
    prods = []
    try:
        cands = [vars.get('products'), vars.get('get_account_response.data.products')]
        r = vars.get('get_account_response')
        if isinstance(r, str):
            try:
                r = json.loads(r)
            except Exception:
                r = None
        if isinstance(r, dict):
            dd = r.get('data')
            if isinstance(dd, dict):
                cands.append(dd.get('products'))
            cands.append(r.get('products'))
        for c in cands:
            if isinstance(c, str) and c.strip():
                try:
                    c = json.loads(c)
                except Exception:
                    try:
                        import ast
                        c = ast.literal_eval(c)
                    except Exception:
                        c = None
            if isinstance(c, dict):
                c = [c[k] for k in sorted(c.keys(), key=str)]
            if isinstance(c, list) and c and all(isinstance(x, dict) for x in c):
                prods = c
                break
        if not prods:
            tmp = dict()
            for k in list(vars.keys()):
                m = re.search(r'products[.\[](\d+)\]?\.(\w+)$', str(k))
                if m:
                    tmp.setdefault(int(m.group(1)), dict())[m.group(2)] = vars.get(k)
            prods = [tmp[i] for i in sorted(tmp.keys())]
    except Exception:
        prods = []
    prods = [p for p in prods if isinstance(p, dict) and p.get('product_id')]
    # ---- weekday words
    monday = today - one * today.weekday()
    ww = []
    for i in range(7):
        t = monday + one * i
        tag = ' (today)' if t == today else (' (already past, not possible)' if t < today else '')
        ww.append('this ' + WD[i] + ' = ' + both(t) + tag + '; next ' + WD[i] + ' = ' + both(t + one * 7))
    last_this = monday + one * 6
    while not isbd(last_this) and last_this > monday - one:
        last_this = last_this - one
    last_next = monday + one * 13
    while not isbd(last_next):
        last_next = last_next - one
    att1 = last_this if (last_this > today and isbd(last_this)) else last_next
    if not prods:
        vars['account_state'] = 'missing'
        vars['product_ids'] = ''
        vars['debt_statement'] = 'Thank you, your identity is verified. One moment please, I am looking up the details.'
        e, l = kth(today, 12), kth(today, 7)
        vars['account_facts'] = ('ACCOUNT FACTS: NOT LOADED. CALL get_account now, before saying anything about the account. Then state the debt as described and ask "Can you make this payment today?".\n'
            'Today: ' + both(today) + '. Deadline if days_late is 1 to 7 (1 persuasion attempt): ' + both(e) + '. Deadline if days_late is 8 to 45 (2 persuasion attempts): ' + both(l) + '.\n'
            'Attempt 1 date: ' + both(att1) + '. Business days are Monday to Friday except bank holidays ' + ', '.join(sorted(HOL)) + '.\n'
            'Floor amount: minimum_payment for a credit card, instalment_amount for a loan.\nWEEKDAY WORDS: ' + '. '.join(ww) + '.')
        return
    A, S, ids, maxdl = [], [], [], today
    A.append('ACCOUNT FACTS (computed by the bank system. Use them exactly as written, never recompute dates yourself):')
    A.append('- Today: ' + both(today) + (', a business day.' if isbd(today) else ', NOT a business day: a payment "today" cannot be recorded, offer ' + both(nb) + ' instead.'))
    for p in prods:
        t = str(p.get('type') or '')
        if t == 'credit_card':
            name = 'credit card ending ' + str(p.get('card_last4') or '')
            floor = p.get('minimum_payment'); fl = 'the minimum payment'
        else:
            name = t.replace('_', ' ')
            floor = p.get('instalment_amount'); fl = 'the instalment amount'
        if floor is None:
            floor = p.get('minimum_payment') if p.get('minimum_payment') is not None else p.get('instalment_amount')
        try:
            dl = int(float(p.get('days_late') or 0))
        except Exception:
            dl = 0
        late = dl >= 8
        dead = kth(today, 7 if late else 12)
        if dead > maxdl:
            maxdl = dead
        ids.append(str(p.get('product_id')))
        A.append('- Product: ' + name + ' (product_id ' + str(p.get('product_id')) + '). Always name it in these words.')
        A.append('  FULL past-due amount: ' + fm(p.get('past_due')) + '. Days late: ' + str(dl) + '.')
        A.append('  FLOOR amount: ' + fm(floor) + ' (' + fl + '). Amounts from ' + fm(floor) + ' up to ' + fm(p.get('past_due')) + ', both included, can be recorded. Nothing below ' + fm(floor) + ' and nothing above ' + fm(p.get('past_due')) + '.')
        A.append('  DEADLINE (latest promise date that can be recorded): ' + both(dead) + '.')
        A.append('  PERSUASION ATTEMPTS ALLOWED: ' + ('2' if late else '1') + '.')
        A.append('  Attempt 1 proposal: pay on ' + both(att1) + '.')
        A.append('  Attempt 2 proposal: ' + ('pay the FLOOR amount ' + fm(floor) + ' on the DEADLINE ' + both(dead) + '.' if late else 'NOT available for this customer. After attempt 1 is refused, stop.'))
        S.append('your ' + name + '. The past-due amount is ' + fm(p.get('past_due')) + ' and the payment is ' + str(dl) + (' day late' if dl == 1 else ' days late'))
    A.append('')
    A.append('PROMISE DATE CALENDAR (date: can it be recorded?):')
    d = today
    while d <= maxdl:
        if isbd(d):
            A.append('- ' + both(d) + (' (today)' if d == today else '') + ': OK')
        else:
            A.append('- ' + both(d) + (' (today)' if d == today else '') + ': NOT possible, ' + why(d) + ' -> offer the next business day ' + both(nbd(d)))
        d = d + one
    A.append('- Any date after ' + both(maxdl) + ': NOT possible, after the deadline -> say the latest date you can record is ' + say(maxdl) + ' and offer it.')
    A.append('- Any date before today: not possible.')
    A.append('')
    A.append('WEEKDAY WORDS (week = Monday to Sunday): tomorrow = ' + both(today + one) + '. ' + '. '.join(ww) + '. A weekday name with a day number ("Monday the 26th") means that calendar date. A weekday name alone means the first such day after today.')
    vars['account_state'] = 'loaded'
    vars['product_ids'] = ','.join(ids)
    vars['account_facts'] = '\n'.join(A)
    vars['debt_statement'] = 'Thank you, your identity is verified. I am calling from the collections team about a late payment on ' + '. Also on '.join(S) + '. Can you make this payment today?'
try:
    _run(vars)
except Exception as _e:
    for _k, _v in (('call_facts', 'NOW: ' + str(vars.get('now'))), ('account_facts', 'ACCOUNT FACTS: NOT LOADED. CALL get_account now, before saying anything about the account.'), ('debt_statement', 'Thank you, your identity is verified. One moment please.'), ('account_state', 'missing'), ('product_ids', '')):
        if not vars.get(_k):
            vars[_k] = _v
'''.replace("__HOLIDAYS__", json.dumps(HOLIDAYS))
assert "{{" not in FACTS_CODE and "}}" not in FACTS_CODE

# ---------------------------------------------------------------- hooks
HEAD = """try:
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if not isinstance(d, dict):
            d = dict(ok=False, error='service failed, nothing was recorded')
            result['data'] = d
        ok = str(d.get('ok')).lower() == 'true'
        err = str(d.get('error') or '')
"""
TAIL = """except Exception:
    pass
"""
def post(body):
    code = HEAD + "".join("        " + l + "\n" for l in body.strip("\n").split("\n")) + TAIL
    assert "{{" not in code and "}}" not in code
    return code

POST_VERIFY = post("""
left = d.get('wrong_answers_left')
if str(left) == '0' or 'check is over' in err:
    msg = 'IDENTITY CHECK FAILED: three wrong answers. Ask no more questions and never state any debt, product or amount. Tell the customer you could not verify their identity and that they should use the mobile app or visit a branch, thank them, say goodbye and CALL close_call with outcome not_verified and reason failed.'
elif d.get('hint'):
    msg = 'Day and month seem to be the other way round. This does NOT count as a wrong answer. Ask the customer to say the date of birth again with the month as a word, then CALL verify_identity again with field date_of_birth.'
elif not ok:
    msg = 'The service could not read or check this answer (' + err + '). This does NOT count as a wrong answer. A date of birth must be sent as YYYY-MM-DD, phone digits as four digits. Fix the format and CALL verify_identity again, or ask the customer to repeat the answer.'
elif str(d.get('all_verified')).lower() == 'true':
    msg = 'Both security answers are correct. Identity verified.'
elif str(d.get('verified')).lower() == 'true':
    msg = 'CORRECT. Do not ask this question again. Now ask the next security question: the last four digits of the phone number on file. When the customer answers, CALL verify_identity with field phone_last4.'
else:
    msg = 'WRONG ANSWER. Wrong answers left: ' + str(left) + '. Tell the customer that this does not match the bank records and ask the SAME question once more. Do not move on to the other question and do not reveal the right answer. When the customer answers, CALL verify_identity again.'
d['what_to_do_next'] = msg
""")

POST_PROMISE = post("""
if ok:
    msg = 'The promise IS recorded.'
elif 'already has a promise' in err:
    msg = 'This product already has a recorded promise. Nothing new was recorded. A recorded promise cannot be changed or cancelled in this call: tell the customer they can change it in the mobile app.'
else:
    n = 0
    try:
        n = int(vars.get('cp_fail') or 0) + 1
        vars['cp_fail'] = n
    except Exception:
        n = 0
    if n == 1:
        msg = 'FAILED: the promise is NOT recorded (' + err + '). Do not say it is recorded. Tell the customer in a few words that it did not go through and that you are trying once more, and CALL create_promise again now with exactly the same product_id, amount and date.'
    elif n >= 2:
        msg = 'FAILED AGAIN: the promise is NOT recorded. Do not try again. Tell the customer you are sorry, you could not record the promise. Thank them, say goodbye and CALL close_call with outcome no_promise and reason service_error.'
    else:
        msg = 'FAILED: the promise is NOT recorded (' + err + '). Do not say it is recorded. If this was the first failed try, tell the customer it did not go through and CALL create_promise once more with the same values. If this was the second failed try, tell the customer you could not record the promise, thank them, say goodbye and CALL close_call with outcome no_promise and reason service_error.'
d['what_to_do_next'] = msg
""")

POST_TRANSFER = post("""
if ok:
    msg = 'The transfer is done. Tell the customer you are connecting them now, thank them, say goodbye and in the same message CALL close_call with outcome transferred and the reason for this queue (queue restructuring -> reason restructuring, queue general -> reason human, queue support -> reason support).'
else:
    msg = 'The transfer FAILED (' + err + '). Do not say the customer is being transferred. CALL transfer_to_agent once more with the same queue. If it fails again, tell the customer honestly that you could not connect them.'
d['what_to_do_next'] = msg
""")

POST_CALLBACK = post("""
if ok:
    msg = 'The callback is scheduled. Tell the person that the bank will call back at that day and time, thank them, say goodbye and in the same message CALL close_call with outcome not_reached and reason callback.'
else:
    msg = 'The callback was NOT scheduled (' + err + '). Do not say it is scheduled. CALL schedule_callback once more with date as YYYY-MM-DD and time as HH:MM. If it fails again, say honestly that you could not schedule it, say goodbye and CALL close_call with outcome not_reached and reason no_callback.'
d['what_to_do_next'] = msg
""")

POST_CLOSE = post("""
if ok or 'already closed' in err:
    msg = 'The call is closed. Call no more tools and say nothing more.'
elif err and 'reason' in err:
    msg = 'close_call was REJECTED: ' + err + '. CALL close_call again now with a valid reason for this outcome.'
else:
    msg = 'close_call FAILED (' + err + '). CALL close_call again now with the same outcome and reason.'
d['what_to_do_next'] = msg
""")

POST_ACCOUNT = post("""
if ok:
    msg = 'State the debt now: say you are calling from the collections team about a late payment, name the product in words (credit card ending <card_last4>, personal loan or car loan), the past_due amount and the days late. Then ask: Can you make this payment today? Use the product_id from this result for create_promise.'
else:
    msg = 'The account could not be loaded (' + err + '). CALL get_account once more. If it fails again, tell the customer honestly that you cannot access the details right now.'
d['what_to_do_next'] = msg
""")

PRE_VERIFY = r"""try:
    import re
    b = config.get('body')
    if isinstance(b, dict):
        f = str(b.get('field') or '')
        a = str(b.get('answer') or '').strip()
        if f == 'phone_last4':
            dg = re.sub(r'\D', '', a)
            if len(dg) == 4:
                b['answer'] = dg
        elif f == 'date_of_birth' and not re.match(r'^\d{4}-\d{2}-\d{2}$', a):
            MON = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
            low = a.lower().replace(',', ' ')
            y = mo = dy = None
            m = re.match(r'^(\d{1,2})\s*[./\- ]\s*(\d{1,2})\s*[./\- ]\s*(\d{4})$', low)
            if m:
                dy, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            else:
                m = re.match(r'^(\d{4})\s*[./\- ]\s*(\d{1,2})\s*[./\- ]\s*(\d{1,2})$', low)
                if m:
                    y, mo, dy = int(m.group(1)), int(m.group(2)), int(m.group(3))
                else:
                    for w in re.findall(r'[a-z]+', low):
                        if w[:3] in MON and len(w) >= 3 and w not in ('st', 'nd', 'rd', 'th'):
                            mo = MON.index(w[:3]) + 1
                    ym = re.search(r'(\d{4})', low)
                    dm = re.search(r'(?<!\d)(\d{1,2})(?:st|nd|rd|th)?(?!\d)', low)
                    if ym:
                        y = int(ym.group(1))
                    if dm:
                        dy = int(dm.group(1))
            if y and mo and dy:
                b['answer'] = '%04d-%02d-%02d' % (y, mo, dy)
except Exception:
    pass
"""
PRE_PROMISE = r"""try:
    b = config.get('body')
    if isinstance(b, dict):
        if isinstance(b.get('date'), str):
            b['date'] = b['date'].strip()[:10]
        ids = [x for x in str(vars.get('product_ids') or '').split(',') if x]
        if len(ids) == 1 and b.get('product_id') != ids[0]:
            b['product_id'] = ids[0]
        if isinstance(b.get('amount'), str):
            b['amount'] = float(b['amount'].replace(',', ''))
except Exception:
    pass
"""
PRE_CLOSE = r"""try:
    b = config.get('body')
    if isinstance(b, dict):
        r = b.get('reason')
        if b.get('outcome') in ('promise', 'paid') or r is None or str(r).strip() in ('', 'none', 'None', 'null') or '{' in str(r):
            b.pop('reason', None)
except Exception:
    pass
"""
PRE_CALLBACK = r"""try:
    import re
    b = config.get('body')
    if isinstance(b, dict):
        if isinstance(b.get('date'), str):
            b['date'] = b['date'].strip()[:10]
        m = re.match(r'^(\d{1,2})[:.h](\d{2})', str(b.get('time') or '').strip())
        if m:
            b['time'] = '%02d:%02d' % (int(m.group(1)), int(m.group(2)))
except Exception:
    pass
"""
for c in (PRE_VERIFY, PRE_PROMISE, PRE_CLOSE, PRE_CALLBACK):
    assert "{{" not in c and "}}" not in c

# ---------------------------------------------------------------- tools
def api_params(path, props, required):
    body = {"customer_id": {"type": "string", "default": "{{customer_id}}"}}
    body.update(props)
    return {"type": "object", "properties": {
        "url": {"type": "string", "default": BASE + path},
        "method": {"type": "string", "default": "POST"},
        "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
        "body": {"type": "object", "properties": body, "required": required}}}

def tool(name, desc, props, required, pre="", post_code="", mapping=None, retries=0):
    t = {"type": "function", "config": {"timeoutMs": 12000, "maxRetries": retries},
         "function": {"name": name, "handler": "__function__:api_call", "description": desc, "parameters": api_params(name, props, required)}}
    if pre: t["pre_process_code"] = pre
    if post_code: t["post_process_code"] = post_code
    if mapping: t["response_mapping"] = mapping
    return t

def leaf(name, typ, desc, enum=None):
    l = {"type": typ, "description": desc, "default": "{{" + name + "}}"}
    if enum: l["enum"] = enum
    return l

T_VERIFY = tool("verify_identity", "Check one security answer. CALL it every time the customer gives an answer to a security question, one call per answer. Never call it for anyone who is not the customer.",
    {"field": leaf("field", "string", "Which question the answer belongs to. One of: date_of_birth, phone_last4.", ["date_of_birth", "phone_last4"]),
     "answer": leaf("answer", "string", "For date_of_birth: the date as YYYY-MM-DD, converted from whatever the customer said (a date given only as numbers is day, then month, then year). For phone_last4: exactly four digits.")},
    ["field", "answer"], PRE_VERIFY, POST_VERIFY)
T_CALLBACK = tool("schedule_callback", "Book a callback for the customer. CALL it only when someone else answered and named a day and time inside the callback window.",
    {"date": leaf("date", "string", "Callback date as YYYY-MM-DD. Only today or the next business day from the callback window."),
     "time": leaf("time", "string", "Callback time as HH:MM, 24 hour clock, from 10:00 to 19:00.")},
    ["date", "time"], PRE_CALLBACK, POST_CALLBACK)
T_TRANSFER = tool("transfer_to_agent", "Hand the call to a person. CALL it when the rules say to transfer: queue restructuring for a new payment plan or lower instalments, queue support for the customer support team after hardship, queue general for a customer who insists on a human.",
    {"queue": leaf("queue", "string", "Team that takes over. One of: restructuring, general, support.", ["restructuring", "general", "support"])},
    ["queue"], "", POST_TRANSFER)
T_PROMISE = tool("create_promise", "Record a payment promise. CALL it only after you read back product, amount and date and the customer clearly said yes to \"Do you confirm?\". Saying it is recorded is not enough: you must call it.",
    {"product_id": leaf("product_id", "string", "The product_id from the account facts, for example p1. Copy it unchanged."),
     "amount": leaf("amount", "number", "The agreed amount as a number, between the floor amount and the full past-due amount."),
     "date": leaf("date", "string", "The agreed payment date as YYYY-MM-DD. It must be a date marked OK in the promise date calendar.")},
    ["product_id", "amount", "date"], PRE_PROMISE, POST_PROMISE, [{"path": "data.promise_id", "variable": "promise_id"}])
T_ACCOUNT = tool("get_account", "The customer's late product. CALL it only if the account facts say NOT LOADED.", {}, [], "", POST_ACCOUNT, None, 1)
T_CLOSE = tool("close_call", "End the call and record how it ended. CALL it exactly once, as the very last tool call, in the same message in which you thank the customer and say goodbye. Never call another tool after it.",
    {"outcome": leaf("outcome", "string", "How the call ended. One of: " + ", ".join(OUTCOMES) + ". See the closing table.", OUTCOMES),
     "reason": leaf("reason", "string", "Required for every outcome except promise and paid (leave it out for those). transferred: restructuring, human, support. no_promise: refused, hardship, dispute, service_error. not_verified: failed, declined. not_reached: callback, no_callback, wrong_number, deceased, unclear.", REASONS)},
    ["outcome"], PRE_CLOSE, POST_CLOSE)

def both(var, val_true=("true", "True")):
    return [{"variable": var, "operator": "equals", "operand": v} for v in val_true]
CLOSED = {"target_node": "end", "description": "close_call succeeded", "condition": {"logic": "or", "conditions": both("close_call_response.data.ok")}}

def fixed_close(name, line, outcome, reason):
    return {"name": name, "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "end",
            "on_enter": {"type": "tts", "text": line}, "disable_inactivity_monitor": True,
            "pre_actions": [{"type": "function", "config": {"timeoutMs": 12000, "maxRetries": 2},
                             "function": {"name": "close_call", "handler": "__function__:api_call", "description": "Close the call.",
                                          "parameters": api_params("close_call", {"outcome": {"type": "string", "default": outcome}, "reason": {"type": "string", "default": reason}}, [])}}],
            "transitions": [{"target_node": "end", "description": "always", "condition": {"operator": "always"}}]}

def code_node(name, target):
    return {"name": name, "node_type": "code", "passthrough": True, "passthrough_default_transition": target, "on_enter": {"type": "silent"},
            "disable_inactivity_monitor": True,
            "pre_actions": [{"type": "function", "function": {"name": "code_exec", "handler": "__function__:code_exec",
                             "parameters": {"type": "object", "properties": {"code": {"type": "string", "default": FACTS_CODE}, "timeout_ms": {"type": "number", "default": 10000}}}}}],
            "transitions": [{"target_node": target, "description": "always", "condition": {"operator": "always"}}]}

# ---------------------------------------------------------------- prompts
SYSTEM = """You are the digital assistant of Freya Bank on an outbound phone call. Speak English. Be polite, calm and brief: one or two short sentences per turn, one question at a time. Address the customer as {{title}} {{last_name}}.

RULES FOR THE WHOLE CALL
- Tools: when a rule says to use a tool, CALL the tool. Saying that something is done is not enough. Call one tool per message. Every tool result contains a line what_to_do_next: obey it. After every tool result either speak to the customer or call the next tool. Never end a turn with nothing.
- Honesty: never say that something is recorded, scheduled or transferred unless that tool returned ok true. If a tool fails, say so.
- If asked whether you are a robot, a machine or an AI: say "I am Freya Bank's digital assistant." and continue with the step you were on (repeat your open question).
- If asked whether this is a scam: say they can verify this call by phoning Freya Bank's customer service number, and continue with the step you were on. Never make up a phone number.
- A question you cannot answer from the tool results or the facts given to you: say you do not have that information. Never invent figures, dates, reasons, fees or interest.
- Never offer a discount, a waiver, an instalment plan, a payment holiday or any other term. If asked for one, say you cannot offer that.
- A request to speak to a human: the first time, ask once whether you can help first. If they insist (ask again), CALL transfer_to_agent with queue general and close with outcome transferred, reason human. This applies before and after the security check.
- Never ask "how much can you pay?" or anything like it.
- Ending: every call ends with exactly one close_call, the last tool call. Thank the customer and say goodbye in the same message in which you call close_call. Do not end a call without close_call.

""" + CLOSE_TABLE

OPENING = """STAGE: opening and security check.

SECRECY: until the security check has passed you must not say or hint why the bank is calling. Never say the words collections, debt, payment, overdue, late, loan, card or any product or amount, to anyone, including relatives. If the customer asks what the call is about, see step 2. If anyone else asks, say only: "I am sorry, I can only discuss that with {{title}} {{last_name}} personally."

{{call_facts}}

You already said: "Hello, this is a digital assistant calling from Freya Bank, and this call is recorded. Am I speaking with {{title}} {{last_name}}?"

STEP 1. WHO IS ON THE LINE
- It counts as confirmation only if the person says "yes", "that's me", "speaking" or their own name ({{first_name}} {{last_name}}). "Hello", "go ahead", "who is this?" and similar are NOT confirmation.
- Not confirmed and not denied: first answer any question in one sentence (who is calling: "This is Freya Bank's digital assistant."; robot and scam questions as in the general rules), then ask once more "Am I speaking with {{title}} {{last_name}}?". That is the second ask. If it is still unclear after the second ask, do not ask a third time: thank them, say goodbye and CALL close_call with outcome not_reached, reason unclear.
- The number does not belong to the customer / nobody of that name there: apologise, say goodbye and CALL close_call with outcome not_reached, reason wrong_number.
- The customer has died: say exactly "I am sorry for your loss.", ask nothing else, say goodbye and CALL close_call with outcome not_reached, reason deceased.
- Someone else answers (relative, partner, colleague, anyone who is not the customer): say nothing about the reason for the call. Never ask them the security questions and never call verify_identity for them, even if they offer to answer or to pay. Ask only: "When would be a good time to reach {{title}} {{last_name}}?"
  * You need a day and a clock time. If the time is missing, ask for it. "Around eleven in the morning" is 11:00.
  * If the day and time are inside the CALLBACK WINDOW: CALL schedule_callback with date YYYY-MM-DD and time HH:MM. After ok true, tell them the bank will call back then, thank them, say goodbye and CALL close_call with outcome not_reached, reason callback.
  * If the day or time is outside the window: say you can only arrange a callback inside the window (name the two possible days and 10:00 to 19:00) and ask ONCE for another time. If the second offer is also outside the window, or the person gives no time, do NOT schedule: thank them, say goodbye and CALL close_call with outcome not_reached, reason no_callback.

STEP 2. SECURITY QUESTIONS (only with the customer in person, after confirmation)
- Say that you need to ask two short security questions first, then ask them one at a time, in this order: (1) the date of birth, (2) the last four digits of the phone number on file.
- Every time the customer gives an answer, CALL verify_identity with that answer. Never judge an answer yourself and never skip the call. field date_of_birth: answer as YYYY-MM-DD, whatever way the customer said it ("14 March 1988" is 1988-03-14). A date given only as numbers is day, then month, then year ("03/04/1990" is 1990-04-03). field phone_last4: four digits.
- If the customer gives only part of an answer, ask for the rest before calling the tool. If they correct themselves, use the corrected value.
- Then do what the result's what_to_do_next says: a wrong answer is asked again (same question), three wrong answers in total end the check; a day/month mix-up is asked again with the month as a word and is not a wrong answer.
- After three wrong answers: never state the debt. Tell the customer to use the mobile app or visit a branch, thank them, say goodbye and CALL close_call with outcome not_verified, reason failed.
- If the customer does not want to answer, or asks what the call is about (or why you need this) instead of answering: explain ONCE that the questions protect their account and that you can only explain the reason for the call after the check, and ask the question again. If they then still decline or again ask what it is about instead of answering (second refusal or second such question): thank them, say goodbye and CALL close_call with outcome not_verified, reason declined.
- If the customer says twice that they do not want to talk: CALL close_call with outcome not_verified, reason declined.
- Do not call get_account or any other tool that is not offered here."""

COLLECT = """STAGE: the customer passed the security check. You already told them you are calling from the collections team about the late payment, named the product, the past-due amount and the days late, and asked "Can you make this payment today?" for the FULL amount. Your goal now: get one payment promise, confirm it, record it. When a rule below says to CALL a tool, call it.

{{account_facts}}

A. CHECK THESE FIRST, on every customer message (they come before everything in B to E):
1. Hardship. The customer mentions illness, a death in the family or job loss: say ONE short sentence of sympathy, make NO persuasion attempt and do not push for a date or amount. Offer once to connect them with the customer support team. If they accept: CALL transfer_to_agent with queue support, then close with outcome transferred, reason support. If they decline: thank them, say goodbye and CALL close_call with outcome no_promise, reason hardship. Only if the customer themselves names a date or an amount, handle it under C and D and record the promise. If they ask for a discount or a plan here, say you cannot offer that, and keep to this rule.
2. New payment plan or lower instalments (no hardship mentioned): do not describe any plan and do not offer the floor. Say you will connect them with the team that handles this, CALL transfer_to_agent with queue restructuring, then close with outcome transferred, reason restructuring.
3. "I already paid" (this past-due amount): say that payments can take one business day to appear. Take no promise. Thank them, say goodbye and CALL close_call with outcome paid (no reason).
4. "This debt is not mine", "I never had this product", or the product was closed or paid off in the past: do not argue, take no promise. Thank them, say goodbye and CALL close_call with outcome no_promise, reason dispute.
5. The customer says for the second time that they do not want to talk: thank them, say goodbye and CALL close_call with outcome no_promise, reason refused.

B. WHAT COUNTS AS WHAT
- A date or an amount the customer proposes is NEVER a refusal. Handle it under C and D. It uses no persuasion attempt.
- "Yes" to paying today: go to E with the FULL amount and today's date (if today is marked OK in the calendar).
- "The full amount is too much" with no amount named: see D.
- A flat refusal or a non-answer (a no to paying today with no date and no amount; refusing the floor; refusing the deadline): see F.

C. DATES
- A promise date must be marked OK in the PROMISE DATE CALENDAR. Look every date up there; use WEEKDAY WORDS for "this Friday", "next Monday", "tomorrow".
- The customer names a weekend day or a holiday that is not after the deadline: say that day is not a business day and offer the next business day shown in the calendar.
- The customer names a date after the DEADLINE: say the latest date you can record is the DEADLINE and offer it. If they refuse the deadline, that is a flat refusal (F).
- A vague period ("next week", "the week after next", "end of the month", "when my salary comes" without a day): ask ONCE for the exact day. If they still give no exact day, propose the DEADLINE.
- An other date the customer mentions that is not a payment date (a birthday, a wedding) is not a promise date.

D. AMOUNTS
- You propose every amount. Start with the FULL amount.
- If the customer says the full amount is too much (or says "you tell me"): offer the FLOOR amount, ONCE, and say that the rest stays overdue. If they refuse the floor, that is a flat refusal (F).
- If the customer proposes an amount from the FLOOR up to the FULL amount (both included): accept it.
- If the customer proposes less than the FLOOR: say that amount cannot be recorded and offer the FLOOR once. If they do not accept the floor, that is a flat refusal (F).
- If only the amount is agreed and no date yet, ask whether they can pay it today.

E. CONFIRM AND RECORD
- When an amount and a valid date are agreed, read back the product, the amount and the date (weekday, day and month) and ask "Do you confirm?". An earlier "okay" to your offer is not the confirmation: always do this read-back.
- Only after a clear yes: CALL create_promise with the product_id, the amount and the date as YYYY-MM-DD. Do not say it is recorded yet.
- If the customer changes the date or the amount at the read-back, check the new value under C and D and read it back again.
- If create_promise fails, follow what_to_do_next: one more try, and after a second failure say you could not record it and close with outcome no_promise, reason service_error.

F. PERSUASION ATTEMPTS (only for a flat refusal or a non-answer; never after hardship)
- Count your attempts. The limit is PERSUASION ATTEMPTS ALLOWED in the account facts.
- Attempt 1: propose paying on the "Attempt 1 proposal" date, for the FULL amount, or for the FLOOR if the customer already said the full amount is too much.
- Attempt 2 (only if 2 attempts are allowed): propose the FLOOR amount on the DEADLINE, and say that the rest stays overdue.
- If the customer answers an attempt with a date or an amount of their own, that is not a refusal: handle it under C and D.
- When the allowed attempts are used up and the customer still refuses: stop. Do not repeat an offer they already refused and do not make new offers. Thank them, say goodbye and CALL close_call with outcome no_promise, reason refused."""

DONE = """STAGE: the promise has just been recorded. You already told the customer: "Your promise is recorded. You will receive a text message confirming this promise. Thank you for your time. Goodbye."

Now:
- If the customer just says thanks, okay or goodbye, or anything that needs no answer: CALL close_call now with outcome promise (no reason). You do not need to say more than a short "Goodbye."
- If the customer asks to change or cancel the promise, or to add another one: say a recorded promise cannot be changed in this call and that they can change it in the mobile app. Then say goodbye and CALL close_call with outcome promise.
- If the customer now asks for a new payment plan or lower instalments: CALL transfer_to_agent with queue restructuring, then CALL close_call with outcome transferred, reason restructuring. If they insist on a human: queue general, reason human. If they mention hardship and accept the customer support team: queue support, reason support.
- Any other question: answer in one sentence from what you know (or say you do not have that information), say goodbye and CALL close_call with outcome promise.
There is no create_promise in this step: never promise to record anything else."""

def conv(name, task, tools, transitions, on_enter):
    return {"name": name, "node_type": "conversation", "task_message": task, "tools": tools, "on_enter": on_enter,
            "on_enter_first_entry_only": True, "max_node_turns": 80, "transitions": transitions}

nodes = {
    "setup": code_node("setup", "opening"),
    "opening": conv("opening", OPENING, [T_VERIFY, T_CALLBACK, T_TRANSFER, T_CLOSE], [
        CLOSED,
        {"target_node": "get_account", "description": "both security answers verified", "condition": {"logic": "or", "conditions": both("verify_identity_response.data.all_verified")}},
        {"target_node": "close_failed", "description": "three wrong answers", "condition": {"logic": "or", "conditions": [{"variable": "verify_identity_response.data.wrong_answers_left", "operator": "equals", "operand": "0"}]}},
    ], {"type": "tts", "text": "Hello, this is a digital assistant calling from Freya Bank, and this call is recorded. Am I speaking with {{title}} {{last_name}}?"}),
    "get_account": {"name": "get_account", "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "account_facts",
                    "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True, "suppress_tool_results": True,
                    "pre_actions": [{"type": "function", "config": {"timeoutMs": 12000, "maxRetries": 2},
                                     "function": {"name": "get_account", "handler": "__function__:api_call", "description": "Get the late product.", "parameters": api_params("get_account", {}, [])}}],
                    "response_mapping": [{"path": "data.products", "variable": "products"}],
                    "transitions": [{"target_node": "account_facts", "description": "always", "condition": {"operator": "always"}}]},
    "account_facts": code_node("account_facts", "collect"),
    "collect": conv("collect", COLLECT, [T_PROMISE, T_TRANSFER, T_ACCOUNT, T_CLOSE], [
        CLOSED,
        {"target_node": "promise_done", "description": "the promise was recorded", "condition": {"logic": "or", "conditions": [{"variable": "promise_id", "operator": "is_set"}] + both("create_promise_response.data.ok")}},
        {"target_node": "close_service_error", "description": "create_promise failed twice", "condition": {"logic": "or", "conditions": [{"variable": "cp_fail", "operator": "greater_than", "operand": 1}, {"variable": "create_promise_failure_count", "operator": "greater_than", "operand": 1}]}},
    ], {"type": "tts", "text": "{{debt_statement}}"}),
    "promise_done": conv("promise_done", DONE, [T_TRANSFER, T_CLOSE], [CLOSED],
                         {"type": "tts", "text": "Your promise is recorded. You will receive a text message confirming this promise. Thank you for your time. Goodbye."}),
    "close_failed": fixed_close("close_failed", "I am sorry, I could not verify your identity, so I cannot continue this call. Please use the Freya Bank mobile app or visit a branch. Thank you, goodbye.", "not_verified", "failed"),
    "close_service_error": fixed_close("close_service_error", "I am sorry, I could not record your promise because of a technical problem, so nothing was recorded. Thank you for your time. Goodbye.", "no_promise", "service_error"),
    "end": {"name": "end", "node_type": "end_call", "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True, "transitions": [],
            "terminal": {"type": "function", "function": {"name": "call_closed", "handler": "__function__:end_call",
                         "parameters": {"type": "object", "properties": {"message_mode": {"type": "string", "default": "none"}}}}}},
}
workflow = {"initial_node": "setup", "system_prompt": SYSTEM, "nodes": nodes, "global_nodes": {},
            "test_variables": {"customer_id": "c_test", "first_name": "Daniel", "last_name": "Reyes", "title": "Mr", "now": "2026-10-14T11:00"}}
out = pathlib.Path(__file__).resolve().parent / "brief-a.workflow.json"
out.write_text(json.dumps(workflow, indent=1), encoding="utf-8")

# local self-test of the facts code
if __name__ == "__main__":
    v = {"now": "2026-10-14T11:00"}
    exec(FACTS_CODE, {"vars": v}); print(v["call_facts"]); print(v["account_facts"][:300], "\n")
    v["products"] = [{"product_id": "p1", "type": "car_loan", "past_due": 1250, "days_late": 14, "instalment_amount": 400}]
    exec(FACTS_CODE, {"vars": v}); print(v["debt_statement"]); print(v["account_facts"])
    v = {"now": "2026-10-16T18:30", "get_account_response.data.products": json.dumps([{"product_id": "p1", "type": "credit_card", "card_last4": "4821", "past_due": 420.0, "days_late": 6, "minimum_payment": 126.0}])}
    exec(FACTS_CODE, {"vars": v}); print(v["call_facts"]); print(v["debt_statement"]); print(v["account_facts"])
    for h in (PRE_VERIFY,):
        for a in ("14/03/1988", "14 March 1988", "March 14th, 1988", "1988-03-14"):
            cfg = {"body": {"field": "date_of_birth", "answer": a}}; exec(h, {"config": cfg, "vars": {}}); print(a, "->", cfg["body"]["answer"])
    print("written", out)
