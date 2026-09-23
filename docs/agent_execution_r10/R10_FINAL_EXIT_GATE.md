# AE-R10 FINAL EXIT GATE — Provider Retry / Fallback Hardening

**Repository:** `boxs-51/assistant`  
**Issue authority:** #14  
**Pull request:** #23  
**Final branch:** `work/ae-r10-d-3560a2d0`  
**Integrated base:** `main@3560a2d034d25659aa535ba91b90aa48256f5ac1`  
**Frozen R10 runtime/test HEAD:** `dc4e9c1019eb0813546a6c0d88e208be73ebdec7`  
**Exit-gate date:** 2026-09-23  
**Status:** **AE-R10 CLOSED / FINAL EXIT GATE GREEN**

> This document freezes the final AE-R10 provider retry/fallback implementation
> before integration. This documentation commit does not redefine the immutable
> runtime baseline above.

---

## 1. Final decision

AE-R10-A through AE-R10-H are complete as one provider-call resilience model.

R10 now provides:

- stable provider-system error codes while retaining provider-native detail;
- normalized retry hints;
- one monotonic deadline and one retry-token budget per logical provider call;
- deadline-aware provider attempts, probes, retry sleeps and fallback;
- global retry-token accounting across fallback providers;
- deterministic provider/model availability and `enable_fallback` behavior;
- Agent inference deadline handoff without restarting the caller clock;
- cancellation-safe retry/provider child-task cleanup;
- streaming fallback only before the first visible chunk;
- deterministic stream cleanup and stable public failure transport;
- exit-gate proof against retry storms, breaker-accounting races and cleanup races.

R10 does **not** introduce durable provider retry scheduling. Short retry backoff
remains active RUNNING time. A retry delay that cannot fit the remaining logical
deadline does not sleep; control falls through to the next legal provider.

---

## 2. Frozen authority model

### 2.1 One logical provider-call budget

One `ProviderCallBudget` is created for one logical handler call and is reused
across provider transitions.

It owns:

```text
deadline_monotonic
max_retries
retries_used
retries_remaining
```

Provider transition is not a retry and does not consume a retry token.

Retry tokens are global to the logical call. They are never recreated merely
because fallback moved to another provider.

### 2.2 Deadline authority

The effective provider deadline is bounded by both caller and provider limits.

For Agent inference:

```text
Agent execution / iteration remaining
        |
        v
absolute inference deadline_monotonic
        |
        v
ProviderInferenceAdapter outer wait
        |
        v
ChatExecutionHandler
        |
        v
ProviderCallBudget.deadline_monotonic
```

The caller deadline is an absolute process-monotonic deadline. It is not
restarted as a fresh duration after publish, reservation, scheduling or handler
entry.

Direct non-Agent provider calls without a caller deadline continue to use the
configured provider timeout.

### 2.3 Retry authority

`RetryPolicy` may consume an additional retry token only when:

1. the failure is retryable;
2. a retry token remains;
3. the retry delay fits inside the logical deadline;
4. cancellation has not interrupted the backoff.

Cancellation or deadline expiry during backoff does not consume a token and
does not start a later retry.

### 2.4 Fallback authority

Fallback provider transitions reuse the same logical budget.

When all legal providers fail before the deadline:

```text
PROVIDER_FALLBACK_EXHAUSTED
```

preserves stable last-provider and last-cause authority.

When the logical deadline expires first:

```text
PROVIDER_DEADLINE_EXCEEDED
```

remains authoritative and is not rewritten as fallback exhaustion.

### 2.5 Availability authority

Provider/model availability is distinct from provider failure.

For Ollama `/api/show`:

- HTTP 404 is authoritative model absence -> `PROVIDER_MODEL_UNAVAILABLE`;
- transport and non-404 HTTP failures remain provider failures;
- malformed successful response data -> `PROVIDER_RESPONSE_INVALID`;
- arbitrary 2xx `{"error": ...}`, empty/non-object JSON or unrelated shapes
  are not promoted into model availability.

Mapped model absence skips that provider without consuming a retry token.

### 2.6 Streaming authority

Streaming uses the same logical deadline model.

Fallback is legal only before any visible chunk is emitted.

After the first visible chunk:

```text
NO retry
NO fallback
NO replay
```

