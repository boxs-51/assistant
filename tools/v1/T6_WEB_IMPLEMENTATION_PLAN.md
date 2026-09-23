# T6 Web Tool — Exact T5→T6 Boundary Audit & T6-A→T6-H Implementation Plan

Repository: `boxs-51/assistant`  
Branch: `tools-v1-contract-freeze`  
Audit baseline HEAD: `622a2a125d59c30e4138bea2ccd6d30d82c0e1a4`  
T5 status: COMPLETE + FROZEN  
Scope: `tools/v1/web_tool/**` + `tools/v1/test/test_web_tool.py` only  
Mode: **AUDIT + IMPLEMENTATION PLAN ONLY — NO T6 PRODUCTION CODE IMPLEMENTED**

---

# 1. T5 → T6 boundary decision

T5 is closed.

T6 owns:

```text
tools/v1/web_tool/__init__.py
tools/v1/web_tool/config.py
tools/v1/web_tool/core.py
tools/v1/web_tool/scraper.py
tools/v1/web_tool/searcher.py
tools/v1/web_tool/proxy.py
tools/v1/web_tool/extractors.py
tools/v1/web_tool/stealth.py
tools/v1/web_tool/utils.py
tools/v1/web_tool/formatters.py
tools/v1/web_tool/cap_solver_handler.py
tools/v1/test/test_web_tool.py
```

T6 may add Web-local implementation modules under:

```text
tools/v1/web_tool/**
```

Recommended additions:

```text
tools/v1/web_tool/errors.py
tools/v1/web_tool/network_policy.py
```

T6 documentation:

```text
tools/v1/T6_WEB_IMPLEMENTATION_PLAN.md
tools/v1/T6_WEB_COMPLETION.md            # only after green implementation
tools/v1/TOOLS_V1_CONTRACT_FREEZE.md     # status only
```

T6 MUST NOT modify:

```text
tools/v1/_shared/**
tools/v1/file_tool.py
tools/v1/find_by_glob.py
tools/v1/terminal_tool.py
tools/v1/window_tool.py
tools/v1/desktop_tool.py
tools/v1/test/test_file_tool.py
tools/v1/test/test_glob_search_tool.py
tools/v1/test/test_terminal_tool.py
tools/v1/test/test_terminal_process_tree.py
tools/v1/test/test_window_tool.py
tools/v1/test/test_desktop_automation.py
tools/v1/live/**
se/**
cl/**
requirements*
```

T1–T5 are frozen dependencies.

---

# 2. Consumer blast radius

Repository search found these important consumers/contracts:

```text
se/tests/architecture/test_local_tool_loader.py
agents/v1/web-researcher/manifest.json
se/tests/architecture/test_p0_async_driver_and_gemini_text_contract.py
tools/v1/live/live_multisource_pipeline.py
tools/v1/live/live_real_system_operations.py
```

Required compatibility:

```text
TOOL_METADATA["name"] == "web_tool"
ToolResult.tool == "web_tool"
```

The agent manifest currently references:

```text
web_tool
```

The Python capability-driver regression explicitly depends on the sync `run()`
entrypoint being able to return an awaitable Task when called inside an already
running event loop.

Therefore T6 MUST preserve:

```text
sync caller:
  run(...) -> terminal ToolResult

active async loop:
  run(...) -> Task[ToolResult]
```

The future logical capabilities remain T7 work:

```text
web.search
web.read
web.read_many
```

---

# 3. Current physical actions

Current canonical physical metadata exposes:

```text
search
scrape
scrape_many
```

Current runtime also accepts:

```text
scrape_webpage
read
```

T6 freezes physical canonical actions as:

```text
search
scrape
scrape_many
```

Compatibility aliases:

```text
read            -> scrape
scrape_webpage  -> scrape
read_many       -> scrape_many
```

Canonical ToolResult.action always uses the canonical physical action.

T7 later binds:

```text
web.search    -> search
web.read      -> scrape
web.read_many -> scrape_many
```

---

# 4. Current Web Tool architecture

Current flow:

```text
run
  -> _async_execute
      -> async with WebTool
          -> execute
              ├─ search -> WebSearcher
              ├─ scrape
              │    ├─ ProxyManager
              │    ├─ curl_cffi static
              │    ├─ extraction
              │    └─ Playwright dynamic fallback
              └─ scrape_many -> asyncio.gather(scrape...)
```

Important existing positive invariant:

```text
Playwright resource owner loop is tracked
browser initialization is lifecycle-locked
run() closes WebTool on the same _async_execute loop
```

T6 MUST preserve this invariant.

---

# 5. Exact current P0 findings

## P0-WEB1 — no URL/SSRF policy

Current `scrape()` only checks:

```text
http://
https://
```

Therefore caller-controlled URLs can target:

- localhost;
- loopback;
- RFC1918/private space;
- link-local addresses;
- cloud metadata endpoints;
- IPv6 local/private addresses;
- hostnames resolving to those ranges;
- mixed public/private DNS answers.

T6 must fail closed before any network side effect.

## P0-WEB2 — redirect chain is not fenced

`curl_cffi` currently follows redirects implicitly.

The tool does not validate every redirect destination before following it.

A public URL can therefore redirect to a private/local endpoint.

## P0-WEB3 — browser subrequests are unrestricted

Current Playwright only blocks some image/font extensions.

Scripts can still cause requests to arbitrary local/private addresses.

Service Workers and WebSockets are not blocked.

This means validating only the top-level URL is insufficient.

## P0-WEB4 — Chromium security isolation is explicitly weakened

Current launch arguments contain:

```text
--no-sandbox
--disable-web-security
--disable-features=IsolateOrigins,site-per-process
```

These MUST NOT be production defaults.

