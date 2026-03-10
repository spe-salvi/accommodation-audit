from typing import Any

def parse_int(value: Any, default=None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def parse_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)

def validate_expected_id(raw_value: Any, expected: int) -> int | None:
    parsed = parse_int(raw_value)
    return parsed if parsed == expected else None
    
def parse_quiz_id(data: dict, engine: str) -> int | None:
    if engine == "classic":
        return parse_int(data.get("quiz_id"))
    return parse_int(data.get("assignment_id"))

def parse_submission_id(data: dict, engine: str) -> int | None:
    if engine == "classic":
        return parse_int(data.get("submission_id"))
    return parse_int(data.get("id"))