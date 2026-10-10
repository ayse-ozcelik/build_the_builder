"""Brief files + tool API file -> a structured rule list (spec JSON).

    python builder/extract.py <brief.md> [<more briefs>...] --tools <openapi.json> -o spec.json

A model reads the brief files and writes the spec. Code then checks the spec
against the tool API file, so a limit that names a tool or a field that does
not exist stops the build instead of being skipped silently.

Nothing about any one brief is written here: the prompt describes the SHAPE of
the answer only. Needs a Claude login (or ANTHROPIC_API_KEY) for the model call.
"""
import argparse
import asyncio
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MODEL = os.environ.get("BUILDER_MODEL", "claude-opus-5-5")
HTTP_METHODS = ("get", "post", "put", "patch", "delete")
COUNTED_EVENTS = ("tool_result", "agent_action", "caller_action")

PROMPT = """You are preparing the rule list for a phone agent. Read every one of these files completely:

BRIEF FILES (in this order; when two disagree, the later file or the later-dated document wins):
{briefs}

TOOL API FILE:
{tools}

Then answer with ONE JSON object and nothing else (no prose, no code fence). Take every value from the
files. Do not add a rule the files do not state. Quote the source for each item.

{{
  "summary": "one sentence: what the call is for",
  "call_variables": [{{"name": "", "meaning": ""}}],
  "stages": [{{"id": "", "what_happens": "", "may_start_only_after": "", "tools": [""]}}],
  "not_before": [{{"forbidden": "what must not be said or done", "until": "the event that lifts it", "applies_to": "who", "source": ""}}],
  "fixed_sentences": [{{"when": "", "text": "the exact words", "source": ""}}],
  "calculations": [{{"name": "", "rule": "", "inputs": [""], "source": ""}}],
  "limits": [{{
    "id": "short_snake_case_name",
    "what_is_counted": "",
    "n": 1,
    "n_depends_on": "empty, or the condition that changes n, with each value of n",
    "per": "call, or the thing it is counted per",
    "counted_event": "tool_result | agent_action | caller_action",
    "tool": "tool name when counted_event is tool_result, else empty",
    "field": "field of that tool's response that shows the event, else empty",
    "value": "value of that field that counts, else empty",
    "after_limit": "what happens once the limit is reached",
    "source": "rule number or quote"
  }}],
  "endings": [{{"when": "", "tool": "", "fields": {{"field": "value"}}, "say_before": "", "source": ""}}],
  "tool_failures": [{{"tool": "", "rule": "", "source": ""}}],
  "replaced_rules": [{{"old": "", "new": "", "source": ""}}],
  "not_stated": ["things a builder would need that the files leave open"]
}}

About "limits": list EVERY rule that allows something only a certain number of times: "at most N", "once",
"once more", "twice", "after the Nth", "N tries", "N attempts", "ask again once". n is the number of times
the counted thing may happen. Use counted_event tool_result only when a field of a tool's response in the
tool API file shows the event; then tool, field and value must be exactly as written in that file."""


def response_fields(spec):
    """{tool_name: set of field names in its 200 response} from an OpenAPI spec."""
    out = {}
    for path, item in spec.get("paths", {}).items():
        for method in HTTP_METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            name = op.get("operationId") or path.strip("/").split("/")[-1]
            fields = set()
            for response in (op.get("responses") or {}).values():
                for media in (response.get("content") or {}).values():
                    fields |= set((media.get("schema") or {}).get("properties") or {})
            out[name] = fields
    return out


def request_enums(spec):
    """{tool_name: {field: allowed values}} from the request bodies of an OpenAPI spec."""
    out = {}
    for path, item in spec.get("paths", {}).items():
        for method in HTTP_METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            name = op.get("operationId") or path.strip("/").split("/")[-1]
            body = {}
            for media in ((op.get("requestBody") or {}).get("content") or {}).values():
                body = (media.get("schema") or {}).get("properties") or {}
            out[name] = {field: schema["enum"] for field, schema in body.items() if "enum" in schema}
    return out