## P0-WEB5 — unbounded scrape_many fan-out

Current:

```python
tasks = [self.scrape(...) for u in urls]
await asyncio.gather(*tasks)
```

creates one task per caller-supplied URL.

The dynamic browser semaphore does not bound static sessions/task creation.

## P0-WEB6 — proxy credentials can be returned to callers

Current formatted output includes:

```text
Proxy: <full proxy URL>
```

If configured as:

```text
http://user:password@proxy.example
```

credentials enter Tool output/model context.

---

# 6. Exact current P1 findings

## P1-WEB7 — blocking CAPTCHA solver on async path

`CapSolverHandler` uses:

```text
requests.post
time.sleep
```

and is called synchronously from async Playwright control flow.

It can block the event loop for tens of seconds.

It also introduces paid/external CAPTCHA-solving side effects into ordinary read.

## P1-WEB8 — synchronous proxy refresh inside async scrape

`ProxyManager.refresh_proxy_pool()` uses:

```text
ThreadPoolExecutor
blocking curl_cffi requests
```

and `WebTool.scrape()` calls it synchronously.

## P1-WEB9 — search failure is indistinguishable from valid zero results

Current DDGS exception:

```text
results = []
```

then HTML fallback failure also returns:

```text
[]
```

Consumers cannot distinguish:

```text
valid search with zero hits
provider unavailable/failure
```

## P1-WEB10 — retry semantics are incomplete

Current retry logic special-cases only HTTP 403.

It has no canonical behavior for:

```text
408
425
429
500
502
503
504
Retry-After
network reset
DNS error
timeout
redirect loop
```

## P1-WEB11 — truthiness defaults bypass explicit invalid values

Current:

```python
req_timeout = timeout or self.default_timeout
char_limit = max_chars or self.default_max_chars
```

Therefore explicit zero can silently become the default.

The same general issue exists for unbounded/negative search and batch parameters.

## P1-WEB12 — terminal results are presentation strings

Search/scrape return Markdown/Text/JSON strings rather than canonical ToolResult.

Expected failures are also presentation strings.

## P1-WEB13 — provenance is incomplete

Current result omits:

```text
final URL
redirect chain
HTTP status
fetch timestamp
content hash
content type
extraction engine
selector satisfaction
truncation reason
proxy-used boolean
```

## P1-WEB14 — latent async extractor bug

`extract_tables_and_charts()` contains:

```python
page_obj.content()
```

without awaiting it when the primary argument is not a string.

The current common path happens to supply an HTML string, but the helper contract is invalid.

## P1-WEB15 — wait_selector timeout is silently swallowed

Current browser path catches selector failure and continues as success.

If the caller requested a selector, timeout must be observable.

## P1-WEB16 — browser/rendered output and structured data are not comprehensively bounded

Static response bytes have a cap, but browser DOM HTML, table matrices, chart arrays,
title/snippet fields and search fallback response content lack one coherent hard budget.

## P1-WEB17 — search path uses synchronous DDGS in a worker thread

`asyncio.to_thread(DDGS.text)` avoids directly blocking the loop but does not provide
strong cancellation of the underlying synchronous provider call.

T6 should not make ordinary search lifecycle depend on an uninterruptible provider thread.

## P1-WEB18 — random human-simulation sleeps consume timeout nondeterministically

Current browser flow includes random mouse movement, scroll and random sleeps.

This is not part of the read contract and makes timeout behavior difficult to reason about.

## P1-WEB19 — raw backend exception text can leak into results/logs

Current static/browser functions return `str(e)`.

These messages may contain URLs, proxy credentials or backend-internal details.

---

# 7. T6 security model

T6 owns intrinsic network safety.

Future SE/CL policy does NOT replace these checks.

T6 must protect the local tool host against:

```text
caller URL
  -> private/local network
  -> redirect to private/local network
  -> browser subrequest to private/local network
  -> WebSocket to private/local network
  -> environment-proxy surprise
```

Future consumer policy still owns:

- whether the agent is allowed to browse;
- HITL;
- organizational allowlists/denylists;
- user permission scopes.

---

# 8. Web-local network policy module

Add:

```text
tools/v1/web_tool/network_policy.py
```

Responsibilities only:

- URL syntax normalization;
- scheme/userinfo/port validation;
- DNS resolution;
- global-address classification;
- redirect destination validation;
- direct-request DNS pin data;
- browser request allow/block decision.

No ToolResult formatting.

No browser ownership.

No proxy-pool management.

---

# 9. Canonical URL validation

Freeze:

```text
MAX_URL_CHARS = 8192
MAX_HOST_CHARS = 253
MAX_DNS_ADDRESSES = 16
DNS_TIMEOUT_SECONDS = 2.0
MAX_REDIRECTS = 5
```

Accepted schemes:

```text
http
https
```

Reject:

- missing hostname;
- embedded username/password;
- malformed port;
- port outside 1..65535;
- control characters;
- URL longer than hard maximum;
- localhost names;
- `.localhost`;
- `.local`;
- empty/malformed IDNA host.

Fragments are removed before network access.

The requested query string/path is otherwise preserved.

---

# 10. IP classification

For every target hostname, resolve A/AAAA asynchronously.

All returned addresses must be globally routable.

Block any answer that is:

```text
loopback
private
link-local
multicast
unspecified
reserved/non-global
IPv4-mapped IPv6 whose mapped IPv4 is non-global
```

If DNS returns a mixture of public + private addresses:

```text
BLOCK ENTIRE TARGET
```

Do not pick only the public answer.

Literal IP URLs are classified through the same rule without DNS.

DNS failure is distinct from policy blocking.

---

# 11. Static direct-request DNS pinning

For direct curl_cffi requests:

