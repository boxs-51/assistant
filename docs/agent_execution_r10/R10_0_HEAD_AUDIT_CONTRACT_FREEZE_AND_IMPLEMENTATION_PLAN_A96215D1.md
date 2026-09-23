# AE-R10 — Provider Retry / Fallback Hardening
## HEAD Audit, Contract Freeze, and Implementation Plan

**Repository:** `boxs-51/assistant`  
**Canonical merged baseline:** `main @ a96215d192535bec25bc23ce8d59eec9e020845e`  
**Planning branch:** `work/ae-r10-c-a96215d1`  
**Status:** **R10-0 CONTRACT FROZEN / PRODUCTION CODE NOT STARTED**  
**Prerequisite:** AE-R9-A→H merged/frozen through PR #13; pre-roadmap CI cleanup #16 merged GREEN; PTC-1/2 merged on main and confirmed file-disjoint from AE-R10 retry/error ownership.

---

## 1. R10 objective

AE-R10 owns provider-call retry/fallback hardening.

Canonical roadmap goal:

> Make provider behavior deadline-aware.

Required outcomes:

- parse provider retry hints;
- deadline-aware retry;
- one provider retry budget per logical inference call;
- immediate fallback when the retry delay cannot fit the remaining deadline;
- provider/model availability-aware routing;
- stable provider error taxonomy;
- cancellation-safe backoff and provider task cleanup;
- no retry storm and no hidden Agent-budget violation.

R10 does **not** change Agent RETRY semantics from AE-R9. Provider retry is an internal retry of one provider call; AE-R9 RETRY remains a new AgentExecution in the same TaskBranch.

---

## 2. Frozen ownership boundary

### AE-R10 owns

- provider-call deadline propagation;
- retry delay/hint interpretation;
- provider retry-attempt accounting;
- provider fallback selection and exhaustion;
- provider/model availability for inference routing;
- stable provider failure codes;
- cancellation during provider backoff;
- provider child-task cleanup;
- preservation of safe streaming fallback boundaries.

### PTC-3 owns, not AE-R10

- tool-capability eligibility;
- provider-facing tool compatibility;
- provider-native tool declaration lowering;
- provider-native tool history/message lowering;
- cross-provider tool-schema compatibility.

Shared provider routing/fallback files require exact R10 ownership claims before implementation. R10 changes in those files must be limited to provider-call retry/fallback/deadline/availability behavior and must not encode tool compatibility.

### Explicit non-scope

Do not absorb:

- AE-R11 persistence/performance optimization;
- AE-R12 execution lease / stale-RUNNING recovery;
- AE-R13 compatibility cleanup;
- AE-R14 broad fault injection / production chaos gates;
- TV1-T* tool metadata/export migration;
- PTC-1→PTC-3 provider tool-contract lowering;
- CAS-F5+;
- durable provider retry scheduling unless separately re-frozen.

---

## 3. Prior timing contracts that R10 must preserve

R4 already froze:

- operation deadlines are bounded by execution/iteration remaining time;
- short in-process provider retry backoff remains RUNNING and consumes active budget;
- short retry backoff must **not** create durable WAITING;
- `WAITING(RETRY_BACKOFF)` is legal only when retry is durably scheduled and runtime ownership is released.

R10 v1 therefore keeps provider backoff in-process. If a provider-requested/local delay cannot fit the remaining provider-call deadline, R10 falls back immediately rather than converting the execution to WAITING.

No R10 implementation may extend or reset an Agent execution/iteration deadline.

---

## 4. Current HEAD audit

### P0-R10-1 — provider retry/fallback has no internal deadline authority

Current path:

```text
AgentRuntime
  -> InferenceRequest.timeout_seconds
  -> ProviderInferenceAdapter outer child-task timeout
  -> ChatExecutionHandler.execute_with_fallback()
  -> ProviderExecutor.execute()
  -> RetryPolicy.apply()
  -> provider.chat.chat()
  -> BaseProvider.send()
```

The outer Agent adapter can cancel the whole provider task when the inference timeout expires, but inner provider retry/fallback does not receive the remaining deadline.

Material details:

- `RetryPolicy.apply()` accepts no deadline or remaining-time input;
- it may sleep exponential backoff without knowing whether another retry can fit;
- `ChatExecutionHandler` calls `ProviderExecutor.execute(...)` without a timeout;
- `ProviderExecutor.execute()` calls `provider.chat.chat(**kwargs)`;
- Gemini/OpenAI/Ollama chat implementations read `kwargs.get("timeout")`;
- therefore normal handler execution currently reaches provider HTTP with `timeout=None`;
- capability/model probing uses handler `self.timeout` (currently provider config timeout) rather than the caller's remaining inference deadline;
- direct call paths that invoke `execute_with_fallback()` do not have the Agent adapter's outer timeout fence.

