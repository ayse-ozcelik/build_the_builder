"""Write mock/openapi.json: the tool API file of our mock, taken from the bank's service contract.
    python make_openapi.py <repo root> <mock url>
"""
import json
import sys

root, url = sys.argv[1], sys.argv[2].rstrip("/")
CARD = {"type": "string", "description": "The card number exactly as cards.list returned it in cardNumber (masked, like 540000******1234). Never rebuilt from digits the caller says."}
CUSTOMER = {"type": "string", "description": "The customer number the phone menu handed over with the call (8 digits)."}
WEEK = {"type": "integer", "enum": [1, 2, 3, 4], "description": "The requested statement week, 1 to 4. Never the week the card already has."}
DAY = {"type": "integer", "description": "The card's current statementDay from statement.week.get."}
CALL = {"type": "object", "description": "Call metadata the bank logs; nothing depends on it.",
        "properties": {"lang": {"type": "string", "default": "tr"}}}
RESULT = {"type": "object", "description": "Present in every answer. Success = HTTP 200 and returnCode \"1\". Anything else is an error; an error answer has no data object. messageText is for logs and is not read to the caller.",
          "properties": {"guid": {"type": "string"}, "returnCode": {"type": "string"}, "messageText": {"type": "string"},
                         "code": {"type": "string"}, "message": {"type": "string"}, "type": {"type": "string", "description": "ERROR or WARNING on errors"},
                         "details": {"type": "object"}}}
ERRORS = {"400": "INVALID_ARGUMENTS_ERROR: a required field is missing or empty", "500": "FBE00099: technical error on the bank's side"}
DEBT = {"type": "object", "description": "One block per currency. A block can be missing; a missing block is not a block with NO_DEBT.",
        "properties": {k: {"type": "number"} for k in ("transferAmount", "statementDebt", "minPayment", "remainingDebt", "remainingMinPayment",
                                                         "currentPeriodExpense", "totalDebt", "remainAutomaticPaymentAmount")}}
DEBT["properties"].update(statementPaymentStatus={"type": "string", "enum": ["HAS_DEBT", "NO_DEBT"]},
                          totalDebtPaymentStatus={"type": "string", "enum": ["HAS_DEBT", "NO_DEBT"]})
DATE = {"type": "string", "description": "dd.MM.yyyy"}


def service(name, summary, data, required, answer, errors):
    responses = {"200": {"description": "Success", "content": {"application/json": {"schema": {
        "type": "object", "properties": {"data": {"type": "object", "properties": answer}, "result": RESULT}}}}}}
    for code, text in dict(ERRORS, **errors).items():
        responses[code] = {"description": text, "content": {"application/json": {"schema": {"type": "object", "properties": {"result": RESULT}}}}}
    return {"post": {"operationId": name.replace(".", "_"), "summary": summary, "description": summary,
                     "requestBody": {"required": True, "content": {"application/json": {"schema": {
                         "type": "object", "required": ["data"],
                         "properties": {"data": {"type": "object", "required": required, "properties": data}, "call": CALL}}}}},
                     "responses": responses}}