1. validate URL;
2. resolve host;
3. require all addresses global;
4. pin the validated resolution into libcurl via `CurlOpt.RESOLVE`;
5. disable automatic redirects;
6. after response, verify `response.primary_ip` is global and belongs to the validated address set;
7. process redirects manually.

This closes the DNS preflight → independent resolver gap for the direct static path.

Environment proxies must be disabled:

```text
trust_env=False
```

TLS verification stays enabled.

---

# 12. Proxied-request boundary

Configured proxies remain supported, but:

- proxy URL itself is validated;
- proxy endpoint must resolve to global addresses;
- proxy list/count is hard bounded;
- proxy credentials are never returned;
- target URL still passes the same syntax + local DNS global-address policy;
- redirects still receive policy validation before following.

The tool can guarantee that its local process does not intentionally connect to a
private/local proxy endpoint or private literal/locally-resolved target.

A remote proxy's independent DNS/network view is outside the local-host guarantee and
must be stated explicitly in the completion document.

---

# 13. Static redirect state machine

Static fetch uses:

```text
allow_redirects=False
```

For each 301/302/303/307/308:

1. read bounded Location header;
2. resolve against current URL with `urljoin`;
3. canonical-validate next URL;
4. DNS/global-address validate next host;
5. enforce MAX_REDIRECTS;
6. fetch next hop.

Redirect chain is recorded structurally.

A redirect to a blocked address fails before sending the next request.

---

# 14. Browser network policy

Before creating/navigating the page:

```text
context service_workers="block"
context route installed
context WebSocket route installed
then page created/navigated
```

HTTP(S) route guard:

- validate each network URL;
- DNS-resolve each network hostname;
- abort route when policy denies it;
- allow browser-local non-network schemes only when necessary (`about:`, `data:`, `blob:`);
- reject other network-capable schemes;
- then apply bandwidth/resource-type blocking.

No policy check is skipped because a request is an image/font/media resource.

---

# 15. Browser WebSocket policy

Ordinary Web read does not require live WebSocket transport.

T6 therefore blocks all browser WebSocket server connections.

Use Playwright `BrowserContext.route_web_socket()` before page creation and close the
routed socket without connecting to the server.

This avoids a second private-network path outside HTTP routing.

---

# 16. Browser sandbox/isolation contract

Remove:

```text
--no-sandbox
--disable-web-security
--disable-features=IsolateOrigins,site-per-process
```

Production launch may retain only non-security-weakening operational options that are
proved necessary, e.g. bounded headless/dev-shm behavior.

Do not set:

```text
ignore_https_errors=True
```

Context remains non-persistent.

Downloads are disabled.

Service workers are blocked.

---

# 17. Hard Web limits

Freeze:

```text
WEB_TOOL_VERSION = "2.0.0"

MAX_QUERY_CHARS = 2048
SEARCH_RESULTS:
  default = 5
  minimum = 1
  maximum = 25

READ_TIMEOUT_SECONDS:
  default = 10
  minimum = 1
  maximum = 60

MAX_STATIC_RESPONSE_BYTES = 10 MiB
MAX_SEARCH_RESPONSE_BYTES = 2 MiB
MAX_RENDERED_HTML_CHARS = 5,000,000

MAX_CONTENT_CHARS:
  default = 100,000
  minimum = 1
  maximum = 500,000

MAX_BATCH_URLS = 20

WEB_CONCURRENCY:
  default = 5
  minimum = 1
  maximum = 8

MAX_WAIT_SELECTOR_CHARS = 1024
MAX_TITLE_CHARS = 4096
MAX_SEARCH_TITLE_CHARS = 1024
MAX_SEARCH_SNIPPET_CHARS = 4096

MAX_RETRIES = 3
MAX_RETRY_AFTER_SECONDS = 5

MAX_PROXY_COUNT = 32
MAX_PROXY_URL_CHARS = 2048
MAX_PROXY_TEST_CONCURRENCY = 8

MAX_TABLES = 20
MAX_TABLE_ROWS = 200
MAX_TABLE_COLUMNS = 50
MAX_TABLE_CELLS_TOTAL = 10,000
MAX_TABLE_CELL_CHARS = 2,000

MAX_CHARTS = 20
MAX_CHART_DATASETS = 20
MAX_CHART_POINTS_TOTAL = 5,000
MAX_CHART_LABEL_CHARS = 512
```

No environment variable disables these limits.

---

# 18. Total timeout semantics

Caller `timeout` is the total per-URL read budget, not a multiplier per retry.

At read start:

```text
deadline = monotonic + timeout
```

Every DNS, retry delay, static attempt, dynamic fallback, selector wait and extraction
step consumes the same remaining budget.

T6 MUST NOT preserve:

```text
dynamic timeout = req_timeout + 20
```

Explicit zero/negative/non-finite timeout is INVALID_ARGUMENT.

---

# 19. Retry classification

Retry only safe read requests.

Retryable categories:

```text
408
425
429
500
502
503
504
transient connect/reset/network failures
```

403:

- if a configured proxy was used, the proxy may be evicted and a new proxy tried;
- direct 403 is not blindly repeated.

Honor `Retry-After` only when parseable and <= hard max / remaining deadline.

Backoff is bounded and deterministic; no unbounded random sleep.

Do not retry:

```text
SSRF/policy rejection
invalid URL
401
404
unsupported content type
response-size overflow
selector timeout
CAPTCHA/challenge-required
```

---

# 20. Static response body contract

Before consuming body:

- inspect Content-Length when present;
- reject declared body > hard response-byte limit;
- validate content type;
- stream body into a bounded bytearray;
- stop/fail once the hard byte cap is exceeded.

Supported content families:

