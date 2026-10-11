"""Look after the simulations of the workspace: which are unfinished, and stop one that hangs.

    python builder/with_env.py python builder/simulations.py list
    python builder/with_env.py python builder/simulations.py cancel <simulation id>
    python builder/with_env.py python builder/simulations.py cancel-unfinished

A workspace runs one simulation at a time. Seen on 10 and 11 October: a simulation stayed
"running" for hours after all its calls had ended, and every later test waited behind it.

Stopping is done with POST /api/v2/simulations/{id}/cancel. Nothing here deletes a simulation:
deleting removes its calls and verdicts for good and stays a decision for a person.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FINAL = ("completed", "failed", "paused", "cancelled", "canceled")


def _freya():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import freya_retry
    return freya_retry.install()


def recent(limit=15):
    """The newest simulations of the workspace, newest first."""
    freya = _freya()
    result = freya.api("GET", "/api/v2/simulations", workspaceId=freya.workspace_id(), limit=limit)
    items = (result.get("simulations") or result.get("items") or []) if isinstance(result, dict) else result
    return [s for s in items if isinstance(s, dict)]


def unfinished(agent_id=None):
    """Simulations that are not in a final state (optionally only those of one agent)."""
    return [s for s in recent() if s.get("status") not in FINAL and (not agent_id or s.get("agentId") == agent_id)]


def cancel(simulation_id):
    """Ask the platform to stop one simulation. Returns (stopped, what the platform said). Never raises."""
    freya = _freya()
    try:
        answer = freya.api("POST", "/api/v2/simulations/%s/cancel" % simulation_id, {})
    except (RuntimeError, OSError) as error:
        return False, str(error)[:300]
    try:
        now = freya.api("GET", "/api/v2/simulations/%s" % simulation_id)
        now = (now.get("simulation") or now).get("status")
    except (RuntimeError, OSError) as error:
        now = "unknown (%s)" % str(error)[:80]
    return now in FINAL, "cancel accepted: %s; status now: %s" % (str(answer)[:120], now)


def cancel_unfinished(agent_id=None):
    """Stop every unfinished simulation. Returns [(id, stopped, text)]."""
    return [(s["id"],) + cancel(s["id"]) for s in unfinished(agent_id)]


def main(argv):
    if argv[:1] == ["list"]:
        for s in recent():
            print(s.get("id"), "%-10s" % s.get("status"), s.get("name"), "| created", s.get("createdAt"), "| updated", s.get("updatedAt"))
    elif argv[:1] == ["cancel"] and len(argv) == 2:
        print("%s: %s" % cancel(argv[1]))
    elif argv[:1] == ["cancel-unfinished"]:
        done = cancel_unfinished()
        for sim_id, stopped, text in done:
            print(sim_id, "STOPPED" if stopped else "NOT STOPPED", "|", text)
        if not done:
            print("no unfinished simulation")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
