"""Live benchmark for the concurrent web.search_many capability.

Run from the repository root with:
    venv/Scripts/python.exe -m tools.v1.live.benchmark_web_search
"""

from __future__ import annotations

import json
import time

from tools.v1.web_tool import run


CASES = (
    ("Python asyncio documentation", None),
    ("OpenAI API documentation", "responses"),
    ("NASA Artemis mission", "launch schedule"),
    ("Vietnam weather forecast", "Hanoi"),
    ("PostgreSQL documentation", "indexes"),
)
TARGET_SECONDS = 2.0


def main() -> int:
    batch_started = time.perf_counter()
    batch = run(
        "search_many",
        searches=[
            {"query": query, **({"focus": focus} if focus else {})}
            for query, focus in CASES
        ],
        max_results=5,
        timeout=5.0,
    )
    batch_seconds = time.perf_counter() - batch_started
    results = (batch.get("data") or {}).get("results", [])
    samples = []
    for (query, focus), result in zip(CASES, results):
        data = result.get("data") or {}
        count = data.get("returned_count", 0)
        samples.append({
            "query": query,
            "focus": focus,
            "ok": result["ok"],
            "count": count,
            "error_code": (result.get("error") or {}).get("code"),
            "results": data.get("results", []),
        })

    for sample in samples:
        print(json.dumps(sample), flush=True)

    summary = {
        "samples": len(samples),
        "passed": sum(sample["ok"] and sample["count"] == 5 for sample in samples),
        "batch_seconds": round(batch_seconds, 3),
        "batch_under_target": batch_seconds < TARGET_SECONDS,
        "target_seconds": TARGET_SECONDS,
        "batch_error_code": (batch.get("error") or {}).get("code"),
    }
    print(json.dumps({"summary": summary}))
    return 0 if batch["ok"] and summary["passed"] == len(CASES) and summary["batch_under_target"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