```text
text/*
application/json
application/xml
application/xhtml+xml
other explicit textual XML/JSON variants
```

Binary/media downloads are not Web read content.

---

# 21. Search provider redesign

T6 ordinary search must not depend on an uninterruptible synchronous DDGS thread.

Canonical provider for T6:

```text
DuckDuckGo HTML over bounded async curl_cffi
```

Remove synchronous DDGS from the default execution path.

Search response:

- uses no live Internet in unit tests;
- has a bounded response-byte limit;
- has a bounded timeout;
- distinguishes HTTP/provider failure from valid zero hits.

Future additional providers can be added later behind the same provider result contract.

---

# 22. Search provider outcome semantics

Internal provider outcome distinguishes:

```text
VALID_RESULTS
VALID_EMPTY
FAILED
```

Therefore:

- valid HTML response + zero organic results => ToolResult success with empty list;
- network/provider/parser failure => `WEB_SEARCH_PROVIDER_FAILED`.

Do not silently convert provider failure to valid empty results.

---

# 23. Structured search ToolResult

Identity:

```text
tool = web_tool
action = search
version = 2.0.0
```

Success data:

```json
{
  "query": "python asyncio",
  "returned_count": 2,
  "provider": "duckduckgo_html",
  "results": [
    {
      "title": "Example",
      "url": "https://example.com/path",
      "snippet": "..."
    }
  ]
}
```

Search-result URL output is syntax-normalized:

- only HTTP(S);
- no embedded credentials;
- bounded length.

Search-result URLs are not DNS-fetched merely to display them.

---

# 24. Structured scrape ToolResult

Success data:

```json
{
  "requested_url": "https://example.com/",
  "final_url": "https://www.example.com/",
  "status_code": 200,
  "title": "Example",
  "content": "...",
  "content_format": "markdown",
  "content_chars": 123456,
  "returned_chars": 100000,
  "content_sha256": "<64 hex>",
  "method": "static",
  "content_type": "text/html",
  "structured_data": {
    "tables": [],
    "charts": []
  },
  "provenance": {
    "fetched_at": "2026-09-22T00:00:00+00:00",
    "redirect_chain": [
      {
        "url": "https://example.com/",
        "status_code": 301
      }
    ],
    "extractor": "trafilatura",
    "rendered": false,
    "proxy_used": false,
    "wait_selector_satisfied": null
  }
}
```

Rules:

- `content_sha256` is SHA-256 of the full bounded extracted content before caller `max_chars` truncation;
- `content_chars` counts full extracted content;
- `returned_chars` counts returned content;
- `meta.truncated=true` iff caller max_chars truncates returned content;
- proxy URL/credentials are never included.

---

# 25. Structured scrape_many ToolResult

Batch preflight validates the entire URL list before starting network side effects.

Batch result preserves input order:

```json
{
  "requested_count": 3,
  "succeeded_count": 2,
  "failed_count": 1,
  "results": [
    { "...canonical per-URL ToolResult..." }
  ]
}
```

Top-level batch `ok=true` means:

```text
the bounded batch orchestration completed
```

Individual URL failures remain individual nested ToolResults.

An invalid batch argument or URL that fails preflight prevents the batch from starting and
returns top-level failure.

---

# 26. Bounded batch worker model

Do not create one task per arbitrary caller URL.

After `MAX_BATCH_URLS` preflight:

```text
asyncio.Queue
+ min(configured_concurrency, url_count) worker tasks
```

Workers preserve result index.

All static + browser work is subject to the same WebTool request-concurrency bound.

Dynamic Playwright may retain a separate browser-context semaphore, but it cannot increase
overall concurrency above the WebTool bound.

---

# 27. Proxy manager redesign

Make proxy refresh fully async.

Do not call synchronous `ThreadPoolExecutor` from ordinary async scrape.

Proxy checks use bounded async curl_cffi requests with:

```text
MAX_PROXY_TEST_CONCURRENCY
hard proxy count
hard per-proxy timeout
trust_env=False
```

One async refresh lock prevents concurrent scrape calls from triggering duplicate pool refresh.

Fastest-proxy selection may remain nondeterministic among a bounded top-N for load spreading,
but output never reveals selected proxy URI.

---

# 28. Automatic CAPTCHA solving is removed from ordinary read

T6 ordinary Web read must not:

- send API keys/sitekeys to CapSolver;
- spend external solver credits;
- inject solver tokens;
- block async loop with `requests.post` or `time.sleep`.

Therefore:

```text
core.py no longer routes captcha_api_key into browser fetch
stealth.py no longer imports CapSolverHandler
cap_solver_handler.py is removed from ordinary Web Tool implementation
```

Recommended implementation diff:

```text
D tools/v1/web_tool/cap_solver_handler.py
```

If a challenge is detected:

```text
WEB_CHALLENGE_REQUIRED
```

with no bypass attempt.

`captcha_api_key` is removed from canonical metadata.

For physical compatibility, if legacy caller supplies a non-null key to `execute/scrape`,
return `INVALID_ARGUMENT` rather than silently ignoring or spending it.

A future explicit CAPTCHA capability, if ever desired, requires a separate contract/security review.

---

# 29. Stealth behavior simplification

Keep only deterministic browser-compatibility/automation masking that does not weaken browser
security boundaries.

Remove random human-simulation sleeps/mouse/scroll behavior from ordinary read.

The read contract must not depend on random timing.

Challenge detection may remain as a pure detector.

---

# 30. Selector semantics

If caller supplies `wait_selector`:

- validate non-empty bounded string;
- browser must wait for it within remaining deadline;
- success records `wait_selector_satisfied=true`;
- timeout returns `WEB_SELECTOR_TIMEOUT`.

Do not silently swallow selector failure.

