"""Run the eval set against the running agent and score it.

For each question in dataset.jsonl, this script:
1. sends it to the backend (POST /chat), like the web page does,
2. collects the answer text AND the tool calls / tool results from the stream,
3. runs the checks in checks.py (right tool? right value? no invented numbers?).

Usage (with `docker compose up` and Ollama running):
    python evals/run_evals.py                 # all questions
    python evals/run_evals.py --only stats    # only ids that start with "stats"
    python evals/run_evals.py --verbose       # also print the full answers

Results are saved to evals/results/<time>.json (this folder is git-ignored),
so you can compare two runs, e.g. qwen3 vs. another model.

Note: questions about "today" or "this season" have no fixed answer. For those
we only check the tool choice and that the numbers are grounded.
"""

import argparse
import datetime as dt
import json
import pathlib
import time
import urllib.request

from checks import load_cases, score_case

HERE = pathlib.Path(__file__).parent
DATASET = HERE / "dataset.jsonl"
RESULTS = HERE / "results"


def ask(api_url: str, question: str, conversation_id: str) -> dict:
    """Send one question and read the whole SSE stream."""
    body = json.dumps({"message": question, "conversation_id": conversation_id}).encode()
    req = urllib.request.Request(f"{api_url}/chat", body, {"Content-Type": "application/json"})
    answer, tool_calls, tool_outputs = "", [], []
    with urllib.request.urlopen(req, timeout=300) as res:
        for line in res:
            line = line.decode().strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            event = json.loads(line[6:])
            if event.get("type") == "tool_call":
                tool_calls.append({"name": event["name"], "args": event.get("args", {})})
            elif event.get("type") == "tool_result":
                content = event.get("content", "")
                tool_outputs.append(content if isinstance(content, str) else json.dumps(content))
            elif "text" in event:
                answer += event["text"]
    return {"answer": answer, "tool_calls": tool_calls, "tool_outputs": tool_outputs}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--only", default="", help="only run cases whose id starts with this")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    cases = [c for c in load_cases(DATASET.read_text()) if c["id"].startswith(args.only)]
    run_id = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    results = []

    for case in cases:
        start = time.monotonic()
        try:
            got = ask(args.api_url, case["question"], f"eval-{run_id}-{case['id']}")
            result = score_case(case, got["answer"], got["tool_calls"], got["tool_outputs"])
        except Exception as e:  # keep going if one question crashes  # noqa: BLE001
            result = {"id": case["id"], "passed": False, "checks": {}, "answer": "",
                      "tool_calls": [], "problems": [f"request failed: {e}"]}
        result["seconds"] = round(time.monotonic() - start, 1)
        results.append(result)

        mark = "PASS" if result["passed"] else "FAIL"
        tools = ", ".join(c["name"] for c in result["tool_calls"]) or "no tool"
        print(f"[{mark}] {case['id']:<26} {result['seconds']:>5}s  tools: {tools}")
        for problem in result["problems"]:
            print(f"         - {problem}")
        if args.verbose:
            print(f"         answer: {result['answer'][:500]!r}")

    # Summary: overall, and per kind of check.
    passed = sum(r["passed"] for r in results)
    print(f"\n{passed}/{len(results)} cases passed")
    for check in ("tools", "contains", "grounded"):
        scored = [r for r in results if check in r["checks"]]
        ok = sum(r["checks"][check] for r in scored)
        print(f"  {check:<9} {ok}/{len(scored)}")

    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"{run_id}.json"
    out.write_text(json.dumps({"run_id": run_id, "results": results}, indent=2))
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