A mid-stream provider failure preserves the normalized provider failure/cause.

Provider read child tasks are cancelled and gathered before nested stream close.
Cleanup `Exception` is retrieved/logged and cannot replace an authoritative
provider/deadline/cancellation outcome. `BaseException` cancellation is not
swallowed.

### 2.7 Public runtime stream ownership

`ProviderRuntime` retains the handler stream object and closes it explicitly in
a `finally` block.

Therefore downstream publication failure or cancellation after a yielded chunk
cannot leave handler/executor/provider stream cleanup to GC timing.

The required ordering on publication failure is:

```text
visible chunk obtained
-> downstream publication fails
-> handler/provider stream deterministically closes
-> provider.failed is transported
```

On runtime cancellation:

```text
cancel runtime task
-> deterministic stream close
-> CancelledError remains authoritative
```

---

## 3. Stage closure summary

| Stage | State | Closed responsibility |
|---|---|---|
| R10-A | CLOSED | stable provider error vocabulary, retry-hint and ProviderCallBudget contracts |
| R10-B | CLOSED | Retry-After / Google RetryInfo normalization without broadening retryability |
| R10-C | CLOSED | deadline-aware RetryPolicy/executor and shared retry-token accounting |
| R10-D | CLOSED | one budget across fallback; deadline vs fallback authority convergence |
| R10-E | CLOSED | provider/model availability, Ollama missing-model taxonomy, enable_fallback |
| R10-F | CLOSED | Agent absolute deadline handoff and cancellation/backoff ownership |
| R10-G | CLOSED | streaming/direct-path deadline/fallback hardening and stable failure transport |
| R10-H | CLOSED | cross-stage race matrix, public stream consumer ownership and final exit gate |

Important immutable stage checkpoints:

```text
R10-D integrated GREEN: 15ec0aa58c9feb566e13e7baaef78cd101d85981
R10-E GREEN:            1048600da87d6f7935283ad06defbe19a8439e42
R10-F GREEN:            58df40c3f9cb8a5bfe0769355519798c2d3e0e2b
R10-G GREEN:            c68797290512d86cfd9073cebdfa57e9b7a9c4cc
R10-H final code:       dc4e9c1019eb0813546a6c0d88e208be73ebdec7
```

---

## 4. Final frozen invariants

### R10-FINAL-I01 — stable system provider error vocabulary

Provider-system `code` is stable and distinct from provider-native
`error_code`.

### R10-FINAL-I02 — provider-native detail is retained

Normalization does not destroy useful native status/error detail or causal
chaining.

### R10-FINAL-I03 — one logical deadline

Retry, probe, provider attempt and fallback all observe the same logical
deadline.

### R10-FINAL-I04 — one global retry budget

Fallback does not reset retry tokens.

### R10-FINAL-I05 — transition is not retry

Changing provider consumes no retry token.

### R10-FINAL-I06 — retry delay must fit

A backoff/hint that cannot fit the remaining deadline does not sleep or consume
a retry token.

### R10-FINAL-I07 — cancellation stops retry progression

Cancellation during backoff/provider child work starts no later retry/fallback
and drains owned child work.

### R10-FINAL-I08 — caller clock is never restarted

Agent inference passes one absolute deadline to the provider path.

### R10-FINAL-I09 — provider timeout remains a local upper bound

A provider-config timeout can shorten the caller deadline but never extend it.

### R10-FINAL-I10 — availability is not provider failure

Definitive mapped-model absence is provider-local ineligibility and consumes no
retry token.

### R10-FINAL-I11 — malformed provider response is not model absence

Malformed successful provider output remains provider response failure.

### R10-FINAL-I12 — enable_fallback is enforced

When disabled, routing does not continue through fallback providers.

### R10-FINAL-I13 — strict/direct is single-provider

Strict/direct routing remains single-provider even when no explicit preferred
provider is supplied.

### R10-FINAL-I14 — fallback exhaustion preserves last authority

Final fallback exhaustion preserves last provider and causal detail.

### R10-FINAL-I15 — stream fallback ends at first visible chunk

No provider replay/fallback occurs after any visible stream output.

### R10-FINAL-I16 — stream cleanup cannot replace primary authority

Nested cleanup noise cannot mask provider/deadline/cancellation semantics.

