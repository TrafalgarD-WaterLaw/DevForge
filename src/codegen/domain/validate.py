"""Lightweight JSON-schema validation for agent structured output.

``json_mode`` guarantees the LLM returns *valid JSON*, not *schema-conformant*
JSON.  These helpers check the essentials (required keys + top-level types)
so a malformed output surfaces as a retry instead of silently degrading
downstream.
"""

import logging

_log = logging.getLogger(__name__)
_TYPE_CHECKERS = {
    "string": lambda v: isinstance(v, str),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and (not isinstance(v, bool)),
    "number": lambda v: isinstance(v, (int, float)) and (not isinstance(v, bool)),
    "null": lambda v: v is None,
}

def validate_nested(instance_data: dict, schema: dict) -> list[str]:
    """Validate array-of-object properties against their item schemas.

    Walks every property whose ``items`` declares a ``required`` list (e.g.
    reviewer ``issues`` items, design ``modules[].exports`` items) and checks
    each array element for the required keys + correct types.  Returns a list
    of violation strings (``[]`` = valid).
    """
    errors: list[str] = []
    props = schema.get("properties", {})
    for key, prop in props.items():
        if not isinstance(prop, dict) or key not in instance_data:
            continue
        items = prop.get("items", {})
        required = items.get("required") if isinstance(items, dict) else None
        if not isinstance(required, list) or not required:
            continue
        value = instance_data[key]
        if not isinstance(value, list):
            continue
        item_props = items.get("properties", {})
        for idx, item in enumerate(value):
            if not isinstance(item, dict):
                errors.append(f"{key}[{idx}] is {type(item).__name__}, expected object")
                continue
            for rk in required:
                if rk not in item:
                    errors.append(f"{key}[{idx}] missing required key '{rk}'")
                    continue
                rp = item_props.get(rk, {})
                type_spec = rp.get("type")
                if type_spec and (not _type_ok(item[rk], type_spec)):
                    errors.append(
                        f"{key}[{idx}] key '{rk}' is {type(item[rk]).__name__}, expected {type_spec}"
                    )
    return errors

def _type_ok(value, type_spec) -> bool:
    """Check *value* against a schema ``type`` entry (may be a list)."""
    if isinstance(type_spec, list):
        return any((_type_ok(value, t) for t in type_spec))
    checker = _TYPE_CHECKERS.get(type_spec)
    return checker(value) if checker else True

def validate_output(instance_data: dict, schema: dict) -> list[str]:
    """Return a list of schema violations (``[]`` = valid).

    Checks ``required`` keys exist with correct top-level types.
    Optional keys are ignored.  Unknown keys are ignored.
    """
    if not isinstance(instance_data, dict):
        return [f"output is {type(instance_data).__name__}, expected object"]
    errors: list[str] = []
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in instance_data:
            errors.append(f"missing required key '{key}'")
            continue
        prop = props.get(key, {})
        type_spec = prop.get("type")
        if type_spec and (not _type_ok(instance_data[key], type_spec)):
            errors.append(
                f"key '{key}' is {type(instance_data[key]).__name__}, expected {type_spec}"
            )
    errors.extend(validate_nested(instance_data, schema))
    return errors

def validated_react(
    agent, prompt: str, schema: dict, *, json_mode: bool = True, retries: int = 1
) -> dict:
    """Call ``agent.react(prompt)``, validate against *schema*.

    On violation, retry up to *retries* times with the error list fed back.
    Returns the last output either way — callers decide how to handle
    a final invalid result.
    """
    instance_data = agent.react(prompt, json_mode=json_mode)
    for attempt in range(retries + 1):
        errors = validate_output(instance_data, schema)
        if not errors:
            return instance_data
        if attempt < retries:
            from core.config import load_sys_message
            msg = load_sys_message("validate_retry_again",
                                   errors="; ".join(errors))
            print(f"  [{agent.name}] schema invalid ({errors}) — retrying", flush=True)
            instance_data = agent.react(msg, json_mode=json_mode)
        else:
            print(f"  [{agent.name}] FINAL output still invalid: {errors}", flush=True)
            _log.warning("[%s] output failed schema: %s", agent.name, errors)
    return instance_data
