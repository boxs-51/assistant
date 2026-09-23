# PTC-1 — Provider-Facing Tool Name / Schema Lowering Contract

## Status

Issue: #8  
Canonical baseline: main@1cae182b999b73335d774ff26537294a0c4b2ad4  
Branch: work/ptc-1-1cae182b  
Scope: PTC-1 only; provider-facing intermediate contract, no adapter wiring.

## 1. Preconditions

PTC-1 is open because:

- TV1-T8 is CLOSED / GREEN / FINAL-FROZEN;
- the pre-roadmap CI cleanup gate is merged into canonical main;
- PTC-1 file ownership is disjoint from the active AE-R10-A claim.

The canonical logical capability identity remains the TV1-T8 identity. Provider lowering is a transport concern and must never rename the catalog/runtime capability ID.

## 2. Fresh provider-name audit

The historical Issue #8 Gemini note was re-audited before implementation.

Current Gemini GenerateContent API documentation constrains function-call names to letters, digits, underscores, and dashes, with maximum length 128. Therefore dotted logical IDs such as web.search require a provider-local alias for Gemini as well as OpenAI.

Authority:

- Gemini GenerateContent API: https://ai.google.dev/api/generate-content
- OpenAI Function Calling: https://developers.openai.com/api/docs/guides/function-calling

PTC-1 uses a conservative OpenAI compatibility bound of 64 characters for Chat Completions-compatible function names. This bound is provider policy only; it does not constrain canonical capability IDs.

PTC-1 does not invent a name restriction for Ollama. Ollama keeps name identity at this stage; its native declaration/history envelope belongs to PTC-2.

## 3. Frozen invariants

### 3.1 Canonical identity is immutable

Logical capability_id is never replaced by a provider alias. web.search, web.read, web.read_many, and future logical IDs remain unchanged in catalog, routing, persistence, Agent execution, and ToolResult authority.

### 3.2 Alias scope is per request / provider contract

PTC-1 creates a bijection from logical_name to provider_name and back. The map is carried by provider request/response adapters in PTC-2. Provider names are never persisted as replacements for logical IDs.

### 3.3 Aliases are deterministic and readable

For a name that violates a provider name rule, the alias is a sanitized readable prefix plus an underscore plus a stable SHA-256 digest prefix.

The digest is derived only from the logical name, so alias generation is deterministic and independent of input ordering.

### 3.4 Collision handling is fail-closed

If two logical IDs would map to the same provider-facing name — including a generated alias colliding with an already-safe logical name — lowering fails before an outbound request is built.

PTC-1 never silently changes one side of a collision after the fact because that would make continuation/history decoding order-dependent.

### 3.5 Schema normalization cannot weaken authority

Canonical/local JSON-Schema validation remains authoritative.

PTC-1 schema normalization:

- operates on a deep copy;
- may remove transport-only top-level $schema dialect metadata;
- preserves semantic constraints such as required, additionalProperties, nested schemas, enums, and numeric/string bounds;
- never mutates GatewayToolDefinition.parameters;
- never turns a locally invalid schema into an accepted local schema.

Provider-native schema envelopes and provider-specific structural conversion are PTC-2 work.

## 4. Exact PTC-1 file ownership

Production:

- se/src/provider/core/tool_contract.py

Tests:

- se/tests/providers/test_provider_tool_contract.py

Documentation:

- docs/provider_tool_contract/PTC_1_CONTRACT_FREEZE_AND_IMPLEMENTATION_PLAN.md

No existing provider adapter, retry, routing, Agent, capability, or persistence file is owned by PTC-1.

## 5. Regression matrix

PTC-1 must prove:

1. OpenAI dotted logical names lower to provider-safe aliases and reverse exactly.
2. Gemini dotted logical names lower to provider-safe aliases and reverse exactly.
3. already-safe OpenAI/Gemini names stay unchanged.
4. Ollama remains identity-mapped in PTC-1.
5. aliases are deterministic and order-independent.
6. long OpenAI names remain within the compatibility bound.
7. duplicate logical names reject.
8. generated-alias collisions reject before request construction.
9. unknown encode/decode lookups reject.
10. unsupported providers reject rather than inheriting guessed rules.
11. schema normalization deep-copies and preserves semantic constraints.
12. lowering a GatewayToolDefinition does not mutate the source object.

## 6. Explicit non-goals

PTC-1 does not:

- change OpenAIChats.prepare_request();
- build OpenAI native function envelopes;
- modify OpenAI response decoding/history;
- modify Gemini request/response adapters;
- modify Ollama declaration or assistant/tool history conversion;
- require TOOL_CALLING during provider/model selection;
- change fallback selection, retry budgets, deadlines, error taxonomy, or cancellation;
- change Agent/R7 continuation semantics.

These are PTC-2, PTC-3, or AE-R10 responsibilities as frozen in Issue #8 and Issue #14.

## 7. PTC-1 / AE-R10 boundary

Active AE-R10-A owns provider retry/error representation, including se/src/provider/exceptions.py and its new retry-contract representation.

PTC-1 must not edit those files. If a future PTC stage needs a shared provider selection/fallback file, implementation stops for a fresh overlap audit before the file is claimed.

## 8. Exit gate

PTC-1 closes only when:

- focused contract tests are green;
- repository PR validation is green at the exact candidate SHA;
- no existing file outside the three claimed paths changed;
- Issue #8 records exact SHA and test evidence;
- PTC-2 receives a fresh claim instead of silently expanding PTC-1 scope.