def parse_json(text):
    """The model's answer -> dict. Accepts a code fence or text around the object."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("the model's answer contains no JSON object")
    return json.loads(text[start:end + 1])


def validate(spec, tool_api):
    """Return a list of problems. An empty list means the spec fits the tool API file."""
    problems = []
    responses, enums = response_fields(tool_api), request_enums(tool_api)
    for key in ("stages", "limits", "endings"):
        if not isinstance(spec.get(key), list) or not spec[key]:
            problems.append("'%s' is missing or empty" % key)
    seen = set()
    for limit in spec.get("limits") or []:
        name = limit.get("id") or "?"
        if name in seen:
            problems.append("limit %s appears twice" % name)
        seen.add(name)
        n = limit.get("n")
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            problems.append("limit %s: n must be a whole number of 1 or more, got %r" % (name, n))
        if not limit.get("source"):
            problems.append("limit %s has no source" % name)
        event = limit.get("counted_event")
        if event not in COUNTED_EVENTS:
            problems.append("limit %s: counted_event %r is not one of %s" % (name, event, ", ".join(COUNTED_EVENTS)))
        if event == "tool_result":
            tool, field = limit.get("tool"), limit.get("field")
            if tool not in responses:
                problems.append("limit %s: tool %r is not in the tool API file" % (name, tool))
            elif field not in responses[tool]:
                problems.append("limit %s: %s has no response field %r" % (name, tool, field))
    for stage in spec.get("stages") or []:
        for tool in stage.get("tools") or []:
            if tool not in responses:
                problems.append("stage %s: tool %r is not in the tool API file" % (stage.get("id"), tool))
    for ending in spec.get("endings") or []:
        tool = ending.get("tool")
        if tool not in responses:
            problems.append("ending %r: tool %r is not in the tool API file" % (ending.get("when"), tool))
            continue
        for field, value in (ending.get("fields") or {}).items():
            allowed = enums[tool].get(field)
            if allowed is not None and value not in allowed:
                problems.append("ending %r: %s.%s = %r is not an allowed value" % (ending.get("when"), tool, field, value))
    return problems


async def _ask(prompt, model, on_message=None, cwd=None):
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
    # Inside a build workspace the model reads only that folder (its settings file says so).
    options = ClaudeAgentOptions(cwd=cwd or ROOT, model=model, allowed_tools=["Read"], permission_mode="dontAsk",
                                 disallowed_tools=["Bash", "PowerShell", "WebFetch", "WebSearch", "Agent", "Task"],
                                 setting_sources=["project"] if cwd else ["project", "local"], max_turns=20)
    answer = ""
    async for message in query(prompt=prompt, options=options):
        if on_message:
            on_message(message)
        if isinstance(message, ResultMessage):
            answer = message.result or ""
    return answer


def extract(brief_paths, tool_api_path, model=DEFAULT_MODEL, trace=None, on_message=None, cwd=None):
    """Run the model, check its answer, return the spec. Raises ValueError with the problems found.

    With `cwd` (a build workspace) the paths are relative to it and the model works inside it.
    """
    shown = (lambda p: p.replace("\\", "/")) if cwd else os.path.abspath
    with open(os.path.join(cwd, tool_api_path) if cwd else tool_api_path, encoding="utf-8") as f:
        tool_api = json.load(f)
    prompt = PROMPT.format(briefs="\n".join("- " + shown(p) for p in brief_paths), tools="- " + shown(tool_api_path))
    if trace:
        trace.model_call(model, "read the brief files and write the structured rule list (spec)")
    spec = parse_json(asyncio.run(_ask(prompt, model, on_message, cwd)))
    problems = validate(spec, tool_api)
    if problems:
        raise ValueError("the spec does not fit the tool API file:\n- " + "\n- ".join(problems))
    return spec


def main(argv=None):
    parser = argparse.ArgumentParser(description="Extract a structured rule list from brief files.")
    parser.add_argument("briefs", nargs="+")
    parser.add_argument("--tools", required=True, help="the tool API file (openapi.json)")
    parser.add_argument("-o", "--out", required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args(argv)
    spec = extract(args.briefs, args.tools, args.model)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=1, ensure_ascii=False)
    print("wrote %s: %d stages, %d limits, %d endings" % (
        args.out, len(spec["stages"]), len(spec["limits"]), len(spec["endings"])), file=sys.stderr)


if __name__ == "__main__":
    main()
