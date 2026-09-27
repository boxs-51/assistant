# Live PUBG Vietnam search and read benchmark

## Method

- Entrypoint: `E:\assistant\venv\Scripts\python.exe -m tools.v1.live.benchmark_pubg_search_read_20260927`
- Network/search/read implementation: `tools.v1.web_tool.run` only; `search_many` then `scrape_many` in one process.
- Five PUBG Vietnam queries, DuckDuckGo `freshness=week`, five results per query, and four selected source pages.
- Timings use Python `time.perf_counter`; the read time includes URL preflight, fetch, extraction, and the timed-out page.

## Result

| Stage | Time | Outcome |
| --- | ---: | --- |
| Search | 0.948 s | 5/5 queries succeeded; 25 results total |
| Read | 12.271 s | 3/4 pages succeeded; one `WEB_TIMEOUT` |
| Full process | 13.220 s | Partial success |

The search stage met the 2-second target in this run. The complete search-and-read process did not. The initial sandboxed attempt took 2.257 s and returned `WEB_SEARCH_PROVIDER_FAILED` / `ConnectionError` for all five queries; it could not read any page. The recorded successful-network run used the same repository script with network access.

## Content verified from successful reads

- Znews, published 2026-09-23: the PVS schedule had disappeared from the PUBG Esports homepage; at publication time KRAFTON had not given a formal reason for the change.
- Thanh Niên, published 2026-09-23: TanVuu removed PUBG on a livestream after the PUBG Asia Stars 2026 dispute; the article also describes the sanctions and community reaction.
- TechZ, published 2026-09-25: PUBG Esports announced on 2026-09-24 that the remaining PVS 2026 Phase 2 matches were postponed, with no new schedule or cause announced. Its article cautions against assuming a direct causal link to the Himass and TanVuu sanctions.
- Lag.vn was selected as the fourth source but returned `WEB_TIMEOUT`; its content was not used in the summary.

Full raw search and read responses, including URLs, content, date provenance, and errors: `BENCHMARK_PUBG_SEARCH_READ_2026-09-27.json`.