When no selector supplied:

```text
wait_selector_satisfied = null
```

---

# 31. Browser rendered-content bounds

Before/while dynamic extraction:

- bound top-level navigation timeout;
- reject known oversized Content-Length;
- block media/images/fonts unless needed;
- cap final `page.content()` characters;
- fail with `WEB_RESPONSE_TOO_LARGE` if rendered HTML exceeds the hard limit.

The browser path must not return partial HTML as success after this hard limit.

---

# 32. Structured data extractor contract

Refactor:

```python
extract_tables_and_charts(html: str, page_obj: Any | None = None)
```

The helper always receives an explicit HTML string.

It never calls async `page_obj.content()` internally.

Chart evaluation may use `page_obj.evaluate()` only.

Enforce table/chart hard limits while iterating, not only after constructing unbounded matrices.

Malformed rowspan/colspan values cannot escape as raw exceptions.

---

# 33. Main-content extractor contract

Refactor extractor to return:

```text
content
extractor_name
```

Example:

```text
trafilatura
beautifulsoup
```

Extraction failure:

```text
WEB_EXTRACTION_FAILED
```

Do not use presentation strings.

CPU-heavy bounded extraction may remain in `asyncio.to_thread`.

---

# 34. formatters.py decision

T6 terminal results are structured ToolResult objects.

Therefore Markdown/Text/JSON wrappers around the entire response are no longer the core result contract.

Recommended:

```text
D tools/v1/web_tool/formatters.py
```

Canonical metadata removes `output_format`.

Physical compatibility rule:

- omitted/default `output_format="markdown"` is accepted;
- legacy explicit `json` or `text` returns INVALID_ARGUMENT with a stable explanation that
  T6 returns structured ToolResult.

Do not silently ignore explicit caller formatting requests.

The extracted page content itself remains Markdown:

```text
content_format = "markdown"
```

---

# 35. Browser lifecycle ownership

Preserve one browser/Playwright owner event loop.

Invariant:

```text
Playwright created on loop L
      ->
all use on loop L
      ->
browser.close on loop L
      ->
playwright.stop on loop L
```

Cross-loop use/close fails closed.

Concurrent browser initialization still creates exactly one Playwright/browser pair.

---

# 36. run() lifecycle contract

Keep:

```text
no running loop:
  asyncio.run(_async_execute(...))

running loop:
  loop.create_task(_async_execute(...))
```

This is required by the existing PythonCapabilityDriver regression.

`execute()` itself does NOT own/close WebTool resources.

`_async_execute()` is the lifecycle owner for the global `run` wrapper.

---

# 37. Cancellation semantics

T6 must not turn `asyncio.CancelledError` into:

```text
WEB_NETWORK_ERROR
empty search
fake success
```

On cancellation:

1. stop batch workers;
2. close page/context;
3. close browser/Playwright when invocation owner exits;
4. preserve/re-raise cancellation.

Cleanup is bounded and cancellation-safe.

A cleanup failure must not mask `KeyboardInterrupt`, `SystemExit` or cancellation.

Expected ordinary Tool failure may be superseded by `WEB_CLEANUP_FAILED` when resource state
cannot be verified clean.

---

# 38. Context cleanup

Every dynamic read gets a fresh non-persistent BrowserContext.

Always close context in `finally`.

Do not swallow a context-close error after an otherwise successful operation.

If the original operation already failed, record cleanup failure without leaking backend exception
text; cleanup precedence follows the same expected-error vs control-flow rule used in prior tools.

---

# 39. Stable T6 error codes

Freeze:

```text
INVALID_ARGUMENT
DEPENDENCY_UNAVAILABLE

WEB_URL_BLOCKED
WEB_DNS_FAILED
WEB_REDIRECT_BLOCKED
WEB_REDIRECT_LIMIT

WEB_NETWORK_ERROR
WEB_TIMEOUT
WEB_HTTP_ERROR
WEB_RESPONSE_TOO_LARGE
WEB_UNSUPPORTED_CONTENT_TYPE

WEB_SEARCH_PROVIDER_FAILED
WEB_PROXY_UNAVAILABLE

WEB_BROWSER_START_FAILED
WEB_BROWSER_REQUEST_BLOCKED
WEB_SELECTOR_TIMEOUT
WEB_CHALLENGE_REQUIRED

WEB_EXTRACTION_FAILED
WEB_STRUCTURED_DATA_LIMIT
WEB_CLEANUP_FAILED
WEB_LOOP_OWNERSHIP_VIOLATION
```

SSRF/policy failures are not retryable.

Transient network/429/5xx terminal failures may set `retryable=true`.

Cancellation is not represented by a Tool error code.

---

# 40. Error secrecy

Do not place raw:

```text
str(exception)
proxy URL
proxy username/password
captcha API key
cookies
Authorization header
request headers
```

into ToolResult.

Use T1:

```text
redact_sensitive
redact_url_credentials
```

where a structured diagnostic value must be retained.

Valid requested/final public URLs may appear in successful provenance after userinfo rejection.

---

# 41. Search-result text bounds

For each search item:

```text
title <= MAX_SEARCH_TITLE_CHARS
snippet <= MAX_SEARCH_SNIPPET_CHARS
url <= MAX_URL_CHARS
```

Malformed provider items are skipped individually.

If provider response itself is structurally unusable as a whole:

```text
WEB_SEARCH_PROVIDER_FAILED
```

---

# 42. Title/content provenance bounds

Page title:

```text
<= MAX_TITLE_CHARS
```

Title truncation is represented explicitly:

```text
title_truncated: bool
```

Content truncation due caller max_chars uses canonical:

```text
meta.truncated
```

Hard response/DOM bounds are errors, not successful truncation.

This distinction is frozen:

