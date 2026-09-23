# PTC-3B — Tool Capability Eligibility Implementation Freeze

**Repository:** `boxs-51/assistant`  
**Issue:** #8  
**Canonical baseline:** `main@beadd3a48818daec137dcf69647d6f83c09c1a6b`  
**AE-R10 frozen runtime baseline inherited:** `dc4e9c1019eb0813546a6c0d88e208be73ebdec7`  
**Implementation branch:** `work/ptc-3b-beadd3a4`  
**Pull request:** #28  
**Status:** IMPLEMENTATION ACTIVE / TEST-FIRST

## 1. Goal

PTC-3B closes the remaining provider-selection correctness gap for requests
that carry tools.

For one candidate provider/model:

```text
non-stream:
CHAT
  -> if body.tools non-empty: TOOL_CALLING
  -> provider execution

stream:
CHAT_STREAM
  -> if body.tools non-empty: TOOL_CALLING
  -> provider stream execution
```

Canonical logical tool identities, aliases, schema lowering, retry/fallback
policy, deadline ownership and stream replay semantics are not redefined here.

## 2. Frozen ownership

PTC-3B owns:

- request-derived tool presence;
- conjunction of CHAT/CHAT_STREAM with TOOL_CALLING;
- capability-false provider ineligibility;
- tool-ineligible fallback skipping;
- strict/direct and enable_fallback integration proof;
- request-scoped/concurrency isolation;
- OpenAI/Gemini/Ollama eligibility-to-adapter exit coverage.

PTC-3B does not own:

- provider ordering;
- RoutingPolicy semantics;
- RetryPolicy;
- ProviderCallBudget creation or token policy;
- R10 provider error taxonomy;
- R10 stream replay/cleanup semantics;
- PTC-1 name aliasing;
- PTC-2 request/response adapter round-trip;
- PTC-3A schema normalization;
- TV1-T9;
- AE-R11/R12;
- CTX/Central Asset.

## 3. R10 inheritance

PTC-3B consumes these final AE-R10 invariants unchanged:

1. one ProviderCallBudget per logical handler call;
2. one monotonic logical deadline;
3. provider transition is not retry;
4. fallback does not reset retry tokens;
5. enable_fallback=false truncates the legal chain;
6. strict/direct is single-provider;
7. provider/model capability probe failures retain R10 error authority;
8. stream fallback is legal only before visible output;
9. after first visible chunk: no retry/fallback/replay;
10. cancellation/cleanup preserves authoritative outcome;
11. breaker accounting begins only with a real provider attempt.

## 4. Placement

Final R10 `ChatExecutionHandler` performs capability probes inside the
provider loop after routing/health filtering and before executor entry.

PTC-3B therefore stays in that handler.

No RoutingPolicy modification is authorized by this freeze.

The implementation helper must be stateless and request-scoped:

```text
_has_required_capabilities(
    provider,
    model,
    call_budget,
    base_capability,
    tools_present,
)
```

Required sequencing:

1. compute remaining timeout;
2. probe base CHAT/CHAT_STREAM;
3. if False, return ineligible immediately;
4. if no tools, return eligible;
5. recompute remaining timeout from the same budget;
6. probe TOOL_CALLING;
7. return that result.

This sequencing intentionally prevents reuse of stale timeout values.

## 5. Tool presence

```text
tools_present := bool(body.get("tools"))
```

Equivalent contract:

- absent -> false;
- null -> false;
- empty list -> false;
- non-empty canonical tool list -> true.

Tool/function messages in history do not imply TOOL_CALLING eligibility.

## 6. Failure semantics

Capability result `False`:

- candidate is ineligible;
- no executor/provider network attempt;
- no retry token consumed;
- no provider failure/cause fabricated;
- fallback may continue only if routing already permits it.

Capability probe error:

- is not equivalent to False;
- remains under R10 provider-error normalization/fallback authority;
- final exhaustion preserves last provider/cause.

Cancellation during a capability probe:

- propagates;
- starts no executor call;
- starts no later fallback provider;
- consumes no retry token.

Deadline exhaustion before or between probes:

- remains PROVIDER_DEADLINE_EXCEEDED;
- starts no provider execution;
- never creates a fresh budget/deadline.

## 7. Regression matrix M1-M35

### Core non-stream