### R10-FINAL-I17 — breaker accounting starts with real provider attempt

Deadline expiry before the first provider stream read does not penalize the
circuit breaker. Once a read begins, a later provider/deadline failure is
accounted exactly once.

### R10-FINAL-I18 — ProviderRuntime owns its consumed stream

Downstream publication failure/cancellation deterministically closes the handler
stream before failure/cancellation escapes.

### R10-FINAL-I19 — retry-storm ceiling is global

Across N eligible providers, total network attempts are bounded by eligible
initial attempts plus the one globally shared retry budget.

### R10-FINAL-I20 — non-retryable failures remain non-retrying

Authentication and response-validation failures do not schedule backoff or
consume retry tokens.

---

## 5. Final H test-first evidence

H intentionally followed red-first protocol for the final stream consumer
ownership finding.

### Test-only RED candidate

```text
HEAD:
8fb9925e07ed064ef0e5a3f2455f8e3354ef0020

Architecture Baseline #898:
Windows client contracts: SUCCESS
Linux full suite:          FAILURE
```

The production runtime was unchanged at this point.

The deterministic failing proof required that the nested provider stream be
closed **before** `provider.failed` publication after a downstream chunk
publication error.

### Fixed final code candidate

```text
HEAD:
dc4e9c1019eb0813546a6c0d88e208be73ebdec7

Architecture Baseline #900:
Windows client contracts: SUCCESS
Linux full suite:          SUCCESS
```

The production fix is limited to:

```text
se/src/runtimes/provider/runtime.py
```

and H exit tests are contained in:

```text
se/tests/providers/test_r10_h_exit_matrix.py
```

---

## 6. H final exit matrix

The final H matrix proves cross-stage invariants that were not already
sufficiently covered by A-G.

### H1 — global retry-storm ceiling

Three-provider fallback with `max_retries=2` proves exactly:

```text
3 eligible initial attempts
+ 2 shared retry attempts
= 5 maximum attempts
```

The retry budget remains consumed when moving from p1 to p2/p3.

### H2 — circuit-open skip

A circuit-open provider is filtered before capability probe/network execution
and consumes no retry token.

### H3 — non-retryable execution behavior

`ProviderAuthenticationError` and `ResponseValidationError` each perform one
attempt, schedule no retry sleep and consume no retry token.

### H4 — visible stream failure public transport

After one public chunk, a provider failure produces:

```text
provider.stream.chunk_emitted
provider.failed
```

and never:

```text
provider.stream.completed
second-provider output
```

Stable provider system metadata is retained.

### H5 — downstream publication failure ownership

If chunk publication itself fails:

```text
nested provider close completes
before
provider.failed publication
```

### H6 — publication cancellation ownership

If the runtime task is cancelled while publishing a visible chunk:

- nested provider stream is closed;
- caller receives `CancelledError`;
- no second provider is started.

### H7 — cleanup failure cannot mask authority

Runtime stream `aclose()` ordinary exceptions are retrieved/logged and cannot
replace the original publication failure or cancellation.

---

## 7. Final code blast radius

Relative to integrated `main@3560a2d0`, R10 changes are limited to provider
retry/fallback/deadline/availability, Agent deadline handoff, runtime failure
transport, and focused compatibility/regression tests.

Production authority surfaces include:

```text
se/src/provider/exceptions.py
se/src/provider/retry_contracts.py
se/src/provider/policies/retry.py
se/src/provider/policies/routing_policy.py
se/src/provider/executor.py
se/src/provider/handlers/base.py
se/src/provider/handlers/chat_handler.py
se/src/provider/handlers/embedding_handler.py
se/src/provider/ollama/api/models.py
se/src/runtimes/agent/contracts/inference.py
se/src/runtimes/agent/adapters/inference.py
se/src/runtimes/agent/runtime.py
se/src/runtimes/provider/runtime.py
```

Focused R10 regressions:

```text
se/tests/providers/test_r10_a_provider_contracts.py
se/tests/providers/test_r10_b_retry_hint_normalization.py
se/tests/providers/test_r10_c_deadline_retry_policy.py
se/tests/providers/test_r10_d_fallback_budget.py
se/tests/providers/test_r10_e_availability.py
se/tests/providers/test_r10_f_deadline_handoff.py
se/tests/providers/test_r10_g_streaming_hardening.py
se/tests/providers/test_r10_h_exit_matrix.py
```

