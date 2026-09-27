"""One live PUBG Vietnam search-to-read benchmark using the repository Web tool."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from tools.v1.web_tool import run


QUERIES = [
    {"query": "PUBG Việt Nam tin nổi bật tháng 9 2026"},
    {"query": "PUBG esports Việt Nam 2026", "focus": "giải đấu đội tuyển"},
    {"query": "PUBG Vietnam September 2026 tournament"},
    {"query": "PUBG Mobile Việt Nam tháng 9 2026", "focus": "esports"},
    {"query": "PUBG đội tuyển Việt Nam 2026", "focus": "kết quả mới"},
]
OUTPUT = Path(__file__).with_name("BENCHMARK_PUBG_SEARCH_READ_2026-09-27.json")


def source_score(item: dict) -> int:
    url = item.get("url", "")
    host = urlparse(url).netloc.lower()
    text = f"{item.get('title', '')} {item.get('snippet', '')}".lower()
    score = 0
    if "pubg" in text or "pubg" in url.lower():
        score += 5
    if "việt nam" in text or "vietnam" in text or "việt" in text:
        score += 3
    if "2026" in text:
        score += 2
    if "pubgesports.com" in host or "pubgmobile.com" in host:
        score += 4
    if host.endswith((".vn", ".com.vn")):
        score += 2
    if any(blocked in host for blocked in ("youtube", "facebook", "tiktok", "reddit", "x.com")):
        score -= 20
    return score


def main() -> int:
    started = time.perf_counter()
    searched_at = datetime.now(timezone.utc).isoformat()
    search_start = time.perf_counter()
    search = run("search_many", searches=QUERIES, freshness="week", max_results=5, timeout=8)
    search_seconds = time.perf_counter() - search_start

    search_rows = (search.get("data") or {}).get("results", [])
    candidates: dict[str, dict] = {}
    for row in search_rows:
        for item in (row.get("data") or {}).get("results", []):
            url = item.get("url")
            if isinstance(url, str) and url.startswith(("https://", "http://")):
                candidates.setdefault(url, item)
    ranked = sorted(candidates.values(), key=source_score, reverse=True)
    urls = [item["url"] for item in ranked if source_score(item) > 0][:4]

    read = None
    read_seconds = 0.0
    if urls:
        read_start = time.perf_counter()
        read = run("scrape_many", urls=urls, timeout=12, max_chars=12000)
        read_seconds = time.perf_counter() - read_start

    elapsed = time.perf_counter() - started
    evidence = {
        "started_at_utc": searched_at,
        "tool": "tools.v1.web_tool.run",
        "queries": QUERIES,
        "freshness": "week",
        "search_seconds": round(search_seconds, 3),
        "read_seconds": round(read_seconds, 3),
        "total_seconds": round(elapsed, 3),
        "search": search,
        "selected_urls": urls,
        "read": read,
    }
    OUTPUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "artifact": str(OUTPUT),
        "search_seconds": evidence["search_seconds"],
        "read_seconds": evidence["read_seconds"],
        "total_seconds": evidence["total_seconds"],
        "search_queries_succeeded": (search.get("data") or {}).get("succeeded_count", 0),
        "search_results": sum((row.get("data") or {}).get("returned_count", 0) for row in search_rows),
        "selected_urls": urls,
        "read_succeeded": ((read or {}).get("data") or {}).get("succeeded_count", 0),
        "read_failed": ((read or {}).get("data") or {}).get("failed_count", 0),
        "search_error": search.get("error"),
        "read_error": (read or {}).get("error"),
    }, ensure_ascii=False, indent=2))
    search_ok = (
        search.get("ok")
        and (search.get("data") or {}).get("succeeded_count") == len(QUERIES)
    )
    read_ok = (
        read is not None
        and read.get("ok")
        and (read.get("data") or {}).get("succeeded_count") == len(urls)
    )
    return 0 if search_ok and read_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
