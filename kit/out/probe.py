import sys, json, urllib.request, urllib.error
sys.path.insert(0, 'tools')
from score import http, HOST
def call(tool, body):
    req = urllib.request.Request(HOST + '/v1/' + tool, data=json.dumps(body).encode(), headers={'content-type': 'application/json', 'user-agent': 'Mozilla/5.0 (probe)'}, method='POST')
    try:
        r = urllib.request.urlopen(req, timeout=20); return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
s = http('/v1/practice/sessions', {'case': 'PUB-10'})
print('session', s)
c = s['variables']['customer_id']
for f, a in [('date_of_birth', '23 Feb 1989'), ('date_of_birth', '1989-23-02'), ('date_of_birth', '1989-02-24'), ('date_of_birth', '1989-02-23'), ('phone_last4', '9904')]:
    print(f, a, call('verify_identity', {'customer_id': c, 'field': f, 'answer': a}))
print(call('get_account', {'customer_id': c}))
print(call('create_promise', {'customer_id': c, 'product_id': 'p1', 'amount': 365.0, 'date': '2026-10-16'}))
print(call('create_promise', {'customer_id': c, 'product_id': 'p1', 'amount': 365.0, 'date': '2026-10-16'}))
print(call('create_promise', {'customer_id': c, 'product_id': 'p1', 'amount': 365.0, 'date': '2026-10-16'}))
print(call('transfer_to_agent', {'customer_id': c, 'queue': 'general'}))
print(call('schedule_callback', {'customer_id': c, 'date': '2026-10-15', 'time': '11:00'}))
print(call('close_call', {'customer_id': c, 'outcome': 'no_promise'}))
print(call('close_call', {'customer_id': c, 'outcome': 'promise', 'reason': 'x'}))
print(call('close_call', {'customer_id': c, 'outcome': 'promise'}))
print(call('close_call', {'customer_id': c, 'outcome': 'promise'}))
s = http('/v1/practice/sessions', {'case': 'PUB-03'}); c = s['variables']['customer_id']
for a in ['1992-21-07', '1992-07-22', '1992-07-23', '1992-07-24', '1992-07-21']:
    print(a, call('verify_identity', {'customer_id': c, 'field': 'date_of_birth', 'answer': a}))
print(call('get_account', {'customer_id': c}))
