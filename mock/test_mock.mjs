// Every answer of the case pack through the mock:  node mock/test_mock.mjs <test-customers.json> [https://deployed-address]
// Without an address the Worker code is called here, in this process. With one, the deployed Worker is called.
import { readFileSync } from "node:fs";
import worker from "./src/worker.js";

const pack = JSON.parse(readFileSync(process.argv[2], "utf8"));
const remote = (process.argv[3] || "").replace(/\/+$/, "");
const skipDelay = !remote;
let checked = 0;
const failed = [];

async function call(service, data, method = "POST", path = "/voice/v1/" + service) {
  const init = { method, headers: { "content-type": "application/json" } };
  if (method === "POST") init.body = JSON.stringify({ data, call: { ani: "0", lang: "tr" } });
  if (remote) {
    for (let attempt = 1; ; attempt += 1) {   // a dropped connection is tried again; a wrong answer is not
      try {
        return await fetch(remote + path, init);
      } catch (error) {
        if (attempt === 4) throw error;
        await new Promise((done) => setTimeout(done, 1500 * attempt));
      }
    }
  }
  return worker.fetch(new Request("https://mock.test" + path, init));
}

async function expect(name, service, data, http, body) {
  const response = await call(service, data);
  const got = await response.json();
  checked += 1;
  if (response.status !== http || (body !== undefined && JSON.stringify(got) !== JSON.stringify(body))) {
    failed.push(name + ": HTTP " + response.status + " " + JSON.stringify(got).slice(0, 160));
  }
  return got;
}

if (skipDelay) {   // the one delayed answer is checked for its body here; the wait itself is checked against the deployed Worker
  const real = globalThis.setTimeout;
  globalThis.setTimeout = (fn, ms, ...rest) => real(fn, ms > 500 ? 0 : ms, ...rest);
}

for (const customer of pack.customers) {
  const no = customer.customerNo;
  for (const [service, entry] of Object.entries(customer.services)) {
    if ("http" in entry) {
      await expect(no + " " + service, service, { customerNo: no, aiFlow: "CardOps", intAddressInfoType: "E" }, entry.http, entry.response);
      await expect(no + " " + service + " (number)", service, { customerNo: Number(no) }, entry.http, entry.response);
      continue;
    }
    for (const [card, below] of Object.entries(entry)) {
      if ("http" in below) {
        await expect(no + " " + service + " " + card, service, { cardNumber: card }, below.http, below.response);
        continue;
      }
      for (const [week, answer] of Object.entries(below)) {
        await expect(no + " " + service + " " + card + " week " + week, service, { cardNumber: card, newStatementWeek: week, statementDay: 5 }, answer.http, answer.response);
        await expect(no + " " + service + " " + card + " week " + week + " (number)", service, { cardNumber: card, newStatementWeek: Number(week) }, answer.http, answer.response);
      }
      const current = ["1", "2", "3", "4"].filter((week) => !(week in below));
      for (const week of current) {
        const got = await expect(no + " " + service + " " + card + " current week " + week, service, { cardNumber: card, newStatementWeek: week }, 400);
        if (got.result.code !== "INVALID_ARGUMENTS_ERROR" || "data" in got) failed.push("current week: wrong error body");
      }
    }
  }
  if (!("statement.pdf.send" in customer.services)) {
    const got = await expect(no + " statement.pdf.send (no entry)", "statement.pdf.send", { customerNo: no }, 404);
    if (got.result.code !== "FB_IVR_CHANNEL_107") failed.push(no + ": pdf.send without entry gave " + got.result.code);
  }
}

// not in the pack: the contract's error for each service, and never a data object
const unknown = [
  ["cards.list", { customerNo: "10000000" }, 404, "FB_IVR_CHANNEL_102", "WARNING"],
  ["cards.balance", { cardNumber: "999999******0000" }, 404, "FB_IVR_CHANNEL_120", "WARNING"],
  ["statement.week.get", { cardNumber: "999999******0000" }, 404, "FB_IVR_CHANNEL_121", "WARNING"],
  ["statement.week.decide", { cardNumber: "999999******0000", newStatementWeek: 2 }, 400, "INVALID_ARGUMENTS_ERROR", "ERROR"],
  ["statement.week.change", { cardNumber: "999999******0000", newStatementWeek: 2 }, 400, "INVALID_ARGUMENTS_ERROR", "ERROR"],
  ["contact.email.get", { customerNo: "10000000", intAddressInfoType: "E" }, 400, "INVALID_ARGUMENTS_ERROR", "ERROR"],
  ["statement.pdf.send", { customerNo: "10000000" }, 404, "FB_IVR_CHANNEL_107", "WARNING"],
  ["cards.list", {}, 400, "INVALID_ARGUMENTS_ERROR", "ERROR"],
  ["cards.balance", { cardNumber: "" }, 400, "INVALID_ARGUMENTS_ERROR", "ERROR"],
  ["statement.week.decide", { cardNumber: "540000******1234" }, 400, "INVALID_ARGUMENTS_ERROR", "ERROR"],
];
for (const [service, data, http, code, type] of unknown) {
  const got = await expect("unknown " + service, service, data, http);
  const r = got.result || {};
  if (r.code !== code || r.returnCode !== code || r.type !== type || r.message !== r.messageText || r.guid !== r.details.guid || "data" in got) {
    failed.push("unknown " + service + ": " + JSON.stringify(got));
  }
}
let response = await call("", null, "GET", "/health");
checked += 1;
if (response.status !== 200 || !(await response.json()).ok) failed.push("/health");
response = await call("cards.list", { customerNo: "{{customerNumber}}" });
checked += 1;
if (response.status !== 200) failed.push("an unfilled variable should give the first record");
response = await call("nothing.here", {});
checked += 1;
if (response.status !== 404) failed.push("unknown path");

if (remote) {
  const slow = pack.customers.flatMap((c) => Object.entries(c.services["cards.balance"] || {})).find(([, a]) => a.delaySeconds);
  if (slow) {
    const started = Date.now();
    await expect("delayed answer", "cards.balance", { cardNumber: slow[0] }, slow[1].http, slow[1].response);
    const seconds = (Date.now() - started) / 1000;
    if (seconds < slow[1].delaySeconds) failed.push("the delayed answer came after " + seconds + " s, expected " + slow[1].delaySeconds);
    console.log("delayed answer: " + seconds.toFixed(1) + " s");
  }
}

console.log((remote || "in process") + ": " + checked + " requests, " + failed.length + " wrong");
for (const line of failed.slice(0, 20)) console.log("  - " + line);
process.exit(failed.length ? 1 : 0);