```text
caller presentation bound -> successful truncation
resource safety hard bound -> failure
```

---

# 43. Optional dependency behavior

Web package import must remain safe if optional providers are missing.

Provider dependency failure occurs only when the relevant action/path is invoked.

Examples:

```text
missing curl_cffi -> DEPENDENCY_UNAVAILABLE for static/search/proxy path
missing playwright -> DEPENDENCY_UNAVAILABLE only when browser rendering is required
missing trafilatura -> BeautifulSoup fallback remains available
```

No installation instructions are mixed into terminal data.

---

# 44. T6-A — Foundation, validation, structured result identity

Modify:

```text
config.py
core.py
__init__.py
utils.py
test_web_tool.py
```

Add:

```text
errors.py
network_policy.py
```

Implement:

- `WEB_TOOL_VERSION=2.0.0`;
- hard limit specs;
- `_WebToolError`;
- canonical success/failure builders using T1;
- physical action/alias contract;
- query/URL/list/timeout/max_chars/max_results/wait_selector/bool validation;
- root name stays `web_tool`;
- `run()` active-loop Task semantics preserved.

Tests:

- metadata/root identity;
- invalid numeric bools/zero/negative/over-limit;
- alias canonical action;
- active-loop run returns awaitable and final ToolResult;
- sync run lifecycle closes exactly once;
- no SE/CL dependency.

---

# 45. T6-B — URL/DNS/redirect safety + static fetch hardening

Implement in:

```text
network_policy.py
scraper.py
```

Close:

```text
P0-WEB1
P0-WEB2
P1-WEB10
P1-WEB11
P1-WEB19 static path
```

Implement:

- URL normalization;
- userinfo rejection;
- DNS all-global policy;
- literal-IP policy;
- direct CURL DNS pinning;
- primary_ip verification;
- trust_env=False;
- manual redirect loop;
- bounded streamed body;
- content-type guard;
- total deadline;
- categorized retries/Retry-After.

Tests:

- loopback/private/link-local/metadata;
- IPv6 and IPv4-mapped IPv6;
- mixed public/private DNS;
- valid public DNS;
- redirect public→private blocked before second request;
- redirect loop/max;
- response.primary_ip mismatch;
- declared/streamed body overflow;
- retry classification;
- no raw exception leakage.

---

# 46. T6-C — Browser safety + sandbox + challenge isolation

Implement in:

```text
scraper.py
stealth.py
core.py
```

Close:

```text
P0-WEB3
P0-WEB4
P1-WEB7
P1-WEB15
P1-WEB18
```

Implement:

- remove unsafe Chromium flags;
- block service workers;
- install context route before page creation/navigation;
- validate every HTTP(S) subrequest;
- block all WebSocket server connections;
- disable downloads;
- browser rendered HTML hard bound;
- selector timeout is explicit;
- remove random human sleeps;
- challenge detection returns WEB_CHALLENGE_REQUIRED;
- ordinary read never calls CapSolver.

Recommended deletion:

```text
D cap_solver_handler.py
```

Tests:

- launch args do not contain unsafe flags;
- service_workers=block;
- private subrequest aborted;
- public subrequest continued;
- WebSocket closed without server connect;
- selector success/failure;
- CAPTCHA detection has zero external solver call;
- context always closes.

---

# 47. T6-D — Search + async proxy redesign

Implement:

```text
searcher.py
proxy.py
core.py
```

Close:

```text
P1-WEB8
P1-WEB9
P1-WEB17
P0-WEB6
```

Implement:

- async bounded DuckDuckGo HTML provider;
- remove sync DDGS from default path;
- valid-empty vs provider-failure semantics;
- bounded search response;
- fully async proxy refresh;
- proxy list/worker bounds;
- refresh lock;
- proxy endpoint network policy;
- proxy output reduced to boolean;
- never echo credentials.

Tests:

- search hits;
- valid empty;
- provider HTTP/network failure;
- malformed items;
- query/result bounds;
- async proxy concurrency;
- credentials absent from every result/error.

---

# 48. T6-E — Extractors + structured output/provenance

Implement:

```text
extractors.py
core.py
utils.py
```

Recommended deletion:

```text
D formatters.py
```

Close:

```text
P1-WEB12
P1-WEB13
P1-WEB14
P1-WEB16
```

Implement:

- structured search result;
- structured scrape result;
- final URL/status/redirect/fetched-at/hash/content type/method;
- extractor name;
- title bound;
- exact caller max_chars truncation;
- bounded tables/charts;
- fix async page.content contract by requiring explicit HTML;
- remove outer Markdown/Text/JSON presentation formatting.

Tests:

- exact ToolResult shape;
- JSON safety;
- content hash determinism;
- truncation semantics;
- static provenance;
- dynamic provenance;
- table/chart bounds;
- malformed rowspan/colspan;
- page_obj.content is never called by extractor.

---

# 49. T6-F — Bounded scrape_many + timeout/cancellation

Implement:

```text
core.py
scraper.py
test_web_tool.py
```

Close:

```text
P0-WEB5
cancellation/resource-cleanup gap
```

Implement:

- full batch preflight;
- MAX_BATCH_URLS;
- fixed worker count;
- shared request concurrency;
- order preservation;
- nested per-URL ToolResults;
- cancel workers on caller cancellation;
- await worker cleanup;
- no orphan browser contexts/tasks.

Tests:

- zero/over-limit batch;
- one invalid URL causes zero network start;
- concurrency never exceeds configured limit;
- output order equals input order;
- partial success;
- all per-URL failure;
- cancellation stops workers;
- no pending tasks after cancellation.

---

# 50. T6-G — Lifecycle/cleanup/resource-warning hardening

Implement:

