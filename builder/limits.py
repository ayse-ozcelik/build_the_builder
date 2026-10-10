"""Limits from a brief ("at most N tries", "only N times") enforced by code, not by the model.

A limit is always built from the same two parts:

1. a counter that CODE increments (in a tool's post-hook, or in a code node), and
2. a deterministic transition on that counter, placed first in the node's list.

The model is never asked to count. Nothing in this file knows any brief: the
counter names, fields, values, limits and target nodes are all arguments.

Two ways to count:

    apply_tool_limit(...)   the counted event is a TOOL RESULT (a wrong answer, a failed try)
    add_counted_step(...)   the counted event is ENTERING A STEP (an offer, a question, a reminder)

Hooks read and write workflow variables through `vars`. If the platform ever runs
a post-hook without `vars`, the hook does nothing (it never raises), so check a
built agent once with a call that reaches the limit.
"""

HOOK_TEMPLATE = """try:
    _d = result.get('data') if isinstance(result, dict) else None
    if isinstance(_d, dict) and str(_d.get(%(field)r)).lower() == %(value)r:
        _n = int(float(vars.get(%(counter)r) or 0)) + 1
        vars[%(counter)r] = _n
        if _n >= %(limit)d and %(text)r:
            _d['what_to_do_next'] = %(text)r
except Exception:
    pass
"""

STEP_TEMPLATE = """try:
    _n = int(float(vars.get(%(counter)r) or 0)) + 1
except Exception:
    _n = 1
vars[%(counter)r] = _n
vars[%(state)r] = 'open' if _n <= %(limit)d else 'used_up'
"""


def _checked(code):
    # The platform may treat double braces inside code as a placeholder.
    if "{{" in code or "}}" in code:
        raise ValueError("generated code must not contain double braces")
    return code


def _positive(limit):
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a whole number of 1 or more, got %r" % (limit,))
    return limit


def counter_hook(counter, field, equals, limit, at_limit_text=""):
    """Post-hook code: add 1 to `counter` each time result.data[field] equals `equals`.

    When the count reaches `limit`, `at_limit_text` (if given) replaces the
    what_to_do_next line of the tool result.
    """
    return _checked(HOOK_TEMPLATE % {
        "counter": counter, "field": field, "value": str(equals).lower(),
        "limit": _positive(limit), "text": at_limit_text,
    })


def limit_transition(counter, limit, target, description=""):
    """Transition that fires as soon as `counter` has reached `limit`."""
    return {
        "target_node": target,
        "description": description or "%s reached its limit" % counter,
        "condition": {"logic": "or", "conditions": [
            {"variable": counter, "operator": "greater_than", "operand": _positive(limit) - 1},
        ]},
    }


def apply_tool_limit(workflow, node, tool, counter, field, equals, limit, target, at_limit_text="", description=""):
    """Limit how often a tool result may show `field == equals` in `node`; then go to `target`.

    Adds the counting hook to the tool and puts the transition first in the
    node's list, so it wins over every other route.
    """
    nodes = workflow["nodes"]
    if node not in nodes or target not in nodes:
        raise KeyError("unknown node: %s" % (node if node not in nodes else target))
    matches = [t for t in nodes[node].get("tools") or [] if t["function"]["name"] == tool]
    if not matches:
        raise KeyError("node %s has no tool %s" % (node, tool))
    for t in matches:
        t["post_process_code"] = (t.get("post_process_code") or "") + counter_hook(counter, field, equals, limit, at_limit_text)
    nodes[node].setdefault("transitions", []).insert(0, limit_transition(counter, limit, target, description))
    return workflow


def add_counted_step(workflow, name, counter, limit, within, over):
    """Add a code node that counts how often a step is entered.

    Route into `name` instead of into the step itself. It continues to `within`
    while the step is still allowed and to `over` once the limit is used up.
    It also sets `<counter>_state` to "open" or "used_up" for use in prompts.
    """
    nodes = workflow["nodes"]
    if name in nodes:
        raise KeyError("node %s already exists" % name)
    for target in (within, over):
        if target not in nodes:
            raise KeyError("unknown node: %s" % target)
    code = _checked(STEP_TEMPLATE % {"counter": counter, "state": counter + "_state", "limit": _positive(limit)})
    nodes[name] = {
        "name": name,
        "node_type": "code",
        "passthrough": True,
        "passthrough_default_transition": within,
        "on_enter": {"type": "silent"},
        "disable_inactivity_monitor": True,
        "pre_actions": [{"type": "function", "function": {
            "name": "code_exec",
            "handler": "__function__:code_exec",
            "parameters": {"type": "object", "properties": {
                "code": {"type": "string", "default": code},
                "timeout_ms": {"type": "number", "default": 10000},
            }},
        }}],
        "transitions": [
            limit_transition(counter, limit + 1, over, "%s is used up" % name),
            {"target_node": within, "description": "always", "condition": {"operator": "always"}},
        ],
    }
    return workflow