Impact:

- inner retry cannot implement “retry vs immediate fallback” correctly;
- direct provider calls can lose the intended provider timeout;
- model/capability probing can consume more time than the remaining logical-call budget;
- provider behavior is not independently deadline-aware.

**R10 requirement:** derive one process-local monotonic logical-call deadline and thread remaining time through capability probing, attempts, retry sleep, and fallback.

---

### P0-R10-2 — provider retry hints are not parsed or represented

Current code has no canonical parsing for:

- HTTP `Retry-After`;
- Gemini / google.rpc `RetryInfo.retryDelay`;
- equivalent structured retry delay hints.

All raw HTTP 429 responses are treated as retryable and use local exponential backoff + jitter.

`wrap_provider_exception()` parses generic response bodies only after retry policy has already finished, and it does not expose a canonical retry delay.

Therefore the mandatory roadmap case:

```text
Gemini 429 RetryInfo > remaining budget
```

cannot currently trigger deterministic immediate fallback.

**R10 requirement:** normalize a finite non-negative `retry_after_seconds` before retry scheduling. Provider hints are lower bounds. Effective retry delay is at least the valid provider hint and may also respect local backoff.

---

### P0-R10-3 — retry budget resets for every fallback provider

Current `RetryPolicy.max_retries` is a per-`ProviderExecutor.execute()` count.

The fallback loop may give each provider its own full:

```text
initial attempt + max_retries
```

With N eligible fallback providers, the logical call can therefore expand toward:

```text
N * (1 + max_retries)
```

network attempts, plus capability/model probes.

There is no shared logical-call retry counter or retry-time budget across the fallback chain.

**R10 requirement:** one retry budget is created per logical inference/provider call and shared across all fallback candidates. A provider's first attempt is a fallback attempt, not a retry charge. Every additional network retry consumes one shared retry token. Thus inference calls are bounded by:

```text
eligible provider first-attempts + shared retry budget
```

and by the logical-call deadline.

Fallback must not reset consumed retry budget.

---

### P1-R10-1 — stable provider error vocabulary is incomplete

Current `ProviderError` carries:

- `provider_name`;
- `status_code`;
- `error_code` (often provider-native);
- `raw_response`;
- `failure_domain="PROVIDER"`;
- class-level `retryable`.

But no canonical system `code` exists for the main provider failure classes. After fallback exhaustion, AgentRuntime may expose a Python class name such as `NoAvailableProviderError` as the public/internal error code.

**R10 requirement:** freeze stable system codes while preserving provider-native error detail separately.

Minimum canonical vocabulary:

```text
PROVIDER_ERROR
PROVIDER_AUTHENTICATION_FAILED
PROVIDER_RATE_LIMITED
PROVIDER_UNAVAILABLE
PROVIDER_MODEL_UNAVAILABLE
PROVIDER_RESPONSE_INVALID
PROVIDER_FALLBACK_EXHAUSTED
PROVIDER_DEADLINE_EXCEEDED
```

`ProviderError.code` is the stable system code. Existing `error_code` remains available for provider-native codes where present.

Final fallback exhaustion remains terminal/non-retryable at the Agent boundary.

---

### P1-R10-2 — mapped model absence is conflated with provider failure

The roadmap explicitly requires:

```text
mapped Ollama model missing
```

Current Ollama capability/detail path:

- logical model is translated through the provider mapper;
- `OllamaModels._fetch_show_data()` catches every exception and returns `None`;
- `model()` then raises a generic `ProviderError` saying the model is missing **or** Ollama did not respond.

This erases the distinction between:

- model absent on an otherwise available provider;
- provider/network unavailable;
- malformed provider response.

The model capability cache can also be stale relative to the provider's installed models.

**R10 requirement:** model absence is provider-local ineligibility and causes immediate skip/fallback without consuming a retry token. Transport/provider failures remain provider failures and must not be silently rewritten as “model missing”.

R10 may correct availability semantics; cache/index/storage optimization remains AE-R11.

---

### P1-R10-3 — `provider.enable_fallback` is configured but not enforced

`ProviderSettings` defines:

```text
enable_fallback: bool = True
```

but current search finds it only in configuration/validation, not in actual routing/fallback execution.

**R10 requirement:**

- `enable_fallback=true`: use the resolved eligible chain;
- `enable_fallback=false`: execute only the first resolved eligible provider;
- metadata `strict/direct` remains single-provider regardless;
- R10 must not reinterpret PTC tool-capability routing through this flag.

---

### P1-R10-4 — fallback exhaustion loses structured last-cause authority

`ChatExecutionHandler` stores `last_exception`, then raises:

```text
NoAvailableProviderError("All providers in fallback chain failed.")
```

using only exception chaining.

The final error lacks stable attempted-provider / last-cause fields and currently relies on class-name fallback at higher layers.