```text
__init__.py
core.py
scraper.py
test_web_tool.py
```

Preserve/freeze:

- one Playwright owner loop;
- one concurrent browser initialization;
- close browser then Playwright exactly once;
- execute() does not close shared instance;
- run() owns exactly one WebTool lifecycle.

Add strict regression:

```text
ResourceWarning -> error
PytestUnraisableExceptionWarning -> error
```

where supported.

Tests:

- repeated close idempotent;
- close on owner loop;
- cross-loop use rejected;
- browser launch failure cleans Playwright;
- context failure closes context;
- cancellation preserves cancellation;
- cleanup failure precedence;
- no Proactor-style orphan lifecycle regression in mocked/default CI.

---

# 51. T6-H — Full Web gate + completion

Focused:

```text
python -m pytest -q tools/v1/test/test_web_tool.py
```

Then:

```text
python -m pytest -q tools/v1/test
```

Then full repository CI.

Additional strict Web gate when platform permits:

```text
python -X tracemalloc=25 -m pytest -q tools/v1/test/test_web_tool.py \
  -W error::ResourceWarning \
  -W error::pytest.PytestUnraisableExceptionWarning
```

Only after green:

- create `T6_WEB_COMPLETION.md`;
- mark T6 complete in source-of-truth;
- freeze T6→T7 Metadata V2.

T6-H does not edit Metadata V2 consumer code.

---

# 52. Detailed regression matrix — validation/network

1. root physical name is web_tool;
2. version is 2.0.0;
3. blank query;
4. overlong query;
5. max_results zero;
6. max_results bool;
7. max_results over hard max;
8. timeout zero;
9. timeout bool;
10. timeout over hard max;
11. max_chars zero;
12. max_chars bool;
13. max_chars over hard max;
14. blank wait_selector;
15. overlong wait_selector;
16. URL wrong scheme;
17. URL embedded credentials;
18. URL malformed port;
19. URL control character;
20. loopback IPv4;
21. loopback IPv6;
22. RFC1918;
23. link-local;
24. metadata endpoint;
25. multicast/reserved;
26. IPv4-mapped private IPv6;
27. mixed public/private DNS answers;
28. public DNS accepted;
29. DNS failure;
30. redirect to private blocked before next request;
31. redirect count hard limit;
32. direct primary_ip mismatch;
33. trust_env disabled.

---

# 53. Detailed regression matrix — static/retry/browser

34. static 200 textual response;
35. Content-Length over hard cap;
36. streamed body over hard cap;
37. binary content rejected;
38. 408 retry;
39. 429 Retry-After bounded retry;
40. 500/502/503/504 retry;
41. 404 no retry;
42. direct 403 no blind retry;
43. proxy 403 rotates proxy;
44. total timeout covers retries;
45. unsafe Chromium flags absent;
46. service workers blocked;
47. downloads disabled;
48. private browser subrequest blocked;
49. public subrequest allowed;
50. non-HTTP network scheme blocked;
51. WebSocket no server connection;
52. rendered HTML hard cap;
53. requested selector success;
54. requested selector timeout failure;
55. challenge detected;
56. challenge causes zero CapSolver/API request;
57. no random human sleep path.

---

# 54. Detailed regression matrix — search/proxy/output

58. search valid result;
59. search valid zero result;
60. search provider network failure;
61. search provider HTTP failure;
62. search body hard cap;
63. malformed search item skipped;
64. malformed whole search response fails;
65. search URL userinfo filtered;
66. proxy refresh async;
67. proxy count hard limit;
68. proxy test concurrency bound;
69. proxy endpoint private address rejected;
70. proxy credentials absent from success;
71. proxy credentials absent from failure;
72. structured search ToolResult JSON-safe;
73. structured scrape ToolResult JSON-safe;
74. final URL;
75. final status;
76. redirect chain;
77. fetched timestamp;
78. content SHA-256;
79. extractor provenance;
80. proxy_used boolean only;
81. exact max_chars no truncation;
82. max_chars+1 sets meta.truncated;
83. hard body overflow is failure, not truncated success;
84. title truncation flag.

---

# 55. Detailed regression matrix — extractors/batch/lifecycle

85. trafilatura extraction provenance;
86. BeautifulSoup fallback provenance;
87. extraction failure;
88. bounded table count;
89. bounded rows/columns/cells;
90. bounded cell chars;
91. malformed rowspan/colspan;
92. bounded chart count/datasets/points;
93. extractor never calls unawaited page.content;
94. batch empty;
95. batch >20;
96. invalid batch URL preflight stops all I/O;
97. batch preserves order;
98. batch partial failure;
99. batch worker concurrency bound;
100. batch cancellation drains workers;
101. dynamic context closes on success;
102. context closes on operation error;
103. context closes on cancellation;
104. browser initialized once under concurrent calls;
105. close idempotent;
106. owner-loop close;
107. cross-loop ownership rejected;
108. run sync path;
109. run active-loop Task path;
110. execute does not close caller-owned WebTool;
111. cleanup failure cannot mask cancellation;
112. no pending browser tasks after run completion.

---

# 56. No-live-Internet default test rule

Every T6 unit test must run without live Internet.

Use:

- fake DNS resolver;
- fake curl AsyncSession/Response;
- fake Playwright/browser/context/page/route/WebSocketRoute;
- fake monotonic/sleep where needed.

Default CI must not query:

```text
DuckDuckGo
api.ipify.org
example.com
CapSolver
any external proxy
```

Real Web smoke remains T8 opt-in.

---

# 57. Live harness boundary

Current live Web scenarios parse presentation strings and regex URLs from them.

T6 does NOT edit live harnesses.

T8 will migrate them to:

```text
ToolResult.data.results
ToolResult.data.content
nested scrape_many results
```

