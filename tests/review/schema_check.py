"""jsonschema 의존성 없이 draft-07 스키마의 핵심(type, required, additionalProperties, enum, items, $ref, anyOf)만 검사한다."""

from __future__ import annotations

TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "boolean": bool,
    "null": type(None),
    "number": (int, float),
}


def _resolve(schema: dict, root: dict) -> dict:
    ref = schema.get("$ref")
    if not ref:
        return schema
    node = root
    for part in ref.lstrip("#/").split("/"):
        node = node[part]
    return node


def _type_ok(value: object, t: str) -> bool:
    py = TYPES[t]
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, py)


def validate(value: object, schema: dict, root: dict | None = None, path: str = "$") -> list[str]:
    """value가 schema를 만족하는지 검사해 오류 메시지 목록을 돌려준다. 비어 있으면 통과."""
    root = root or schema
    schema = _resolve(schema, root)
    errors: list[str] = []
    if "anyOf" in schema:
        if not any(not validate(value, s, root, path) for s in schema["anyOf"]):
            errors.append(f"{path}: anyOf 불일치")
        return errors
    t = schema.get("type")
    if t:
        types = t if isinstance(t, list) else [t]
        if not any(_type_ok(value, x) for x in types):
            errors.append(f"{path}: type {types} 가 아님 ({type(value).__name__})")
            return errors
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: enum 밖의 값 {value!r}")
    if isinstance(value, str):
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: maxLength 초과")
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: minLength 미달")
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                errors.append(f"{path}: 필수 키 {k} 누락")
        props = schema.get("properties", {})
        addl = schema.get("additionalProperties", True)
        for k, v in value.items():
            if k in props:
                errors.extend(validate(v, props[k], root, f"{path}.{k}"))
            elif isinstance(addl, dict):
                errors.extend(validate(v, addl, root, f"{path}.{k}"))
            elif addl is False:
                errors.append(f"{path}: 허용되지 않는 키 {k}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: minItems 미달")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: maxItems 초과")
        if "items" in schema:
            for i, v in enumerate(value):
                errors.extend(validate(v, schema["items"], root, f"{path}[{i}]"))
    return errors
