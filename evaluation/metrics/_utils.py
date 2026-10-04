from __future__ import annotations
import json, math, re

VERDICT_SCORES = {"yes": 1.0, "partial": 0.5, "no": 0.0}


def parse_json_array(text: str) -> list:
    """Tolerant JSON-array parser (handles ```json fences / chatter around the array)."""
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start:end + 1])
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def first_word(text: str) -> str:
    m = re.search(r"[a-zA-Z]+", text)
    return m.group(0).lower() if m else ""


def verdict_score(text: str) -> float:
    return VERDICT_SCORES.get(first_word(text), 0.0)


def is_yes(text: str) -> bool:
    return first_word(text) == "yes"


def cosine(a: list[float], b: list[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))
