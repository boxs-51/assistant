# Web search batch and source dates

## Capabilities

- `web.search_many` accepts 1–5 `{query, focus?}` items and searches them concurrently, bounded by `WebTool.max_concurrency` and one total timeout. Results retain input order and report per-query success or failure.
- `web.search` and `web.search_many` accept `freshness`: `day`, `week`, `month`, or `year`. This passes DuckDuckGo's date filter. Search results are candidates, not verified publication dates.
- `web.read` returns `published_date` only from publication metadata or Article JSON-LD. It returns `displayed_date` separately for explicitly marked page dates. Either field can be `null`; `provenance` records the source of each date.

## Example

```python
from tools.v1.web_tool import run

batch = run(
    "search_many",
    searches=[
        {"query": "PUBG Vietnam Series", "focus": "postponed"},
        {"query": "PUBG Asia Stars", "focus": "Himass TanVuu"},
    ],
    freshness="week",
    max_results=5,
    timeout=5,
)
page = run("scrape", url="https://pubgesports.com/vi/news/11157")
```

Read the source before reporting a publication date. A provider date filter may include stale or unrelated results, and a displayed page date may describe an update rather than original publication.

## Review fixes

- A batch now returns `ok=false` when all items fail. Counts and every item error are preserved in `error.details`; mixed batches remain `ok=true` with a warning and per-item failures in `data.results`.
- `search_many` preserves completed queries when the total timeout is reached. `read_many` now uses one total timeout, resolves its URL preflight concurrently, and preserves completed reads on timeout.
- Search results are reranked using query terms (including Vietnamese text without accent matching) and unrelated candidates are removed when relevant candidates exist. A retry is made for one transient provider/DNS failure while time remains.
- `freshness` without `verify_freshness` remains a fast provider-only hint. Set `verify_freshness=true` to fetch source pages and retain only results whose publication metadata is within the requested interval. This strict mode may return fewer or zero results and can use the full timeout.
- Content extraction removes common related/promotional blocks and repeated nearby summary cards. Site-specific noise may still require source review.

## Final verification after review fixes

- Cross-layer Web, server, agent projection, E2E, and client tests: `137 passed, 20 subtests passed` (two fixture deprecation warnings). On Windows, a non-elevated full run hit `WinError 5` in the pytest temporary directory; the same command completed with a writable elevated temporary directory.
- Live fast search benchmark: five concurrent queries with five results each, `0.839` seconds total.
- Live strict search for PVS Phase 2: four results with source publication dates in the requested week; one candidate without qualifying metadata was omitted.
- Live Znews read: publication date `2026-09-23`; the unrelated NVIDIA promotional note was absent from extracted content.

`verify_freshness=true` performs extra source requests, so the fast search timing does not apply to it. A page can omit publication metadata or publish incorrect metadata; strict mode only verifies the date declared by the fetched page. External provider outages and slow sites can still cause per-item failures or timeouts, which are now reported explicitly.

## Verification

- Live `web.search_many`: 3 concurrent queries, 5 results each, 1.11 seconds.
- Live benchmark through `search_many`: 5 concurrent queries, 5 results each, 0.924 seconds total.
- Live source date: Dân trí returned `published_date=2026-09-25` from JSON-LD; PUBG Esports returned `displayed_date=2026-09-24` from its visible modified-date label, with `published_date=null`.
- Targeted Web, server, agent projection, E2E, and client gate: 128 passed, 2 warnings, 20 subtests passed.
