"""Tiny eval runner (placeholder).

Sends each question in dataset.jsonl to the running backend and checks
whether the answer contains the expected text.

Usage (with `docker compose up` running):
    python evals/run_evals.py

Next steps: check which tool was called, add LLM-as-judge scoring,
and log results to LangSmith.
"""

import json
import pathlib
import urllib.request

API_URL = "http://localhost:8000/chat"
DATASET = pathlib.Path(__file__).parent / "dataset.jsonl"


def ask(question: str, conversation_id: str) -> str:
    body = json.dumps({"message": question, "conversation_id": conversation_id}).encode()
    req = urllib.request.Request(API_URL, body, {"Content-Type": "application/json"})
    answer = ""
    with urllib.request.urlopen(req, timeout=120) as res:
        for line in res:
            line = line.decode().strip()
            if line.startswith("data: ") and line != "data: [DONE]":
                answer += json.loads(line[6:])["text"]
    return answer


def main() -> None:
    cases = [json.loads(line) for line in DATASET.read_text().splitlines() if line]
    passed = 0
    for case in cases:
        answer = ask(case["question"], f"eval-{case['id']}")
        ok = case["expected_answer_contains"] in answer
        passed += ok
        print(f"[{'PASS' if ok else 'FAIL'}] {case['id']}: {answer[:80]!r}")
    print(f"\n{passed}/{len(cases)} passed")


if __name__ == "__main__":
    main()