paths = {
    "/voice/v1/cards.list": service(
        "cards.list", "Returns the customer's cards the assistant may talk about. The first service of every call.",
        {"customerNo": CUSTOMER, "aiFlow": {"type": "string", "default": "CardOps"}}, ["customerNo"],
        {"cards": {"type": "array", "description": "Only active cards. Two cards can have the same productName and the same cardNumberLast4.",
                   "items": {"type": "object", "properties": {"cardNumber": {"type": "string"}, "cardNumberLast4": {"type": "string"},
                                                              "productName": {"type": "string", "description": "free text from the card system"},
                                                              "status": {"type": "string"}, "isMainCard": {"type": "boolean"}}}},
         "validCardsCount": {"type": "integer"}, "exitState": {"type": "string", "enum": ["1_CARD", "MULTI_CARD"]}},
        {"404": "FB_IVR_CHANNEL_102 (WARNING): the customer has no card the assistant can work with"}),
    "/voice/v1/cards.balance": service(
        "cards.balance", "Returns the statement date, the due date and the debt per currency of one card.",
        {"cardNumber": CARD}, ["cardNumber"],
        {"cardNumber": {"type": "string", "description": "masked differently from cards.list; do not match on it"}, "cardNumberLast4": {"type": "string"},
         "productName": {"type": "string"}, "statementDate": DATE, "lastPayDate": DATE, "nextStatementDate": DATE, "nextLastPayDate": DATE,
         "debtTL": DEBT, "debtUSD": DEBT, "debtEUR": DEBT, "debtGBP": DEBT,
         "statementPaymentStatus": {"type": "string", "enum": ["HAS_DEBT", "NO_DEBT"]},
         "totalDebtPaymentStatus": {"type": "string", "enum": ["HAS_DEBT", "NO_DEBT"]},
         "automaticPaymentFlag": {"type": "string", "enum": ["Y", "N"]}},
        {"404": "FB_IVR_CHANNEL_120 (WARNING): card not found"}),
    "/voice/v1/statement.week.get": service(
        "statement.week.get", "Returns the card's current statement week and day, and whether it shares its statement with another card.",
        {"cardNumber": CARD}, ["cardNumber"],
        {"cardNumber": {"type": "string"}, "cardNumberLast4": {"type": "string"}, "productName": {"type": "string"},
         "statementWeek": {"type": "integer", "description": "1 to 4"}, "statementDay": {"type": "integer"},
         "statementDate": DATE, "lastPayDate": DATE, "nextStatementDate": DATE, "nextLastPayDate": DATE,
         "relationType": {"type": "string", "description": "empty: own statement. M: main card of a shared statement. C: the card linked to it."},
         "relatedCardNumber": {"type": "string"}, "relatedCardProductName": {"type": "string"}},
        {"404": "FB_IVR_CHANNEL_121 (WARNING): card not found"}),
    "/voice/v1/statement.week.decide": service(
        "statement.week.decide", "Preview only: the statement date, due date and day the card would get with the requested week. It does not change the card.",
        {"cardNumber": CARD, "newStatementWeek": WEEK, "statementDay": DAY}, ["cardNumber", "newStatementWeek", "statementDay"],
        {"statementDate": DATE, "statementDueDate": DATE, "statementDay": {"type": "integer"},
         "responseCode": {"type": "integer", "description": "0 = fine"}, "responseMessage": {"type": "string"}}, {}),
    "/voice/v1/statement.week.change": service(
        "statement.week.change", "Changes the card's statement week. Only after the caller has agreed to the dates statement.week.decide returned. Has a side effect: never sent twice.",
        {"cardNumber": CARD, "newStatementWeek": WEEK, "statementDay": DAY}, ["cardNumber", "newStatementWeek", "statementDay"],
        {"responseCode": {"type": "integer", "description": "0 = fine"}, "responseMessage": {"type": "string"},
         "statementWeek": {"type": "string"}, "statementDay": {"type": "string", "description": "ignore; the dates to tell the caller are those of statement.week.decide"},
         "exitState": {"type": "string", "description": "OK on success"}}, {"401": "token missing or expired"}),
    "/voice/v1/contact.email.get": service(
        "contact.email.get", "Returns the customer's registered e-mail address and phone.",
        {"customerNo": CUSTOMER, "intAddressInfoType": {"type": "string", "default": "E"}}, ["customerNo", "intAddressInfoType"],
        {"communicationType": {"type": "string"}, "ibanCountryCode": {"type": "string"}, "ibanAreaCode": {"type": "string"},
         "ibanPhoneNum": {"type": "string"}, "ibanEmail": {"type": "string", "description": "the registered e-mail address; empty when none is registered"},
         "ttsEmail": {"type": "string"}, "commGramType": {"type": "string"}, "exitState": {"type": "string"}}, {}),
    "/voice/v1/statement.pdf.send": service(
        "statement.pdf.send", "Sends the statement PDF by e-mail to the address in the request. Has a side effect: never sent twice.",
        {"customerNo": CUSTOMER, "cardNumber": CARD,
         "currencyType": {"type": "string", "enum": ["TL", "USD", "EUR", "GBP"]},
         "statementDate": {"type": "string", "description": "the statementDate from cards.balance, dd.MM.yyyy"},
         "emailAddress": {"type": "string", "description": "the address the statement is sent to"}},
        ["customerNo", "cardNumber", "currencyType", "statementDate", "emailAddress"],
        {"exitState": {"type": "string", "description": "DONE on success"}},
        {"404": "FB_IVR_CHANNEL_107 (WARNING): no statement document to send"}),
}
spec = {"openapi": "3.0.3",
        "info": {"title": "Freya Bank voice assistant: statement and debt services (mock)", "version": "2026-10-01",
                 "description": "Our mock of the seven services of the bank's service contract of 1 October 2026. Every service is POST with {data, call}; every answer has result, successful ones also data."},
        "servers": [{"url": url}], "paths": paths}
with open(root + "/mock/openapi.json", "w", encoding="utf-8", newline="\n") as f:
    json.dump(spec, f, indent=1, ensure_ascii=False)
print("wrote mock/openapi.json for", url)
