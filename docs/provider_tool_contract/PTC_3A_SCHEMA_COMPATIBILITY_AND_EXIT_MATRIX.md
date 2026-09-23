# PTC-3A — Provider Schema Compatibility / Exit Matrix

**Repository:** `boxs-51/assistant`  
**Baseline:** `main@a96215d192535bec25bc23ce8d59eec9e020845e`  
**Branch:** `work/ptc-3a-a96215d1`  
**Issue:** #8  
**Status:** PTC-3A implementation candidate; PTC-3B routing wiring remains blocked on AE-R10 shared-file handoff.

## 1. Scope

PTC-3A closes only provider-facing tool-schema compatibility that can be
implemented without touching AE-R10 shared routing/fallback ownership.

Owned production paths:

- `se/src/provider/core/tool_contract.py`
- `se/src/provider/gemini/converters/chats/request.py`

Owned regressions:

- `se/tests/providers/test_ptc3_schema_compatibility.py`

Explicitly not owned:

- `se/src/provider/handlers/chat_handler.py`
- `se/src/provider/policies/routing_policy.py`
- `se/src/provider/executor.py`
- RetryPolicy / ProviderCallBudget
- retry/fallback/deadline/provider-availability behavior
- PTC-3B `TOOL_CALLING` eligibility wiring

## 2. Canonical schema authority

`GatewayToolDefinition.parameters` is the canonical JSON Schema representation.
It intentionally remains a free `Dict[str, Any]` so nested MCP input schemas can
survive catalog -> inference -> provider lowering without being flattened into a
provider-specific model.

Provider lowering may deep-copy and remove transport-only metadata such as
top-level `$schema`, but it must not weaken or mutate canonical semantic
constraints. PTC-3A also normalizes the legacy OpenAPI-style uppercase JSON
Schema type tokens still accepted by older Gateway fixtures (for example
`OBJECT` / `INTEGER`) to canonical lowercase tokens on the provider copy,
recursing only through schema-bearing keywords. Instance data under
`default`, `const`, or `enum` is not rewritten.

Local execution remains authoritative for argument validation against the
canonical schema. Provider schema guidance is an inference contract, not a
replacement for local validation.

## 3. Gemini PTC-3A finding

Before PTC-3A, GenerateContent function declarations placed canonical JSON
Schema into Gemini `FunctionDeclaration.parameters`, then rewrote only the
root `type` to the provider `Schema` enum style.

Current Gemini GenerateContent exposes two distinct fields:

- `parameters`: Gemini/OpenAPI `Schema`
- `parametersJsonSchema`: JSON Schema value, mutually exclusive with
  `parameters`

Because the Gateway authority is JSON Schema, PTC-3A uses
`parametersJsonSchema`. This keeps nested `type`, `items`,
`additionalProperties`, numeric bounds, required keys, and other supported
JSON-Schema semantics in their canonical representation instead of partially
coercing only the root.

The shared PTC normalizer also enforces the Gateway invocation invariant that
tool arguments are a JSON object for all providers. An explicit non-object root
fails closed. A missing root type or an explicitly empty parameter schema is
bounded to `type: object` with empty/default `properties` on the provider
copy without mutating the source request.

## 4. Provider compatibility matrix

| Contract | OpenAI Chat Completions | Gemini GenerateContent | Ollama Chat |
|---|---|---|---|
| Canonical logical ID preserved outside provider wire | yes | yes | yes |
| Provider-safe reversible name map | yes, bounded alias | yes, bounded alias | identity at current PTC rule |
| Declaration envelope | `tools[].function` | `tools[].function_declarations[]` | `tools[].function` |
| Parameter schema field | `function.parameters` | `parametersJsonSchema` | `function.parameters` |
| Nested JSON-Schema constraints preserved by lowering | yes | yes after PTC-3A | yes |
| Assistant tool-call arguments | JSON string | object `args` | object |
| Tool-result continuation identity | `tool_call_id` | `functionResponse.name` | `tool_name` |
| Provider response restored to logical tool ID | yes | yes | yes |
| Request-scoped alias state | immutable / request scoped | immutable / request scoped | immutable / request scoped |
| Canonical source request mutated | no | no | no |
| Tool-capability-aware provider eligibility | PTC-3B pending | PTC-3B pending | PTC-3B pending |

## 5. Regression matrix

PTC-1/PTC-2 already prove name aliasing, declaration/history round-trip,
normal/stream response decode, source immutability, and provider-native
continuation shapes. PTC-3A deliberately does not duplicate those tests.

New PTC-3A regressions prove:

1. one nested canonical schema produces semantically equivalent provider-facing
   schemas for OpenAI, Gemini, and Ollama;
2. top-level `$schema` is transport-normalized while nested semantic
   constraints remain unchanged;
3. Gemini uses `parametersJsonSchema`, not `parameters`;
4. explicit non-object parameter roots fail closed consistently for all three
   provider adapters;
5. missing-root and empty parameter schemas are bounded to object schemas on
   provider copies only;
6. legacy uppercase JSON-Schema type tokens normalize recursively for all three
   provider adapters without rewriting instance data;
7. canonical source request/schema remains unchanged.

## 6. PTC-3B blocked boundary

Current `ChatExecutionHandler` checks only:

- `CHAT` for non-streaming inference;
- `CHAT_STREAM` for streaming inference.

A request with non-empty `body.tools` therefore does not yet require
`TOOL_CALLING`.

The natural wiring point is `chat_handler.py`, with possible interaction in
`routing_policy.py`. AE-R10 has already frozen both as shared ownership for
fallback/deadline/availability stages. PTC-3B must not edit those paths until an
AE-R10 durable handoff records the exact stable HEAD.

After handoff, PTC-3B must prove:

- tools + non-streaming -> `CHAT` and `TOOL_CALLING`;
- tools + streaming -> `CHAT_STREAM` and `TOOL_CALLING`;
- fallback skips tool-ineligible candidates without consuming retry budget;
- strict/direct mode fails closed on tool-ineligible requested providers;
- no PTC code changes retry/deadline/fallback lifecycle semantics.

## 7. Current provider references

Checked 2026-09-23:

- Gemini GenerateContent API:
  https://ai.google.dev/api/generate-content
- Gemini function calling:
  https://ai.google.dev/gemini-api/docs/function-calling
- OpenAI function calling:
  https://platform.openai.com/docs/guides/function-calling
- Ollama tool calling:
  https://docs.ollama.com/capabilities/tool-calling

These references describe provider wire compatibility only. Repository-local
canonical validation and execution authority remain unchanged.

## 8. PTC-3A exit gate

PTC-3A may close when:

- focused provider schema regressions are GREEN;
- full Architecture Baseline Linux + Windows are GREEN on exact HEAD;
- diff contains only the four claimed PTC-3A paths;
- no AE-R10 shared routing/retry path is modified;
- no new P0/P1 is found in last-mile audit.

PTC-3 as a whole remains OPEN until PTC-3B routing eligibility is implemented
after the AE-R10 handoff and the final cross-provider exit gate is rerun.
