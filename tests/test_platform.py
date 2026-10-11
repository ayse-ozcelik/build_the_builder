"""The builder's dealings with a shaky platform, checked without the platform.

    python tests\\test_platform.py

A stand-in for the kit's client answers with the errors seen on 11 October (504, 502, 503 "API key
validation is temporarily unavailable"). No request leaves this machine.
"""
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "builder"))

FAILED = []


def check(name, ok, detail=""):
    print("%s  %s%s" % ("OK  " if ok else "FAIL", name, ("   " + str(detail)) if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


class FakePlatform:
    """Fails each request `fail` times with `error`, then answers."""

    def __init__(self, fail=0, error="-> 504: error code: 504", simulations=None):
        self.fail, self.error, self.calls, self.simulations = fail, error, [], simulations or []

    def api(self, method, path, body=None, **params):
        self.calls.append((method, path))
        if self.calls.count((method, path)) <= self.fail:
            raise RuntimeError("%s %s %s" % (method, path, self.error))
        if path.endswith("/cancel"):
            sim_id = path.split("/")[-2]
            for s in self.simulations:
                if s["id"] == sim_id:
                    s["status"] = "completed"
            return {"success": True, "cancelledJobs": 10}
        if method == "GET" and path == "/api/v2/simulations":
            return {"simulations": self.simulations}
        if method == "GET" and path.startswith("/api/v2/simulations/"):
            return {"simulation": [s for s in self.simulations if s["id"] == path.split("/")[-1]][0]}
        return {"ok": True}


def client(platform):
    """A fresh copy of the wrapped client over the stand-in. Pauses are recorded, not slept."""
    fake = types.ModuleType("freya")
    fake.api = platform.api
    fake.workspace_id = lambda: "ws"
    sys.modules["freya"] = fake
    import freya_retry
    pauses = []
    freya_retry.time = types.SimpleNamespace(sleep=pauses.append)
    return freya_retry.install(), pauses


def main():
    # ---- waiting out an outage
    p = FakePlatform(fail=3)
    f, pauses = client(p)
    check("a GET that fails three times is answered on the fourth try", f.api("GET", "/api/v2/call/x") == {"ok": True})
    check("the pauses are 10, 20 and 40 seconds", pauses == [10, 20, 40], pauses)

    p = FakePlatform(fail=99)
    f, pauses = client(p)
    try:
        f.api("GET", "/api/v2/call/x")
        check("a long outage ends the request with an error", False)
    except RuntimeError:
        check("a long outage ends the request with an error after 4 tries (no endless wait)", len(p.calls) == 4 and sum(pauses) == 70, (len(p.calls), pauses))

    for path, what in (("/api/v2/simulations", "creating a simulation"), ("/api/v2/chat/message", "a chat turn")):
        p = FakePlatform(fail=1)
        f, _ = client(p)
        try:
            f.api("POST", path, {})
            check("%s is not sent again after a time-out" % what, False)
        except RuntimeError:
            check("%s is not sent again after a time-out (it may still be running)" % what, len(p.calls) == 1, p.calls)

    p = FakePlatform(fail=2, error='-> 503: {"message":"API key validation is temporarily unavailable. Retry shortly."}')
    f, _ = client(p)
    check("a simulation the platform refused at the door is sent again", f.api("POST", "/api/v2/simulations", {}) == {"ok": True} and len(p.calls) == 3)

    p = FakePlatform(fail=1, error="-> 400: bad workflow")
    f, _ = client(p)
    try:
        f.api("PUT", "/api/v2/workflow/x", {})
        check("an error that is ours (400) is not repeated", False)
    except RuntimeError:
        check("an error that is ours (400) is not repeated", len(p.calls) == 1)

    # ---- a simulation that hangs is cancelled, never deleted
    sims = [{"id": "s1", "status": "running", "agentId": "a1"}, {"id": "s2", "status": "completed", "agentId": "a1"},
            {"id": "s3", "status": "running", "agentId": "other"}]
    p = FakePlatform(simulations=sims)
    client(p)
    import simulations
    check("only unfinished simulations of our agent are found", [s["id"] for s in simulations.unfinished("a1")] == ["s1"])
    done = simulations.cancel_unfinished("a1")
    check("the hanging simulation is stopped with the cancel route", done and done[0][0] == "s1" and done[0][1] is True
          and ("POST", "/api/v2/simulations/s1/cancel") in p.calls, done)
    check("nothing is deleted", not [c for c in p.calls if c[0] == "DELETE"])
    p = FakePlatform(fail=99, simulations=[{"id": "s1", "status": "running", "agentId": "a1"}])
    client(p)
    stopped, text = simulations.cancel("s1")
    check("a cancel that fails is reported and does not raise", stopped is False and "504" in text, text)

    # ---- an outage is not a result
    import fastbuild
    row = lambda tools, slow: {"id": "X", "pass": False, "tools": tools, "slowest_turn_s": slow}
    check("no tool call and no timed turn in any call: outage", fastbuild.is_outage({"all_rows": [[row([], 0), row([], None)]]}))
    check("one call with tool calls: a real run", not fastbuild.is_outage({"all_rows": [[row([], 0), row(["verify_identity"], 3.1)]]}))
    check("calls with timed turns but no tools: a real run (the agent only talked)", not fastbuild.is_outage({"all_rows": [[row([], 4.0)]]}))
    check("an empty result is not called an outage", not fastbuild.is_outage({"all_rows": [[]]}))

    print("\n" + ("ALL CHECKS PASSED" if not FAILED else "%d CHECK(S) FAILED: %s" % (len(FAILED), ", ".join(FAILED))))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