Compatibility-only architecture test adjustments preserve earlier Agent async
ownership contracts.

Final branch relationship before this documentation commit:

```text
base main: 3560a2d034d25659aa535ba91b90aa48256f5ac1
code HEAD: dc4e9c1019eb0813546a6c0d88e208be73ebdec7
behind main: 0
mergeable: true
```

---

## 8. PTC / Tools / CTX-CAS boundaries

### 8.1 Issue #8 — PTC-3B handoff

R10 does not implement TOOL_CALLING eligibility.

PTC-3B may consume the frozen R10 handler/budget semantics after R10 final
freeze.

PTC must preserve:

- non-stream tools: CHAT + TOOL_CALLING eligibility belongs to PTC;
- stream tools: CHAT_STREAM + TOOL_CALLING eligibility belongs to PTC;
- tool-ineligible provider skip must not consume/reset R10 retry tokens;
- PTC must reuse the same R10 logical deadline/budget;
- tool eligibility must be resolved before provider stream execution so R10's
  post-visible no-replay invariant cannot be weakened.

### 8.2 Issue #12 — TV1-T9

TV1-T9 remains parallel and normally file-disjoint.

It owns canonical logical tool IDs/schemas/binds and CLIENT/Agent projection.
It must not redefine provider retry/fallback/deadline/stream lifecycle.

### 8.3 Issue #15 — CTX-F / Central Asset downstream handoff

Future Central Asset provider hydration must consume, not redefine, R10:

1. provider selection/fallback precedes provider-specific hydration;
2. each fallback provider receives independently hydrated transient state from
   canonical asset identity;
3. hydration never creates/resets `ProviderCallBudget` or retry tokens;
4. provider/model ineligibility remains distinct from hydration failure;
5. no retry/fallback/replay occurs after first visible chunk;
6. cancellation must drain provider/hydration child work deterministically;
7. provider-native file IDs/URIs remain transient binding/cache state, never
   logical Message/checkpoint/CTX identity.

---

## 9. Explicit non-scope after closure

R10 closure does not implement or redefine:

```text
AE-R11 persistence/performance optimization
AE-R12 leases / stale-RUNNING recovery
AE-R13 broad protocol/data compatibility cleanup
AE-R14 broad fault injection / production exit
TV1 / TV1-T9 logical tool export migration
PTC tool schema/history lowering
PTC TOOL_CALLING routing eligibility
Central Asset F5+ provider hydration
CTX-F context/memory storage integration
durable provider retry scheduling
```

Those systems must consume R10's frozen provider-call semantics.

---

## 10. Merge/freeze rule

The immutable R10 runtime/test baseline is:

```text
dc4e9c1019eb0813546a6c0d88e208be73ebdec7
```

This documentation commit may move the branch HEAD. It must not be interpreted
as a new runtime baseline.

Before integration into `main`:

1. the final PR must remain mergeable and not behind current main;
2. conflict resolution must not reset logical retry budgets/deadlines;
3. PTC tool-specific eligibility must not be folded into the R10 merge;
4. full Architecture Linux + Windows gates must remain green on the frozen code
   semantics;
5. any later finding-driven runtime change reopens the relevant R10 invariant
   and requires exact-head regression/CI evidence.

---

## 11. Final closure statement

```text
R10-A  CLOSED
R10-B  CLOSED
R10-C  CLOSED
R10-D  CLOSED
R10-E  CLOSED
R10-F  CLOSED
R10-G  CLOSED
R10-H  CLOSED

FINAL RUNTIME/TEST BASELINE:
dc4e9c1019eb0813546a6c0d88e208be73ebdec7

FINAL ARCHITECTURE GATE:
#900 GREEN
- Linux full suite: SUCCESS
- Windows client contracts: SUCCESS

TEST-FIRST RED PROOF:
#898
- Linux full suite: FAILURE
- Windows client contracts: SUCCESS

BLOCKING P0:
NONE FOUND

BLOCKING P1:
NONE FOUND
```

AE-R10 Provider Retry / Fallback Hardening is frozen at this boundary.