T6 only ensures the structured contract is complete enough for that migration.

---

# 58. Metadata boundary

T6 updates legacy/root V1 metadata bounds and action inputs.

T6 MUST NOT add:

```text
manifest_version
exports
expose_root
bind
per-export output_schema
per-export effects/idempotency
```

Those remain T7.

The physical root remains discoverable as `web_tool` until T7 handoff.

---

# 59. Expected implementation diff

Likely production:

```text
M tools/v1/web_tool/__init__.py
M tools/v1/web_tool/config.py
M tools/v1/web_tool/core.py
M tools/v1/web_tool/extractors.py
M tools/v1/web_tool/proxy.py
M tools/v1/web_tool/scraper.py
M tools/v1/web_tool/searcher.py
M tools/v1/web_tool/stealth.py
M tools/v1/web_tool/utils.py
A tools/v1/web_tool/errors.py
A tools/v1/web_tool/network_policy.py

D tools/v1/web_tool/cap_solver_handler.py
D tools/v1/web_tool/formatters.py
```

Tests:

```text
M tools/v1/test/test_web_tool.py
```

Completion docs only after green:

```text
A tools/v1/T6_WEB_COMPLETION.md
M tools/v1/TOOLS_V1_CONTRACT_FREEZE.md
```

Current plan:

```text
A tools/v1/T6_WEB_IMPLEMENTATION_PLAN.md
```

Forbidden implementation diff:

```text
NO tools/v1/_shared/**
NO File/Glob/Terminal/Window/Desktop
NO live/**
NO se/**
NO cl/**
NO requirements*
```

---

# 60. Completion invariants

T6 may be marked complete only if:

1. physical root remains `web_tool`;
2. run active-loop Task behavior is preserved;
3. no caller URL can directly target local/private/non-global address space;
4. every static redirect is validated before following;
5. direct static DNS is pinned/verified;
6. environment proxy inheritance is disabled;
7. browser top-level and subrequests are network-policy guarded;
8. service workers are blocked;
9. WebSockets do not connect to servers;
10. unsafe Chromium sandbox/web-security flags are gone;
11. static, dynamic, search and batch inputs are hard bounded;
12. scrape_many task/concurrency fan-out is bounded;
13. proxy credentials never enter ToolResult;
14. search provider failure differs from valid zero results;
15. Retry-After/retry categories are deterministic and deadline-bounded;
16. ordinary read performs no CAPTCHA-solving external side effect;
17. wait_selector failure is observable;
18. all public actions return canonical ToolResult;
19. search results are structured;
20. scrape results contain final URL/status/provenance/hash;
21. hard response overflow is failure;
22. caller max_chars is successful truncation;
23. structured table/chart extraction is bounded;
24. no latent unawaited page.content call remains;
25. cancellation propagates;
26. browser contexts close on success/error/cancellation;
27. Playwright single-owner-loop invariant remains green;
28. no Web unit test uses live Internet;
29. no T1–T5 implementation/test change;
30. no live/se/cl/requirements change;
31. focused Web tests green;
32. all tools/v1 tests green;
33. full repository CI green.

---

# 61. T6-A→T6-H implementation order

```text
T6-A  Foundation + validation + ToolResult identity
  ↓
T6-B  URL/DNS/redirect safety + static fetch
  ↓
T6-C  Browser subrequest safety + sandbox + challenge isolation
  ↓
T6-D  Search + async proxy redesign
  ↓
T6-E  Structured extraction + provenance
  ↓
T6-F  Bounded scrape_many + cancellation
  ↓
T6-G  lifecycle/resource-warning hardening
  ↓
T6-H  full gates + completion document
```

Do not implement presentation/output refactoring before the URL/network security layer is frozen.

Do not restore automatic CAPTCHA solving inside ordinary `scrape`.

---

# 62. External dependency capability verification

The repository pins:

```text
curl_cffi==0.16.3
playwright==1.63.0
```

The T6 design relies only on capabilities available in those lines:

- curl_cffi supports explicit redirect control and exposes response primary IP;
- low-level Curl options support DNS resolve pinning;
- Playwright BrowserContext routing can intercept context requests;
- service workers can be blocked on the context;
- Playwright WebSocket routing exists before 1.63 and can close a routed socket without connecting to its server.

These dependency facts must be covered by mocked contract tests rather than live Internet tests.

---

# 63. Implementation verdict

T6 can be completed entirely inside the frozen Web Tool scope.

Target architecture:

```text
run / lifecycle owner
        ↓
canonical validation
        ↓
network policy
  ├─ URL syntax
  ├─ DNS/global IP
  └─ redirect/subrequest guard
        ↓
bounded provider
  ├─ async search
  ├─ static curl
  └─ sandboxed Playwright
        ↓
bounded extraction
        ↓
structured provenance
        ↓
canonical ToolResult
```

Central T6 invariants:

```text
PUBLIC URL INPUT
      ≠
PRIVATE NETWORK ACCESS
```

```text
BROWSER FALLBACK
      ≠
BROWSER SECURITY BYPASS
```

```text
PROXY CONFIGURED
      ≠
PROXY CREDENTIALS IN MODEL CONTEXT
```

```text
CANCELLATION
      ≠
ORPHAN PLAYWRIGHT PROCESS
```

**AUDIT STATUS:** COMPLETE  
**T5→T6 BOUNDARY:** FROZEN  
**T6 IMPLEMENTATION DESIGN:** FROZEN  
**T6-A→T6-H PLAN:** FROZEN FOR REVIEW  
**PRODUCTION CODE STATUS:** NOT STARTED  
**NEXT ALLOWED STEP:** implement T6-A→T6-H within the exact diff boundary above.
