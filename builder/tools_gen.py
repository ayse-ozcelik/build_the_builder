"""openapi.json -> Freya tool definitions.

Turns every operation in the bank's tool API into a Freya "api_call" tool,
in the same shape as the tools in reference_workflow.json. Nothing about the
bank is hardcoded here: tool names, fields, types, enums and descriptions all
come from the OpenAPI file, so a brief with different fields still works.

Usage:
    python builder/tools_gen.py tools/openapi.json            # print to screen
    python builder/tools_gen.py tools/openapi.json -o out.json

From code:
    tools = generate_tools(load_spec("tools/openapi.json"))
    tools["create_promise"]   # -> Freya tool definition (a dict)
"""
import argparse
import copy
import json
import sys

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
API_CALL_HANDLER = "__function__:api_call"
TOOL_CONFIG = {"timeoutMs": 12000, "maxRetries": 0}

# Variables the platform fills in by itself for every call (see test_variables in
# the reference workflow). The model must not be asked for them, so they are kept
# out of "required" and get no description, exactly as in the reference.
CONTEXT_VARS = ("customer_id",)

# JSON-schema details Freya's format has no slot for; we add them to the description
# so the phone agent still sees them.
HINT_KEYS = ("format", "minimum", "maximum", "minLength", "maxLength", "pattern")
# Standard JSON-schema formats the phone agent understands better when spelled out.
FORMAT_TEXT = {"date": "YYYY-MM-DD"}


def load_spec(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _resolve(schema, spec, depth=0):
    """Follow "$ref" pointers and merge "allOf" parts into one plain schema."""
    if not isinstance(schema, dict):
        return {}
    if depth > 20:
        raise ValueError("schema references are nested too deep (circular $ref?)")
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            raise ValueError("only local $ref is supported, got: " + ref)
        node = spec
        for part in ref[2:].split("/"):
            node = node[part.replace("~1", "/").replace("~0", "~")]
        return _resolve(node, spec, depth + 1)
    if "allOf" in schema:
        merged = {k: v for k, v in schema.items() if k != "allOf"}
        properties = dict(merged.get("properties", {}))
        required = list(merged.get("required", []))
        for part in schema["allOf"]:
            part = _resolve(part, spec, depth + 1)
            properties.update(part.get("properties", {}))
            required += [r for r in part.get("required", []) if r not in required]
            for key, value in part.items():
                if key not in ("properties", "required"):
                    merged.setdefault(key, value)
        merged["properties"] = properties
        merged["required"] = required
        return merged
    return schema


def _field(name, schema, spec, context_vars, warnings, tool_name):
    """One body field of the API -> one Freya body property."""
    schema = _resolve(schema, spec)
    field_type = schema.get("type", "string")
    if isinstance(field_type, list):  # OpenAPI 3.1 style: ["string", "null"]
        field_type = next((t for t in field_type if t != "null"), "string")

    if name in context_vars:
        return {"type": field_type, "default": "{{%s}}" % name}
    if "default" in schema and field_type not in ("object", "array"):
        # the API file fixes the value: it is sent as written and the phone agent never sees the field
        return {"type": field_type, "default": schema["default"]}

    out = {"type": field_type}
    if "enum" in schema:
        out["enum"] = [v for v in schema["enum"] if v is not None]

    if field_type in ("object", "array"):
        # The reference workflow only has flat fields, so this shape is a guess.
        for key in ("properties", "items", "required"):
            if key in schema:
                out[key] = copy.deepcopy(schema[key])
        warnings.append("%s.%s is a nested %s: check it by hand in Freya" % (tool_name, name, field_type))
    else:
        out["default"] = "{{%s}}" % name

    description = (schema.get("description") or "").strip()
    hints = ["%s: %s" % (key, schema[key]) for key in HINT_KEYS if key in schema]
    if schema.get("format") in FORMAT_TEXT:
        hints[0] = FORMAT_TEXT[schema["format"]]
    if hints:
        description = (description + " (" + ", ".join(hints) + ")").strip()
    if description:
        out["description"] = description
    return out


def _body_schema(operation, spec):
    content = _resolve(operation.get("requestBody", {}), spec).get("content", {})
    media = content.get("application/json") or next(iter(content.values()), {})
    return _resolve(media.get("schema", {}), spec)


def generate_tools(spec, base_url=None, context_vars=CONTEXT_VARS, warnings=None):
    """Return {tool_name: Freya tool definition} for every operation in the spec."""
    if warnings is None:
        warnings = []
    if base_url is None:
        servers = spec.get("servers") or []
        if not servers or not servers[0].get("url"):
            raise ValueError("the OpenAPI file has no servers[0].url; pass base_url")
        base_url = servers[0]["url"]
    base_url = base_url.rstrip("/")

    tools = {}
    for path, path_item in spec.get("paths", {}).items():
        for method in HTTP_METHODS:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            name = operation.get("operationId") or path.strip("/").split("/")[-1]
            if operation.get("parameters") or path_item.get("parameters"):
                warnings.append("%s has URL/query parameters: they are not converted" % name)

            body = _body_schema(operation, spec)
            leaves, nesting = _leaves(body, spec, (), warnings, name)
            properties = {
                field: _field(field, field_schema, spec, context_vars, warnings, name)
                for field, field_schema, _ in leaves
            }
            required = [field for field, _, needed in leaves if needed and field not in context_vars]

            if name in tools:
                warnings.append("tool name %s appears twice: the later one wins" % name)
            tools[name] = {
                "type": "function",
                "function": {
                    "name": name,
                    "handler": API_CALL_HANDLER,
                    "description": (operation.get("description") or operation.get("summary") or "").strip(),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "default": base_url + path},
                            "method": {"type": "string", "default": method.upper()},
                            "headers": {
                                "type": "object",
                                "properties": {
                                    "Content-Type": {"type": "string", "default": "application/json"}
                                },
                            },
                            "body": {"type": "object", "required": required, "properties": properties},
                        },
                    },
                },
                "config": dict(TOOL_CONFIG),
            }
            if nesting:
                tools[name]["pre_process_code"] = NEST_HOOK % {"nesting": [[f, list(p)] for f, p in nesting]}
    return tools


