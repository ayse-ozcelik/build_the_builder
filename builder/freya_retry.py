"""Make the kit's Freya client wait out a short outage of the platform instead of failing.

    import freya_retry; freya_retry.install()

    python builder/freya_retry.py <args of kit/tools/run_cases.py>     the practice customers, with the same patience

Seen on 11 October: the platform answered 504, 502 and once 401 ("Invalid API key") for a few
minutes, in the middle of a test and during an export. The kit's client (kit/freya.py, not ours
to edit) raises at the first such answer. This wraps its one request function: a request that
is safe to send twice is sent again, three more times at most, after 10, 20 and 40 seconds.
It never waits longer than that: a long outage must end the step, not hang it.

Creating a simulation, personas or a workflow is repeated only when the platform refused the
request at the door (401, 429, 503: "API key validation is temporarily unavailable"). After a
time-out (504 and the like) it may have been created, and sent twice it would exist twice.
The same holds for a chat turn: after a 504 the platform may still be working on that turn.
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.path.join(ROOT, "kit")
PASSING = ("-> 500", "-> 502", "-> 503", "-> 504", "-> 520", "-> 522", "-> 524", "-> 429", "-> 401")
ONCE_ONLY = ("/api/v2/simulations", "/api/v2/personas", "/api/v2/workflow", "/api/v2/chat/message")
REFUSED = ("-> 503", "-> 429", "-> 401")
RETRIES, FIRST_PAUSE = 3, 10   # pauses of 10, 20 and 40 seconds


def install(retries=RETRIES, first_pause=FIRST_PAUSE):
    if KIT not in sys.path:
        sys.path.insert(0, KIT)
    import freya
    if getattr(freya.api, "waits_out_outages", False):
        return freya
    plain = freya.api

    def api(method, path, body=None, **params):
        creates = method == "POST" and path in ONCE_ONLY and not params.get("dryRun")
        tries = retries + 1
        for attempt in range(1, tries + 1):
            try:
                return plain(method, path, body, **params)
            except (RuntimeError, OSError) as error:
                if creates:
                    passing = isinstance(error, RuntimeError) and any(code in str(error) for code in REFUSED)
                else:
                    passing = isinstance(error, OSError) or any(code in str(error) for code in PASSING)
                if not passing or attempt == tries:
                    raise
                pause = first_pause * 2 ** (attempt - 1)
                print("platform: %s %s failed (%s), try %d of %d in %d s" % (
                    method, path, str(error)[-60:].replace("\n", " "), attempt + 1, tries, pause), flush=True)
                time.sleep(pause)

    api.waits_out_outages = True
    freya.api = api
    return freya


if __name__ == "__main__":
    import runpy
    install()
    script = os.path.join(KIT, "tools", "run_cases.py")
    sys.path.insert(0, os.path.dirname(script))
    sys.argv = [script] + sys.argv[1:]
    runpy.run_path(script, run_name="__main__")
