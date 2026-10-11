"""The date and amount engine that runs INSIDE the phone agent (in its code steps).

compile.py copies the source of `_run` into the workflow and calls it with the
parameters taken from the rules file. Nothing here knows a brief: holidays,
bands, deadlines, names and sentences all arrive in the parameters.

Written for the platform's sandbox: everything lives inside one function (the
sandbox does not share top-level names with functions), it never raises on bad
data, and the source contains no double braces.
"""


def _run(vars, params_json):
    import datetime
    import json
    import re

    P = json.loads(params_json)
    HOL = set(P.get('holidays') or [])
    BW = set(P.get('business_weekdays') or [0, 1, 2, 3, 4])
    WD = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    MN = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
          'November', 'December']
    one = datetime.timedelta(days=1)

    def isbd(d):
        return d.weekday() in BW and d.isoformat() not in HOL

    def nbd(d):
        d = d + one
        for _ in range(400):
            if isbd(d):
                break
            d = d + one
        return d

    def kth(d, k):
        for _ in range(max(0, int(k))):
            d = nbd(d)
        return d

    def say(d):
        return WD[d.weekday()] + ' ' + str(d.day) + ' ' + MN[d.month - 1] + ' ' + str(d.year)

    def both(d):
        return say(d) + ' [' + d.isoformat() + ']'

    def why(d):
        return 'a bank holiday' if d.isoformat() in HOL else 'not a business day'

    def fm(x):
        try:
            return '%.2f' % float(x)
        except Exception:
            return str(x)

    def last_bd_of_week(d):
        monday = d - one * d.weekday()
        e = monday + one * 6
        while e >= monday and not isbd(e):
            e = e - one
        return e if e >= monday else None

    def last_bd_of_month(d):
        e = (d.replace(day=28) + one * 4).replace(day=1) - one
        while e.month == d.month and not isbd(e):
            e = e - one
        return e

    def resolve(spec, today, deadline):
        kind = str((spec or dict()).get('type') or '')
        n = (spec or dict()).get('n') or 0
        if kind == 'today':
            return today
        if kind == 'next_business_day':
            return nbd(today)
        if kind == 'business_days_after_today':
            return kth(today, n)
        if kind == 'calendar_days_after_today':
            return today + one * int(n)
        if kind == 'last_business_day_of_week':
            e = last_bd_of_week(today)
            if e is None or e <= today:
                e = last_bd_of_week(today + one * 7)
            return e or nbd(today)
        if kind == 'last_business_day_of_month':
            e = last_bd_of_month(today)
            if e <= today:
                e = last_bd_of_month((today.replace(day=28) + one * 4).replace(day=1))
            return e
        return deadline or today

    def fill(template, values):
        return re.sub(r'\{(\w+)\}', lambda m: str(values.get(m.group(1), '')), str(template or ''))

    def pick(entries, kind, key):
        found = None
        for e in entries or []:
            if e.get('type') == kind:
                return e.get(key)
            if e.get('type') == '*':
                found = e.get(key)
        return found

    # ---- now
    raw = str(vars.get(P.get('now_variable') or 'now') or '').strip().replace('Z', '')
    try:
        now = datetime.datetime.fromisoformat(raw[:16])
    except Exception:
        try:
            now = datetime.datetime.fromisoformat(raw[:10])
        except Exception:
            now = datetime.datetime.utcnow()
    today = now.date()
    hhmm = '%02d:%02d' % (now.hour, now.minute)

    # ---- callback window
    cb = P.get('callback') or dict()
    lo, hi = str(cb.get('from') or '00:00'), str(cb.get('to') or '23:59')
    L = ['NOW: ' + say(today) + ', ' + hhmm + '. Use only this date and time, never the real clock.', '',
         'CALLBACK WINDOW (the only slots that may be scheduled):']
    allowed = []
    for kind in cb.get('days') or []:
        if kind == 'today':
            if hhmm < hi:
                L.append('- Today, ' + both(today) + ': a time later than ' + hhmm + ', not before ' + lo
                         + ' and not after ' + hi + ' (' + hi + ' itself is allowed).')
                allowed.append(today)
            else:
                L.append('- Today is no longer possible (it is already ' + hhmm + ').')
        else:
            d = resolve(dict(type=kind), today, None)
            L.append('- ' + both(d) + ': from ' + lo + ' to ' + hi + ' (' + lo + ' and ' + hi + ' themselves are allowed).')
            allowed.append(d)
    L.append('Any other day, and any time before ' + lo + ' or after ' + hi + ', is OUTSIDE the window.')
    L.append('Coming days: ' + '; '.join(
        (('tomorrow = ' if i == 1 else '') + both(today + one * i)
         + (' = in the window' if (today + one * i) in allowed else ' = OUTSIDE the window')) for i in range(1, 9)) + '.')
    vars['call_facts'] = '\n'.join(L)

    # ---- products, in whatever form the platform stored them
    field = str(P.get('products_field') or 'products')
    tool = str(P.get('account_tool') or '')
    prods = []
    try:
        cands = [vars.get('products'), vars.get(tool + '_response.data.' + field)]
        r = vars.get(tool + '_response')
        if isinstance(r, str):
            try:
                r = json.loads(r)
            except Exception:
                r = None
        if isinstance(r, dict):
            dd = r.get('data')
            if isinstance(dd, dict):
                cands.append(dd.get(field))
            cands.append(r.get(field))
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
                m = re.search(re.escape(field) + r'[.\[](\d+)\]?\.(\w+)$', str(k))
                if m:
                    tmp.setdefault(int(m.group(1)), dict())[m.group(2)] = vars.get(k)
            prods = [tmp[i] for i in sorted(tmp.keys())]
    except Exception:
        prods = []
    idf = str(P.get('id_field') or 'product_id')
    prods = [p for p in prods if isinstance(p, dict) and p.get(idf)]

    # ---- weekday words
    monday = today - one * today.weekday()
    ww = []
    for i in range(7):
        t = monday + one * i
        tag = ' (today)' if t == today else (' (already past, not possible)' if t < today else '')
        ww.append('this ' + WD[i] + ' = ' + both(t) + tag + '; next ' + WD[i] + ' = ' + both(t + one * 7))
    words = ('WEEKDAY WORDS (week = Monday to Sunday): tomorrow = ' + both(today + one) + '. ' + '. '.join(ww)
             + '. A weekday name with a day number means that calendar date. A weekday name alone means the first such day after today.')

    vars['product_count'] = len(prods)
    if not prods:
        vars['account_state'] = 'missing'
        vars['product_ids'] = ''
        vars['debt_statement'] = str(P.get('intro') or '')
        vars['floor_offer_line'] = ''
        vars['account_facts'] = ('ACCOUNT FACTS: NOT LOADED. CALL ' + tool + ' now, before saying anything about the account.\n'
                                 'Today: ' + both(today) + '.\n' + words)
        return

    bands = P.get('bands') or []
    proposals = sorted(P.get('proposals') or [], key=lambda x: int(x.get('attempt') or 0))
    A = ['ACCOUNT FACTS (computed by the bank system. Use them exactly as written, never recompute dates yourself):',
         '- Today: ' + both(today) + (', a business day.' if isbd(today) else ', NOT a business day: nothing can be recorded for today, offer ' + both(nbd(today)) + ' instead.')]
    S, ids, maxdl, first, offers, deadlines = [], [], today, None, [], []
    if len(prods) > 1:
        A.append('- PRODUCTS: ' + str(len(prods)) + ', always handled in this order: '
                 + ', '.join(str(p.get(idf)) for p in prods) + '. Each product has its own amounts, deadline and attempts.')
    for p in prods:
        kind = str(p.get(P.get('type_field') or 'type') or '')
        name = fill(pick(P.get('names'), kind, 'say') or kind.replace('_', ' '), p)
        full = p.get(P.get('amount_field') or '')
        floor = p.get(pick(P.get('floor_fields'), kind, 'field') or '')
        if floor is None:
            floor = full
        try:
            late = float(p.get(P.get('lateness_field') or '') or 0)
        except Exception:
            late = 0.0
        band = None
        for b in bands:
            try:
                if float(b.get('min')) <= late <= float(b.get('max')):
                    band = b
                    break
            except Exception:
                pass
        if band is None and bands:
            band = bands[-1] if late > float(bands[-1].get('max') or 0) else bands[0]
        band = band or dict()
        dead = resolve(band.get('deadline'), today, None)
        tries = int(band.get('attempts') or 0)
        if dead > maxdl:
            maxdl = dead
        ids.append(str(p.get(idf)))
        deadlines.append((name, dead))
        latetext = str(int(late)) if late == int(late) else str(late)
        A.append('- Product: ' + name + ' (' + idf + ' ' + str(p.get(idf)) + '). Always name it in these words.')
        A.append('  FULL amount: ' + fm(full) + '. ' + str(P.get('lateness_field') or 'lateness') + ': ' + latetext + '.')
        A.append('  FLOOR amount: ' + fm(floor) + '. Amounts from ' + fm(floor) + ' up to ' + fm(full)
                 + ', both included, can be recorded. Nothing below ' + fm(floor) + ' and nothing above ' + fm(full) + '.')
        A.append('  DEADLINE (latest date that can be recorded): ' + both(dead) + '.')
        A.append('  PERSUASION ATTEMPTS ALLOWED: ' + str(tries) + '.')
        avail, said, stopped = [], [], False
        for pr in proposals:
            k = int(pr.get('attempt') or 0)
            scope = pr.get('bands') or []
            if k > tries or (scope and band.get('name') not in scope):
                if len(prods) == 1:
                    A.append('  Attempt ' + str(k) + ' proposal: NOT available for this product.')
                elif not stopped:
                    # several products: the stop stands in the list itself, where the next proposal would be
                    A.append('  After that: NOTHING. No further proposal for this product and no FLOOR offer. It is refused.')
                    stopped = True
                continue
            d = resolve(pr.get('date'), today, dead)
            if d > dead:
                d = dead
            how = str(pr.get('amount') or 'full')
            amount = ('the FLOOR amount ' + fm(floor)) if how == 'floor' else ('the FULL amount ' + fm(full))
            if how == 'full_or_floor':
                amount += ', or the FLOOR amount ' + fm(floor) + ' if the customer already said the full amount is too much'
            A.append('  Attempt ' + str(k) + ' proposal: payment on ' + both(d) + ' of ' + amount + '.')
            avail.append(k)
            said.append('Attempt ' + str(k) + ': payment on ' + both(d) + ' of ' + amount)
        if len(prods) > 1:
            if not stopped:
                A.append('  After that: NOTHING. No further proposal for this product and no FLOOR offer. It is refused.')
            steps =['refusal ' + str(i + 1) + ' -> make the Attempt ' + str(k) + ' proposal' for i, k in enumerate(avail)]
            steps.append('refusal ' + str(len(avail) + 1) + ' -> STOP: propose nothing more for this product, not the FLOOR'
                         ' either; it is refused')
            A.append('  IF THE CUSTOMER REFUSES THIS PRODUCT (and no rule says to make no attempt): ' + '. '.join(steps)
                     + '. A refusal the customer already gave for this product earlier in the call counts as refusal 1.')
            # the tool hooks repeat this right before the product's turn comes
            vars['script_' + str(p.get(idf))] = (
                'NEXT PRODUCT: ' + name + ' (' + str(p.get(idf)) + '). After a refusal you may make exactly ' + str(len(avail))
                + ' proposal(s) for it, no more: ' + '; then '.join(said) + '. If the customer refuses after that, it is refused:'
                ' propose nothing else for it and act on the refusal at once. The FLOOR offer is only for a customer who says'
                ' the amount is too much. "No" or "I am not paying" is a refusal, never a reason to offer the FLOOR.')
        values = dict(p)
        values.update(name=name, amount=fm(full), floor=fm(floor), lateness=latetext)
        S.append(fill(P.get('product_sentence'), values))
        offers.append('  - ' + name + ': "' + fill(P.get('floor_offer_line'), values) + '"')
        if first is None:
            first = values
    A.append('')
    if len(prods) == 1:
        A.append('FLOOR OFFER: never say it yourself. To make it, CALL transition_to_floor_gate; the system then says it.')
    else:
        A.append('FLOOR OFFER (at most ' + str(P.get('floor_offer_limit') or 1) + ' time(s) per product; say exactly this sentence and do not call transition_to_floor_gate).'
                 ' Make it ONLY when the customer says the full amount of that product is too much, asks you to name an amount, or'
                 ' proposes less than its FLOOR. A plain refusal to pay is NOT a reason for it: a refusal gets the next attempt'
                 ' proposal of that product, if one is left.')
        A.extend(offers)
    A.append('')
    A.append('DATE CALENDAR (date: can it be recorded?):')
    d = today
    while d <= maxdl:
        if isbd(d):
            # with several products a date can be fine for one and too late for another
            late_for = [n + ' (its latest date is ' + both(x) + ')' for n, x in deadlines if d > x] if len(prods) > 1 else []
            A.append('- ' + both(d) + (' (today)' if d == today else '') + ': OK'
                     + ((' except for: ' + '; '.join(late_for)) if late_for else ''))
        else:
            A.append('- ' + both(d) + (' (today)' if d == today else '') + ': NOT possible, ' + why(d)
                     + ' -> offer the next business day ' + both(nbd(d)))
        d = d + one
    A.append('- Any date after ' + both(maxdl) + ': NOT possible, after the deadline -> say the latest date you can record is '
             + say(maxdl) + ' and offer it.')
    A.append('- Any date before today: not possible.')
    A.append('')
    A.append(words)
    vars['account_state'] = 'loaded'
    vars['product_ids'] = ','.join(ids)
    vars['account_facts'] = '\n'.join(A)
    vars['debt_statement'] = ' '.join([str(P.get('intro') or '')] + S + [str(P.get('first_ask') or '')]).strip()
    vars['floor_offer_line'] = fill(P.get('floor_offer_line'), first or dict())