# The platform takes body fields only as flat leaves. When the API wants them inside objects (an envelope such
# as "data"), the fields are declared flat and this hook puts each one back where the API expects it.
NEST_HOOK = """try:
    _body = config.get('body')
    if isinstance(_body, dict):
        _nested = dict()
        _moved = []
        for _field, _parents in %(nesting)r:
            _moved.append(_field)
            if _field in _body:
                _at = _nested
                for _parent in _parents:
                    _at = _at.setdefault(_parent, dict())
                _at[_field] = _body[_field]
        for _field in _body:
            if _field not in _moved and _field not in _nested:
                _nested[_field] = _body[_field]
        config['body'] = _nested
except Exception:
    pass
"""


def _leaves(schema, spec, parents, warnings, tool_name, depth=0):
    """The fields of a request body as flat leaves: ([(name, schema, required)], [(name, parents)] for nested ones)."""
    leaves, nesting = [], []
    needed = schema.get("required", [])
    for field, field_schema in (schema.get("properties") or {}).items():
        resolved = _resolve(field_schema, spec)
        if resolved.get("type") == "object" and resolved.get("properties") and depth < 4:
            below, below_nesting = _leaves(resolved, spec, parents + (field,), warnings, tool_name, depth + 1)
            leaves += [(n, s, r and field in needed) for n, s, r in below]
            nesting += below_nesting
            continue
        if any(field == n for n, _, _ in leaves):
            warnings.append("%s: field name %s appears twice in the request body: the later one wins" % (tool_name, field))
            leaves = [leaf for leaf in leaves if leaf[0] != field]
            nesting = [n for n in nesting if n[0] != field]
        leaves.append((field, field_schema, field in needed))
        if parents:
            nesting.append((field, parents))
    return leaves, nesting


def main(argv=None):
    parser = argparse.ArgumentParser(description="Convert an OpenAPI tool spec into Freya tool definitions.")
    parser.add_argument("openapi_path")
    parser.add_argument("-o", "--out", help="write the result to this file instead of the screen")
    parser.add_argument("--base-url", help="override the server URL from the OpenAPI file")
    args = parser.parse_args(argv)

    warnings = []
    tools = generate_tools(load_spec(args.openapi_path), base_url=args.base_url, warnings=warnings)
    for warning in warnings:
        print("WARNING: " + warning, file=sys.stderr)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(tools, f, indent=2, ensure_ascii=False)
        print("wrote %d tools to %s" % (len(tools), args.out), file=sys.stderr)
    else:
        print(json.dumps(tools, indent=2))


if __name__ == "__main__":
    main()