- **M1** no tools + CHAT -> execute.
- **M2** tools + CHAT + TOOL_CALLING -> execute.
- **M3** tools + CHAT but TOOL_CALLING=False -> skip.
- **M4** first tool-ineligible, second eligible -> second executes.
- **M5** M4 consumes zero retry tokens.
- **M6** already-consumed retries remain consumed across a later tool-ineligible skip.
- **M7** all candidates tool-ineligible -> no network execution and no fabricated provider cause.
- **M8** strict/direct tool-ineligible selected provider -> fail closed, no second provider.
- **M9** empty tools behaves exactly as no tools.

### Streaming

- **M10** no tools + CHAT_STREAM -> stream.
- **M11** tools + CHAT_STREAM + TOOL_CALLING -> stream.
- **M12** tools + CHAT_STREAM but TOOL_CALLING=False -> exclude before stream creation.
- **M13** first stream candidate tool-ineligible, second eligible -> second may start.
- **M14** post-first-visible provider failure -> no replay/fallback; inherited and rechecked through R10-G.
- **M15** tool eligibility cannot switch provider after visible output; eligibility occurs before stream construction.

### Error/deadline/routing

- **M16** TOOL_CALLING=False -> no retry charge/network attempt.
- **M17** TOOL_CALLING probe error preserves R10 error authority and legal fallback.
- **M18** deadline already exhausted before required probe -> no execution.
- **M19** deadline expires between base and TOOL_CALLING probes -> no execution.
- **M20** strict/direct probe error remains single-provider and preserves cause.

### Cross-provider compatibility

- **M21** OpenAI native adapter is entered only after eligibility.
- **M22** Gemini native adapter is entered only after eligibility.
- **M23** Ollama native adapter is entered only after eligibility.
- **M24** PTC-1 provider-name aliasing unchanged.
- **M25** PTC-2 request/response round-trip unchanged.
- **M26** PTC-3A schema compatibility unchanged.
- **M27** existing no-tools chat/stream and R10 behavior remains green.

### Audit additions M28-M35

- **M28** CHAT=False short-circuits TOOL_CALLING.
- **M29** CHAT_STREAM=False short-circuits TOOL_CALLING.
- **M30** enable_fallback=false + TOOL_CALLING-ineligible first provider remains fail-closed/single-provider.
- **M31** TOOL_CALLING probe exception is not capability-False; fallback/final cause authority preserved.
- **M32** cancellation during TOOL_CALLING probe starts no executor/fallback progression.
- **M33** second capability probe recomputes remaining timeout from the same budget.
- **M34** history-only tool messages do not imply TOOL_CALLING when tools are absent/null/empty.
- **M35** concurrent tools/no-tools requests on one handler do not leak mutable eligibility state.

## 8. Test authority

New focused test module:

```text
se/tests/providers/test_ptc3b_tool_capability_eligibility.py
```

Inherited compatibility authority:

```text
se/tests/providers/test_provider_tool_contract.py
se/tests/providers/test_ptc2_tool_adapters.py
se/tests/providers/test_ptc3_schema_compatibility.py
se/tests/providers/test_r10_e_availability.py
se/tests/providers/test_r10_f_deadline_handoff.py
se/tests/providers/test_r10_g_streaming_hardening.py
se/tests/providers/test_r10_h_exit_matrix.py
```

The PTC module must not duplicate R10 race/cleanup tests; it proves only the
new tool-capability boundary and integration.

## 9. Production scope

Expected and currently sufficient:

```text
se/src/provider/handlers/chat_handler.py
```

Expected no-change surfaces:

```text
se/src/provider/policies/routing_policy.py
se/src/provider/policies/retry.py
se/src/provider/retry_contracts.py
se/src/provider/executor.py
se/src/runtimes/provider/runtime.py
provider adapters
```

Any required expansion is a new boundary finding and must be recorded on
Issue #8 before code.

## 10. Exit gate

PTC-3B closes only when:

1. M1-M35 are represented by direct or inherited proofs;
2. focused PTC-3B tests are green;
3. PTC-1/2/3A regressions remain green;
4. R10 provider tests remain green;
5. Linux Architecture full suite is green;
6. Windows client contracts are green;
7. diff remains within PTC ownership;
8. no blocking P0/P1 remains;
9. Issue #8 records the final PTC completion state.