**R10 requirement:** final fallback exhaustion has a stable code and preserves safe structured last-cause/provider information without leaking secrets or raw credentials.

---

### P1-R10-5 — cancellation/backoff cleanup is plausible but not proven at the required boundary

Existing `ProviderInferenceAdapter` already:

- owns a provider child task;
- cancels and gathers it on inference cancellation/timeout;
- cleans up its cancellation-wait task.

`asyncio.CancelledError` should also escape `RetryPolicy` rather than be treated as provider failure.

However there is no focused R10 proof for cancellation **during retry sleep**.

Required regression:

- enter retry backoff;
- cancel the inference;
- sleep exits immediately;
- no further retry occurs;
- no fallback provider starts after cancellation;
- provider child task is done;
- no leaked task remains.

---

### P1-R10-6 — streaming replay boundary must remain frozen

Current streaming fallback permits fallback before any chunk is emitted, but once a chunk has been emitted it raises rather than replaying/falling back.

R10 must preserve:

```text
0 emitted chunks -> fallback may be legal
>=1 emitted chunk -> no retry/fallback replay
```

Deadline propagation may be hardened for streams, but R10 must not duplicate visible output.

---

## 5. Canonical R10 timing/accounting model

One logical provider call gets a process-local context:

```text
ProviderCallBudget
    deadline_monotonic
    max_retries
    retries_used
```

This object is not durable and is not an AE-R11 persistence feature.

Deadline creation:

```text
effective_provider_call_timeout =
    min(
        caller remaining inference/operation timeout,   # when present
        ProviderSettings.timeout
    )

direct provider path without caller timeout =
    ProviderSettings.timeout
```

Every provider operation must use:

```text
remaining = deadline_monotonic - monotonic_now
```

and pass a network timeout no larger than `remaining`.

The same deadline covers:

- model/capability availability probes;
- provider attempt;
- retry sleep;
- next retry;
- fallback to later provider.

Fallback never resets the deadline.

---

## 6. Retry-delay contract

For retryable failure:

1. normalize provider hint, if present;
2. calculate local exponential backoff/jitter;
3. effective delay must not be earlier than a valid provider-requested retry delay;
4. if effective delay cannot fit inside remaining logical-call time, do **not** sleep;
5. re-expose the provider failure to the fallback handler immediately;
6. if fallback candidate exists and time remains, try it;
7. only an actual additional attempt consumes a retry token.

Invalid/negative/non-finite retry hints are ignored safely and must not create unbounded sleeps.

No durable WAITING is created by this in-process R10 policy.

---

## 7. Availability contract

Candidate eligibility is evaluated in deterministic routing order.

Skip/fallback without retry charge when:

- provider is not configured;
- circuit breaker is already open;
- mapped model is definitively absent;
- requested provider/model cannot serve the required inference capability.

Do not classify a transport failure during availability probing as “model absent”.

PTC-3 retains authority over **tool** compatibility. R10 only checks provider/model availability for the inference operation itself.

---

## 8. Error contract

R10 preserves:

```text
failure_domain = "PROVIDER"
```

Stable system code and provider-native code are separate concerns.

A provider-specific 429/error may carry:

```text
code                 = PROVIDER_RATE_LIMITED
error_code           = provider-native code, optional
retry_after_seconds  = normalized hint, optional
provider_name        = provider identity
status_code          = HTTP status, optional
retryable            = retry classification
```

Fallback exhaustion:

```text
code       = PROVIDER_FALLBACK_EXHAUSTED
retryable  = false
```

because provider routing/retry has already been exhausted for that logical call.

---

## 9. Primary implementation ownership

Expected R10 production ownership:

```text
se/src/provider/exceptions.py
se/src/provider/policies/retry.py
se/src/provider/executor.py
se/src/provider/handlers/base.py
se/src/provider/handlers/chat_handler.py
se/src/provider/policies/routing_policy.py
se/src/runtimes/agent/adapters/inference.py
se/src/infrastructure/config/schemas.py
se/src/provider/core/capability/__init__.py          # availability semantics only if required
se/src/provider/ollama/api/models.py                # missing-model taxonomy only
```

Conditional only if a red regression proves it necessary:

```text
se/src/runtimes/provider/runtime.py
direct provider HTTP/session transport call sites
provider-specific response/error adapters
```

Before changing any shared PTC provider file, re-check the exact diff and reject tool-contract changes.

---

## 10. Prohibited implementation patterns

Reject a patch that:

