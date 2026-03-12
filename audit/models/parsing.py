from typing import Any, Literal

QuizEngine = Literal["classic", "new"]

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
    return parse_int(data.get("id"))


def parse_submission_id(data: dict, engine: str) -> int | None:
    if engine == "classic":
        return parse_int(data.get("submission_id"))
    return parse_int(data.get("id"))


def validate_engine_value(engine: str) -> QuizEngine:
    if engine not in ("classic", "new"):
        raise ValueError(f"Invalid engine: {engine!r}. Expected 'classic' or 'new'.")
    return engine


def validate_payload_for_engine(data: dict, engine: QuizEngine) -> None:
    """
    Validate that the payload contains the minimum required shape
    for the declared engine. Raise ValueError on mismatch.
    """
    if engine == "classic":
        if parse_int(data.get("quiz_id")) is None:
            raise ValueError("Classic submission payload missing 'quiz_id'.")
        return

    if engine == "new":
        if parse_int(data.get("assignment_id")) is None:
            raise ValueError("New quiz submission payload missing 'assignment_id'.")
        return

    raise ValueError(f"Unsupported engine: {engine!r}")


def parse_quiz_id(data: dict, engine: QuizEngine) -> int | None:
    if engine == "classic":
        return parse_int(data.get("quiz_id"))
    return parse_int(data.get("assignment_id"))


def parse_submission_id(data: dict, engine: QuizEngine) -> int | None:
    if engine == "classic":
        return parse_int(data.get("id"))
    return parse_int(data.get("id"))


def validate_expected_id(actual: int | None, expected: int) -> int | None:
    if actual is None:
        return None
    return actual if actual == expected else None