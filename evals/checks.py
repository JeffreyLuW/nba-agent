"""The checks used by run_evals.py. Pure functions, so they are easy to test.

Concept: **evals** are tests for an AI system. A normal test says "this
function returns 4". An LLM's answer changes every time, so instead we check
*properties* of the answer:

1. tools:    did the agent call the right tool (with the right arguments)?
2. contains: does the answer include the known correct value (e.g. "30.1")?
3. grounded: is every number in the answer found in the tool results?
             If not, the LLM probably invented ("hallucinated") it.
"""

import itertools
import json
import re

# ------------------------------------------------------------------ numbers

# Things that look like numbers but are not stats: seasons, dates, times.
_NOT_STATS = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b"  # 2026-11-24
    r"|\b\d{4}\s*[-/]\s*\d{2,4}\b"  # 2015-16, 2015/2016
    r"|\b\d{1,2}:\d{2}\b"  # 7:30 (tip-off time, game clock)
    r"|\b(?:Q|OT)\d\b",  # Q3, OT2
    re.IGNORECASE,
)
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?|(?<![\w\d])\.\d+")


def numbers_in(text: str) -> list[str]:
    """All stat-like numbers in a text, as strings ("30.1", ".454", "73")."""
    text = _NOT_STATS.sub(" ", text)
    return [m.group() for m in _NUMBER.finditer(text.replace(",", ""))]


def _is_year(value: float, raw: str) -> bool:
    return "." not in raw and 1946 <= value <= 2100


def _tolerance(raw: str) -> float:
    """How far a number may be from the source and still count as "rounded".

    "30.1" may come from 30.06 (half of 0.1). "30" may come from 30.4.
    """
    decimals = len(raw.split(".")[1]) if "." in raw else 0
    return 0.5 * 10**-decimals + 1e-9


def ungrounded_numbers(answer: str, tool_outputs: list[str], question: str = "") -> list[str]:
    """Numbers in the answer that appear nowhere in the tool outputs (or question).

    A number counts as grounded if it matches a tool number:
    - after rounding (30.06 -> "30.1"),
    - as a percentage (0.454 -> "45.4"),
    - or as the difference of two tool numbers (30.1 - 25.3 -> "4.8").
    Small whole numbers (0-9) and years are skipped: too common to judge.
    """
    known = set()
    for text in [*tool_outputs, question]:
        known.update(float(n) for n in numbers_in(text))
    known_list = sorted(known)
    question_numbers = set(numbers_in(question))

    def grounded(raw: str) -> bool:
        value, tol = float(raw), _tolerance(raw)
        if any(abs(value - k) <= tol or abs(value - k * 100) <= tol for k in known_list):
            return True
        if len(known_list) <= 300:  # differences, e.g. "Curry scored 4.8 more"
            for a, b in itertools.combinations(known_list, 2):
                if abs(value - abs(a - b)) <= tol or abs(value - abs(a - b) * 100) <= tol:
                    return True
        return False

    bad = []
    for raw in numbers_in(answer):
        value = float(raw)
        if raw in question_numbers or _is_year(value, raw):
            continue
        if "." not in raw and value < 10:
            continue
        if not grounded(raw):
            bad.append(raw)
    return bad


# -------------------------------------------------------------------- tools


def _arg_matches(expected, actual) -> bool:
    """Loose match: "2015-16" == "2015-16", "curry" in "Stephen Curry", True == "true"."""
    return str(expected).lower() in str(actual).lower()


def check_tools(case: dict, tool_calls: list[dict]) -> list[str]:
    """Problems with the tools the agent called. Empty list = good.

    expected_tools: every entry must be called. "a|b" means a OR b is fine.
                    An empty list means: no tool should be called.
    expected_args:  {"tool_name": {"arg": "value"}}. One call of that tool
                    must have matching arguments.
    """
    called = [c["name"] for c in tool_calls]
    expected = case.get("expected_tools", [])
    problems = []
    if not expected and called:
        problems.append(f"expected no tool, but called {called}")
    for option in expected:
        names = option.split("|")
        if not any(n in called for n in names):
            problems.append(f"did not call {' or '.join(names)} (called {called or 'nothing'})")

    for tool, args in case.get("expected_args", {}).items():
        calls = [c for c in tool_calls if c["name"] == tool]
        if not calls:
            continue  # already reported above if it was required
        if not any(all(_arg_matches(v, c["args"].get(k)) for k, v in args.items()) for c in calls):
            got = [c["args"] for c in calls]
            problems.append(f"{tool} args should include {args}, got {got}")
    return problems


def check_contains(case: dict, answer: str) -> list[str]:
    """answer_contains: all must appear. answer_contains_any: at least one."""
    low = answer.lower()
    problems = [f"missing {s!r}" for s in case.get("answer_contains", []) if s.lower() not in low]
    any_of = case.get("answer_contains_any", [])
    if any_of and not any(s.lower() in low for s in any_of):
        problems.append(f"should contain one of {any_of}")
    return problems


def clean_answer(answer: str) -> str:
    """Remove a reasoning model's hidden thinking, e.g. qwen3's <think>...</think>."""
    return re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL).strip()


def score_case(case: dict, answer: str, tool_calls: list[dict], tool_outputs: list[str]) -> dict:
    """Run all checks for one eval case."""
    answer = clean_answer(answer)
    checks = {
        "tools": check_tools(case, tool_calls),
        "contains": check_contains(case, answer),
        "grounded": [],
    }
    if case.get("check_grounded", True):
        bad = ungrounded_numbers(answer, tool_outputs, case["question"])
        if bad:
            checks["grounded"] = [f"numbers not in any tool result: {bad}"]
    return {
        "id": case["id"],
        "passed": not any(checks.values()),
        "checks": {name: not problems for name, problems in checks.items()},
        "problems": [p for problems in checks.values() for p in problems],
        "answer": answer,
        "tool_calls": tool_calls,
    }


def load_cases(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]