- resets provider deadline on fallback;
- gives every fallback provider a fresh retry budget;
- sleeps a RetryInfo/Retry-After delay beyond remaining time;
- converts short in-process provider retry to durable WAITING;
- catches `CancelledError` and continues retry/fallback;
- treats every 429 as equivalent without preserving provider hint/code;
- treats network failure as model-not-found;
- retries a definitively missing mapped model;
- falls back after streaming output has already been emitted;
- changes TaskBudget persistence or execution lease semantics;
- changes provider tool-schema/tool-history compatibility;
- uses wall-clock time for process-local retry elapsed-time accounting.

---

## 11. Required regression matrix

At minimum:

1. Gemini 429 RetryInfo greater than remaining deadline -> immediate fallback, no sleep/retry.
2. RetryInfo within remaining deadline -> exactly one bounded retry.
3. HTTP Retry-After normalization.
4. invalid retry hint -> bounded local backoff only.
5. shared retry budget does not reset across providers.
6. total additional retries never exceed the logical-call retry budget.
7. fallback does not reset logical deadline.
8. each provider HTTP attempt receives timeout <= remaining logical deadline.
9. capability/model probe also respects remaining deadline.
10. direct provider handler path uses configured provider timeout when no caller deadline exists.
11. mapped Ollama model missing -> skip/fallback without transient retry.
12. Ollama transport failure is not rewritten as model-missing.
13. `enable_fallback=false` prevents second-provider attempt.
14. strict/direct routing remains single-provider.
15. circuit-breaker-open provider is skipped without retry charge.
16. cancellation during retry sleep starts no further retry/fallback.
17. cancellation gathers provider child task.
18. outer Agent timeout still dominates provider retry/fallback.
19. fallback exhaustion yields stable `PROVIDER_FALLBACK_EXHAUSTED`.
20. provider-native error code/hint survive normalization where safe.
21. response validation failure remains non-retryable.
22. authentication failure remains non-retryable on the same provider.
23. pre-first-chunk stream failure may fallback.
24. post-first-chunk stream failure never replays/falls back.
25. existing R5 async-ownership tests remain green.
26. existing R2.1 failure transport contracts remain green.
27. AE-R9 full regression remains green.
28. Phase 5 + R8 + Architecture gates remain green.

---

## 12. Staged implementation plan

### AE-R10-A — representation + exact tests freeze

- stable provider system error codes;
- normalized retry-hint representation;
- ProviderCallBudget contract;
- unit tests only;
- no behavior wiring yet.

### AE-R10-B — error normalization + provider retry hints

- Retry-After parser;
- Gemini RetryInfo parser;
- preserve provider-native codes separately;
- deterministic invalid-hint handling.

### AE-R10-C — deadline-aware RetryPolicy

- monotonic deadline input;
- shared retry budget;
- bounded sleep;
- per-attempt remaining timeout;
- cancellation propagation.

### AE-R10-D — fallback budget/deadline convergence

- one budget across provider chain;
- immediate fallback when delay cannot fit;
- no retry-budget reset;
- fallback exhaustion stable error.

### AE-R10-E — provider/model availability

- deterministic provider/model eligibility;
- mapped Ollama missing-model behavior;
- preserve transport-vs-model-missing taxonomy;
- enforce `enable_fallback`.

### AE-R10-F — Agent inference handoff + async ownership

- pass caller inference deadline into ProviderRuntime;
- preserve outer Agent deadline as upper bound;
- cancellation during backoff;
- child-task cleanup regressions.

### AE-R10-G — streaming/direct-path hardening

- default configured deadline for direct handler paths;
- pre-first-chunk vs post-first-chunk fallback boundary;
- public/internal stable provider error transport where already exposed.

### AE-R10-H — race/exit matrix

- deterministic retry/fallback tests;
- no leaked tasks;
- no retry storm;
- no Agent-budget overrun;
- full R9/R8/Phase5/Architecture CI;
- completion/freeze checkpoint.

---

## 13. Stop conditions

Return to contract review before continuing if implementation appears to require:

1. a provider retry to create a new AgentExecution;
2. TaskBudget schema/persistence changes;
3. durable retry scheduling / `WAITING(RETRY_BACKOFF)`;
4. execution lease/stale-RUNNING ownership;
5. provider tool-schema/history conversion;
6. PTC tool-capability eligibility changes;
7. checkpoint persistence optimization;
8. broad production fault-injection infrastructure;
9. post-output streaming replay;
10. fallback that can reset deadline or retry budget.

---

## 14. R10 exit gate

R10 is complete only when all of the following are true:

- provider retry respects provider hints and remaining deadline;
- fallback happens immediately when retry delay cannot fit;
- one logical call has one bounded retry budget;
- fallback never resets caller deadline/accounting;
- mapped unavailable models are skipped deterministically;
- provider failures have stable system codes;
- cancellation during backoff leaves no provider task;
- stream output is never duplicated by fallback;
- no known P0/P1 remains;
- full current-head gates are GREEN.

Canonical exit statement:

> **No retry storm and no hidden Agent-budget violation.**
