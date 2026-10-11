"""The date and amount engine that runs INSIDE the phone agent (in its code steps).

compile.py copies the source of `_run` into the workflow and calls it with the
parameters that HiddenBriefAdapter (below) takes from the rules file. Nothing here knows a brief: holidays,
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
    S, ids, maxdl, first, deadlines, total = [], [], today, None, [], 0.0
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
        okd, d = [], today
        while d <= dead:
            if isbd(d):
                okd.append(d.isoformat())
            d = d + one
        vars['ok_dates_' + str(p.get(idf))] = ','.join(okd)
        vars['floor_' + str(p.get(idf))] = fm(floor)
        vars['full_' + str(p.get(idf))] = fm(full)
        try:
            total = total + float(full)
        except Exception:
            pass
        latetext = str(int(late)) if late == int(late) else str(late)
        A.append('- Product: ' + name + ' (' + idf + ' ' + str(p.get(idf)) + '). Always name it in these words.')
        A.append('  FULL amount: ' + fm(full) + '. ' + str(P.get('lateness_field') or 'lateness') + ': ' + latetext + '.')
        A.append('  FLOOR amount: ' + fm(floor) + '. Amounts from ' + fm(floor) + ' up to ' + fm(full)
                 + ', both included, can be recorded. Nothing below ' + fm(floor) + ' and nothing above ' + fm(full) + '.')
        A.append('  DEADLINE (latest date that can be recorded): ' + both(dead) + '.')
        A.append('  PERSUASION ATTEMPTS ALLOWED: ' + str(tries) + '.')
        avail, stopped = [], False
        for pr in proposals:
            k = int(pr.get('attempt') or 0)
            scope = pr.get('bands') or []
            if k > tries or (scope and band.get('name') not in scope):
                if not stopped:
                    # the stop stands in the list itself, where the next proposal would be
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
        if not stopped:
            A.append('  After that: NOTHING. No further proposal for this product and no FLOOR offer. It is refused.')
        steps = ['refusal ' + str(i + 1) + ' -> make the Attempt ' + str(k) + ' proposal' for i, k in enumerate(avail)]
        steps.append('refusal ' + str(len(avail) + 1) + ' -> STOP: propose nothing more for this product, not the FLOOR'
                     ' either; it is refused')
        A.append('  IF THE CUSTOMER REFUSES THIS PRODUCT (and no rule says to make no attempt): ' + '. '.join(steps)
                 + '. A refusal the customer already gave for this product earlier in the call counts as refusal 1.')
        if len(prods) > 1:
            # the tool hooks repeat this right before the product's turn comes
            vars['script_' + str(p.get(idf))] = (
                'If ' + name + ' (' + str(p.get(idf)) + ') is still to be discussed: after a refusal exactly ' + str(len(avail))
                + ' proposal(s), as its Attempt line(s) say, then stop. No FLOOR offer after a plain "no".')
        values = dict(p)
        values.update(name=name, amount=fm(full), floor=fm(floor), lateness=latetext)
        S.append(fill(P.get('product_sentence'), values))
        if len(prods) > 1:
            # several products: the offer stands with its product, behind its condition, and nowhere else
            A.append('  FLOOR OFFER, at most ' + str(P.get('floor_offer_limit') or 1) + ' time(s) for this product and ONLY IF the customer'
                     ' says its amount is too much, asks you to name an amount, or proposes less than ' + fm(floor)
                     + ' (NEVER after a plain "no" or "I am not paying": that is a refusal, see above). Then say exactly: "'
                     + fill(P.get('floor_offer_line'), values) + '"')
        if first is None:
            first = values
    if len(prods) > 1:
        A.append('- TOTAL of the FULL amounts of all products: ' + fm(total) + '. Never add amounts up yourself.')
    A.append('')
    if len(prods) == 1:
        A.append('FLOOR OFFER: never say it yourself. To make it, CALL transition_to_floor_gate; the system then says it.')
    else:
        A.append('FLOOR OFFER: only as written under each product above. Do not call transition_to_floor_gate.')
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
    if not vars.get('settle_summary'):
        vars['settle_summary'] = 'nothing was recorded for any product yet'
    vars['product_ids'] = ','.join(ids)
    vars['account_facts'] = '\n'.join(A)
    vars['debt_statement'] = ' '.join([str(P.get('intro') or '')] + S + [str(P.get('first_ask') or '')]).strip()
    vars['floor_offer_line'] = fill(P.get('floor_offer_line'), first or dict())


class HiddenBriefAdapter:
    """The one door through which a brief's values reach the engine above.

        adapter = HiddenBriefAdapter(rules)      rules = the file rules.py filled from the brief files
        adapter.params()                         the parameters of `_run`: calendar, bands, proposals, names, sentences
        adapter.problems()                       what in this brief the engine cannot express, in plain sentences
        adapter.preview(now, products)           the facts a call would get, computed here, without the platform

        python builder/runtime_facts.py <rules.json> [--now 2026-10-14T11:00]

    It takes its values from the rules file and from nowhere else. A new brief (other holidays, other
    limits, other deadlines, other sentences) therefore needs no change here or in `_run`: build it with
    fastbuild.py and everything arrives through this class. Nobody types a brief's value into the code,
    which is also what the event's rules ask for once the final briefs are open.
    """
    DATE_TYPES = ("today", "next_business_day", "business_days_after_today", "calendar_days_after_today",
                  "last_business_day_of_week", "last_business_day_of_month", "deadline")
    CALLBACK_DAYS = ("today", "next_business_day")

    def __init__(self, rules, account_tool="get_account"):
        self.rules = rules
        self.account_tool = account_tool

    def params(self):
        rules, account = self.rules, self.rules["account"]
        return {
            "now_variable": rules["now_variable"],
            "holidays": rules["calendar"].get("holidays") or [],
            "business_weekdays": rules["calendar"]["business_weekdays"],
            "callback": rules["callback"],
            "account_tool": self.account_tool,
            "products_field": account["products_field"], "id_field": account["id_field"], "type_field": account["type_field"],
            "amount_field": account["amount_field"], "lateness_field": account["lateness_field"],
            "names": account["names"], "floor_fields": account["floor_fields"],
            "intro": account["intro"], "product_sentence": account["product_sentence"], "first_ask": account["first_ask"],
            "bands": rules["bands"], "proposals": rules["proposals"],
            "floor_offer_line": rules["floor_offer"]["line"], "floor_offer_limit": rules["floor_offer"]["limit"],
        }

    def params_json(self):
        import json
        # indent=1 keeps closing braces on separate lines, so the text never holds two in a row
        return json.dumps(self.params(), indent=1)

    def problems(self):
        """Sentences about values the engine would handle differently from what the brief may mean. Empty = none found."""
        import datetime
        found, rules = [], self.rules
        say = found.append
        days = (rules.get("calendar") or {}).get("business_weekdays") or []
        if not days or any(not isinstance(d, int) or d < 0 or d > 6 for d in days):
            say("calendar.business_weekdays must be numbers from 0 (Monday) to 6 (Sunday); got %r" % (days,))
        for day in (rules.get("calendar") or {}).get("holidays") or []:
            try:
                datetime.date.fromisoformat(str(day))
            except ValueError:
                say("holiday %r is not a date (YYYY-MM-DD) and is ignored" % (day,))
        bands = rules.get("bands") or []
        if not bands:
            say("no bands: every product gets today as its deadline and no attempt")
        spans = []
        for band in bands:
            kind = (band.get("deadline") or {}).get("type")
            if kind not in self.DATE_TYPES or kind == "deadline":
                say("band %r: deadline type %r is unknown to the engine, the deadline becomes today" % (band.get("name"), kind))
            try:
                spans.append((float(band["min"]), float(band["max"]), band.get("name")))
            except (KeyError, TypeError, ValueError):
                say("band %r has no numeric min and max and never matches" % (band.get("name"),))
        spans.sort()
        for (lo1, hi1, n1), (lo2, hi2, n2) in zip(spans, spans[1:]):
            if lo2 <= hi1:
                say("bands %r and %r overlap: the one listed first wins" % (n1, n2))
            elif lo2 > hi1 + 1:
                say("no band covers lateness between %g and %g: the first band is used there" % (hi1, lo2))
        most = max([int(b.get("attempts") or 0) for b in bands] or [0])
        numbers = sorted(int(p.get("attempt") or 0) for p in rules.get("proposals") or [])
        for n in range(1, most + 1):
            if n not in numbers:
                say("a band allows %d attempt(s) but the brief gave no proposal for attempt %d" % (most, n))
        for proposal in rules.get("proposals") or []:
            if (proposal.get("date") or {}).get("type") not in self.DATE_TYPES:
                say("proposal %r: date type %r is unknown to the engine, the deadline is proposed"
                    % (proposal.get("attempt"), (proposal.get("date") or {}).get("type")))
            if int(proposal.get("attempt") or 0) > most:
                say("proposal %r is never used: no band allows that many attempts" % (proposal.get("attempt"),))
        callback = rules.get("callback") or {}
        for day in callback.get("days") or []:
            if day not in self.CALLBACK_DAYS and day not in self.DATE_TYPES:
                say("callback day %r is unknown to the engine" % (day,))
        if str(callback.get("from") or "") >= str(callback.get("to") or "99"):
            say("callback window from %r to %r is empty" % (callback.get("from"), callback.get("to")))
        account = rules.get("account") or {}
        if not any(e.get("type") == "*" for e in account.get("floor_fields") or []):
            kinds = sorted(str(e.get("type")) for e in account.get("floor_fields") or [])
            say("a product type other than %s has no lowest amount of its own: its full amount is used" % ", ".join(kinds))
        return found

    def preview(self, now, products):
        """Run the engine here on one account. Returns the variables a call would have after the account is loaded."""
        variables = {self.rules["now_variable"]: now, "products": products}
        _run(variables, self.params_json())
        return variables

    def sample_products(self):
        """One made-up product per band, so that a brief can be looked at before any customer exists."""
        account, out = self.rules["account"], []
        for i, band in enumerate(self.rules.get("bands") or []):
            kind = (account["names"][i % len(account["names"])].get("type") if account.get("names") else "") or "product"
            product = {account["id_field"]: "p%d" % (i + 1), account["type_field"]: kind,
                       account["amount_field"]: 1000.0, account["lateness_field"]: band.get("min")}
            for entry in account.get("floor_fields") or []:
                if entry.get("type") in (kind, "*"):
                    product[entry["field"]] = 300.0
            out.append(product)
        return out


if __name__ == "__main__":
    import argparse
    import json
    import time
    parser = argparse.ArgumentParser(description="Show what the date and amount engine makes of one rules file.")
    parser.add_argument("rules")
    parser.add_argument("--now", default=time.strftime("%Y-%m-%dT%H:%M"))
    args = parser.parse_args()
    with open(args.rules, encoding="utf-8") as f:
        adapter = HiddenBriefAdapter(json.load(f))
    found = adapter.problems()
    print("PROBLEMS: " + ("none found" if not found else "".join("\n- " + line for line in found)))
    seen = adapter.preview(args.now, adapter.sample_products())
    print("\nSAMPLE ACCOUNT (one made-up product per band), call at %s\n" % args.now)
    print(seen.get("call_facts", "") + "\n\n" + seen.get("debt_statement", "") + "\n\n" + seen.get("account_facts", ""))
