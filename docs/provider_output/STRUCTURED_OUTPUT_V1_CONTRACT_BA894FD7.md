# SO-C0 — STRUCTURED_OUTPUT_V1 Provider-Neutral Contract Freeze

> **Authority:** Issue [#385](https://github.com/boxs-51/assistant/issues/385), Policy [#85 v2.5](https://github.com/boxs-51/assistant/issues/85), independent PRE-CLAIM **PASS/RELEASED** [#6059934493](https://github.com/boxs-51/assistant/issues/385#issuecomment-6059934493), owner exact-path CLAIM [#6060012926](https://github.com/boxs-51/assistant/issues/385#issuecomment-6060012926). This is a **DESIGN/ARCHITECTURE contract, not an implementation**.

## 0. Claim fence and provenance

```text
stage                              SO-C0 / STRUCTURED_OUTPUT_V1
policy                             #85 v2.5 / FOLLOW-LATEST
class                              CONTRACT + ARCHITECTURE EVIDENCE / ZERO PRODUCTION
baseline                           main@ba894fd772e5d12a9a33db6be0b8198820d5578a
independent_PRE_CLAIM              PASS/RELEASED #6059934493
owner_CLAIM                        EXACT 2 NEW PATHS #6060012926
path_1                             ADD docs/provider_output/STRUCTURED_OUTPUT_V1_CONTRACT_BA894FD7.md
path_2                             ADD se/tests/architecture/test_structured_output_v1_contract.py
exact changed paths                2 NEW / 2
third path                         PROHIBITED
se/src/** delta                    ZERO
cl/** delta                        ZERO
production PRE-CLAIM / CLAIM       NONE / NONE
schema / migration authority       NONE
Tool / PTC / DCS / AOS authority  NONE
AE / recovery / checkpoint         NONE
CTX / Memory / CAS / UBQ           NONE
provider adapter / runtime        NONE
client / APR AgentProfile         NONE
merge authority                   NONE
```

Baseline Architecture #2367 (run 37776169690) Linux job 113307462607 **SUCCESS**, Windows job 113307463177 **SUCCESS** on the exact baseline. These results prove baseline health only; they **do not** prove structured output is implemented, usable or tested against real models. This SO-C0 document and architecture test are only the two files independently released. A later production stage needs its own exact-path independent PRE-CLAIM/CLAIM, owner handoffs and regression evidence.

## 1. Terminology / strict output contract

- **STRUCTURED_OUTPUT_V1** is the canonical provider-neutral request/final-answer constraint. It is **not** an instruction permitting Tool/Skill access; is **not** PTC tool-parameter schema; is **not** APR AgentProfile, CTX persona/Memory, or a client-owned system message.
- Default omitted format **TEXT** preserves existing request behavior byte-for-byte where possible (no implicit prompt injection, adapter rewrite or extra inference).
- **JSON_OBJECT** demands a syntactically valid JSON object at terminal acceptance, without business-schema conformance. Native "JSON mode" is advisory; the server is authoritative for parsing.
- **JSON_SCHEMA** demands an object matching the normalized portable schema, and requires `strict=true`. Invalid schema or unsupported provider/model/endpoint/tool/stream combo is fail-closed **before** physical provider dispatch. Native provider schema controls are advisory, never substitute for independent local validation. No silent coercion or downgrade to JSON_OBJECT/TEXT.
- A **provisional token/chunk** is neither validated final data nor durable truth. A **committed final answer** has passed complete terminality, JSON parse, schema validation and canonical execution fence.
- **No cross-domain authority:** only canonical AE/Session/CTX owners can write checkpoints, transcript, memory or state; this contract does not create a parallel validator-owned persistence ledger.

### 1.1 Canonical public shape (future production DTO)

```json
{
  "config": {
    "response_format": {
      "type": "json_schema",
      "json_schema": {
        "name": "motor_analysis_v1",
        "strict": true,
        "schema": {
          "type": "object",
          "properties": {
            "status": { "type": "string", "enum": ["normal", "warning", "critical"] },
            "summary": { "type": "string" },
            "recommendations": { "type": "array", "items": { "type": "string" } }
          },
          "required": ["status", "summary", "recommendations"],
          "additionalProperties": false
        }
      }
    }
  }
}
```

Other shapes: `{"type":"json_object"}` and omission for TEXT. Reject top-level bare strings (e.g. `"json"`), arbitrary dictionary shape, incorrect types, unrecognized keys, `strict=false` for JSON_SCHEMA and accidental nested tool schemas at ingress. A later version may explicitly introduce nonstrict schemas, but V1 does not silently reinterpret them. Compatibility migration must separately audit `cl/src/schemas/request.py` / `se/src/domain/schemas/request.py`, which currently declare an unrestricted `response_format` mapping.

### 1.2 Canonical internal shape and fingerprint

Conceptually immutable `OutputContract(revision, mode, schema_name?, strict?, normalized_schema?, schema_hash?, profile_version?, enforcement_policy)`. `revision = "STRUCTURED_OUTPUT_V1"`. A trusted server ingress resolver validates, normalizes and freezes it **once** per logical request; never allow untrusted `metadata.client_id` or a later client profile edit to change it. Per-attempt provider-native lowering derives a copy; retry, fallback, ordinary AGENT and recovery all consume the same frozen neutral value.

The pinned `schema_revision` is the literal `STRUCTURED_OUTPUT_V1`; it is included in both persisted output-contract identity and canonical fingerprint input. Canonical `schema_hash` is generated from the normalized schema envelope, not the provider-specific wire request.

Canonical JSON bytes for hashing use UTF-8, lexicographically sorted object keys, no insignificant whitespace, deterministic numeric representation, original **semantic** string/enum values and `ensure_ascii=false`; reject duplicate JSON object members at ingress. Schema identity is `SHA256(canonical JSON)` of `{"revision":"STRUCTURED_OUTPUT_V1","type":"json_schema","name":<validated_name>,"strict":true,"schema":<validated_normalized_schema>}`. The hash is a *schema identity* not an authorization token; owners bind hash to execution identity, tenant and durable AE/Session record via separately authorized implementation. JSON_OBJECT and TEXT have deterministic mode/revision identities but no fabricated JSON schema. Hash and authoritative schema snapshot, not a mutable profile pointer alone, must be recoverable.

### 1.3 Frozen portable JSON Schema V1 subset (proposed strict intersection)

- Require root `type:object`, explicit `properties`, `required` listing **every** property, and `additionalProperties:false`; all nested object nodes use the same closed-object rule. Duplicate required names/unknown properties are invalid.
- Permitted structural keywords: `type`, `properties`, `required`, `additionalProperties`, `items`, `description`, `enum`. Types: object, array, string, integer, number, boolean, null; optionality by omission is disallowed under strict intersection. `items` requires one schema. `enum` applies only at a compatible primitive node.
- Disallow `$ref`, `$defs`, remote references, `anyOf`, `oneOf`, `allOf`, `not`, `patternProperties`, `additionalItems`, `unevaluatedProperties`, `format`, transforms/defaults and provider-specific dialect keywords unless a **separately independently released** version extends the subset. No lossy dropping of required semantic keywords.
- Conservative V1 caps (proposal, must be verified against actual supported models at future production eligibility): 32 KiB canonical schema bytes, maximum nested depth 8, maximum total object properties 128, maximum enum values per node 64, maximum schema name length 64 and ASCII `^[A-Za-z][A-Za-z0-9_]*$`. Exceed => OUTPUT_SCHEMA_INVALID. Portable subset is a deliberately bounded compatibility subset, not a claim that every provider supports all schemas within it.
- Validate schema syntax/metaschema and semantic limits before dispatch. Parse final output **without coercion** and validate the complete JSON instance. For example integer vs string, missing required, extra keys, truncated JSON, refusal and safety-filtered output all fail, including cases where native provider reports "success".

## 2. Provider-native compatibility / capability matrix

The matrix describes **intended lowering on supported endpoint revisions only**; every actual model+provider+endpoint+schema+tool+stream combination requires capability evidence. Provider capability strings alone never grant eligibility.

| Provider endpoint | JSON_OBJECT native lowering | JSON_SCHEMA native lowering | Strict eligibility / stop rule |
|---|---|---|---|
| OpenAI **Chat Completions** | `response_format={"type":"json_object"}` | `response_format={"type":"json_schema","json_schema":{"name":name,"strict":true,"schema":schema}}` | Gate exact model, Chat Completions API revision, supported strict schema subset, tool usage and streaming. Reject unsupported schema/model before send. This contract does **not** target OpenAI Responses API native schema envelope. |
| Google Gemini **GenerateContent** supported new revision | JSON MIME through endpoint-documented `generationConfig.responseFormat.text.mimeType` | `generationConfig.responseFormat.text={ "mimeType":"application/json", "schema":schema }` when actually accepted by version | Gate exact endpoint/API revision, provider model, schema dialect, tools, stream. |
| Google Gemini **GenerateContent** legacy revision | `generationConfig.responseMimeType="application/json"` | `generationConfig.responseMimeType="application/json"` plus `generationConfig.responseJsonSchema=schema`, **only** on a revision that supports the pair | Do not infer backward/forward support; `responseMimeType` MUST be a string, never the format dictionary. `parametersJsonSchema` on function declarations is a **tool input contract**, not an output schema. |
| Ollama native `/api/chat` | `format="json"` | `format=schema` (JSON Schema object) | Gate native route and exact supported local model. Ollama Cloud / incompatible backends are NOT assumed to support structured output. |

A provider eligibility predicate for JSON_SCHEMA requires at least: `CHAT` or `CHAT_STREAM`, proven endpoint/model schema capability, portable-subset support, native strict constraints, and proven simultaneous `TOOL_CALLING` support if an in-progress tool cycle is involved. If the combination is not proven, reject it before provider call; do not replace a tool-call result with a schema-shaped fake final. A future separately authorized AGENT terminal-only finalization turn is an option, **not** created by SO-C0.

**PTC #8 boundary:** tool input `parametersJsonSchema` and `normalize_provider_tool_schema()` have distinct validation, response shape, authority and constraints. No unreviewed reuse of tool schema lowering as output schema lowering.

**Current-main gap inventory, not PASS evidence:** server `RequestConfig.response_format` remains a permissive dict; DIRECT/AGENT `InferenceRequest` lacks `output_contract`; `ProviderInferenceAdapter.serialize_request` carries generation params only; Gemini uses `responseMimeType = config_data["response_format"]`; Ollama compares a dict candidate against a set; OpenAI passes raw format through without normalized preflight. These are known **future production findings**.

## 3. Mode propagation and terminal acceptance invariants

### DIRECT

Future path: authenticated `GatewayChatRequest` ingress → trusted normalized `OutputContract` → `WorkflowRuntime._execute_direct` → `DirectChatRuntime.execute` → each `InferenceRequest` → `ProviderInferenceAdapter.serialize_request` → provider-neutral handler. Contract revision/hash cannot drop on tool iteration, context refresh or retry. Model-visible instructions do not gain Tool access or override server trusted prompt/Skills. DIRECT may not mutate privileged effects from formatting requests.

### AGENT ordinary + recovery

Future path: gateway → `WorkflowRuntime._execute_agent` → `AgentExecutionContext` / AE-owned frozen snapshot → `AgentContextAssembler` → `AgentRuntime` ordinary inference and **recovered inference** → `InferenceRequest` → provider adapter → AE-owned finalization. All inference creation sites (ordinary, recovered and persistence snapshot) must have the same contract identity semantics. A Tool-call continuation is *not* the JSON_SCHEMA final result; validate only terminal user answer. Upon invalid terminal response, **do not** append as a committed final transcript, write a successful checkpoint, transition to completed, or publish a successful execution event. AE #359 owns actual checkpoint/revision/reconciliation changes, including crash windows; future implementation must obtain independent owner release. #156 DCS/AOS owns capability/Skill/tool selection; schema instructions never mint capabilities.

### Session regenerate / provider direct

`SessionRegenerateRequest.config` enters `transport/gateway/api/v1/session_router.py` and calls provider `ChatExecutionHandler.execute_with_fallback` directly, bypassing workflow DIRECT/AGENT. Future ingress and validator must cover this path **before session persistence**. Existing media/hydration fences remain CAS-owned and unchanged.

### Fallback/retry and budget accounting

Keep the *same* normalized contract/hash and logical call identity through eligible provider fallback and retries, with immutable schema and unchanged canonical deadline/quota/recovery lease. Handler must exclude incompatible providers before physical send; all-ineligible => typed failure, **not** raw text success. Post-success application validation failures are terminal unless separately authorized by PTC/provider/AE-R10/UBQ policy; no free retry, no quota reset and no false circuit-breaker blame. Preserve provider attempt usage even when returned content is invalid. Do not bypass `ProviderRecoveryGuardError` or existing stream-start fallback barriers.

### Streaming, refusal, output terminality

Strict JSON_SCHEMA chunks are *provisional*. On the current SSE interface without a new versioned provisional/commit protocol, the safe V1 design is **bounded buffer → assemble exact raw answer → terminal finish/refusal checks → parse → validate → atomically publish committed result**. No unvalidated partial JSON is success, checkpoint truth or client-displayable validated data; a separate provisional UI protocol requires new independent release. Bound output bytes and buffered duration by existing request deadline/UBQ controls; overflow or timeout is fail-closed. Stream stop, disconnect, cancellation, refusal, safety/recitation filter, MAX_TOKENS/length, malformed UTF-8/JSON, missing required key or invalid final marker cannot publish COMPLETE. Trailing usage-only events do not mutate validated answer. No CAS generated asset payloads enter JSON-shaped text validation or bypass post-success asset fences.

## 4. Canonical failure and state matrix

| Fault | Terminal error class proposal | Must NOT happen |
|---|---|---|
| Unknown mode, malformed schema/dialect/hash, disallowed reference, excess caps | `OUTPUT_SCHEMA_INVALID` | Provider request sent / coercion |
| Model/endpoint/schema/tool/stream combination unsupported | `OUTPUT_FORMAT_UNSUPPORTED` | Silent format downgrade |
| Invalid JSON bytes or non-object JSON_OBJECT result | `OUTPUT_JSON_INVALID` | Gateway success |
| Valid JSON but missing key, wrong type, extra key, enum mismatch | `OUTPUT_SCHEMA_MISMATCH` | Correcting data to fit schema |
| Truncated output, length finish, incomplete response | `OUTPUT_INCOMPLETE` | Committed transcript / terminal success |
| Refusal / safety filter / policy-blocked generation | `OUTPUT_REFUSED` | Fake schema-conforming success |
| Strict stream truncated/disconnect/unvalidated tail | `OUTPUT_STREAM_INCOMPLETE` | Publish validated final before fence |

Error envelopes must preserve original provider status, model, finish_reason/refusal, consumed budget and recovery provenance without revealing secrets/raw private prompts. Error class ownership and HTTP/WS mapping are a **future production release**, not established by this document.

## 5. Regression specification and SO-C0 architecture evidence

SO-C0 architecture evidence checks the **design and known-source boundaries** read-only. It must **not** assert that production fixes are already in place. After its own focused checks and exact-head Linux/Windows CI, SO-C0 still needs an independent FINAL; it does not license production changes.

- **A-C01:** exact two new paths / independent release / no production claim.
- **A-C02:** discriminated output modes, portable dialect, bound caps, stable revision/schema hash.
- **A-C03:** OpenAI, new/legacy Gemini, native Ollama compatibility distinct; PTC parametersJsonSchema is never response schema.
- **A-C04:** DIRECT, AGENT ordinary/recovered and session regenerate propagation surfaces inventoried from actual source.
- **A-C05:** capability+fallback eligibility; no downgrade and no free retries.
- **A-C06:** refusal/length/schema mismatch fails prior to checkpoint, success event and streaming final.
- **A-C07:** independent evidence is baseline-truth; current unimplemented path remains future RED-first.
- **A-C08:** cross-track authority/no-third-path/zero-production explicitly remain CLOSED.

### Future RED-first production regression IDs — not SO-C0 GREEN

| IDs | Future suite | Mandatory cases |
|---|---|---|
| SO-T01..T05 | DTO/contract | mode union, unknown field, strict false reject, portable dialect/caps, stable normalized hash |
| SO-T06..T12 | Provider adapters | OpenAI JSON_OBJECT/schema; Gemini current+legacy schema; Ollama native JSON_OBJECT/schema; input immutability |
| SO-T13..T17 | Runtime | DIRECT propagation, AGENT tool iteration, ordinary/recovered path, hash pin, no success checkpoint on invalid JSON |
| SO-T18..T23 | Final validator | malformed JSON, missing required, wrong type, extra property/enum, refusal, truncated/length |
| SO-T24..T27 | Fallback | provider capability gating, unsupported all-candidates, no silent downgrade, unchanged deadline/usage |
| SO-T28..T31 | Streaming | partial JSON never committed, atomic final validated, interrupted stream errors, trailing usage/cancel fence |
| SO-T32..T35 | Gateway integration | HTTP request, regenerate same contract, persisted answer validation, OpenAI/Gemini/Ollama parity |

Future production work touching `se/src/provider/**`, `se/src/runtimes/**`, `se/src/transport/**`, `cl/**`, SQL, migrations or runtime schema requires **fresh** independent PRE-CLAIM/CLAIM, reviewer ownership disposition, focused LIVE/real WebSocket evidence where applicable, exact-head Architecture Linux+Windows, independent FINAL and Policy #85 Integration Wave authorization. A contract-only PASS is never production authority.

## 6. Owners and explicit non-scope

- **PTC #8:** tool input parameter schema and tool capability eligibility; independent negotiation for provider fallback changes.
- **AOS/DCS #156:** capability selection, Skill assembly, sandbox, tool envelope; output formatting cannot widen capability.
- **AE-R14 #359, AE-R10 provider:** crash, lease fence, checkpoint, retry/recovery authority; no alternate checkpoint.
- **CTX #15:** memory, user personalization, source authority; no hidden profile persistence.
- **APR #278/#374:** AgentProfile/runtime specialization; output format is not AgentProfile.
- **CL-UI #242:** client rendering; no client-private fake trusted system message.
- **UBQ #141, CAS #74:** quota/deadline accounting and asset/media semantics remain unchanged.

**Exit disposition of SO-C0 materialization:** exact two-file contract/evidence claim only; focused evidence and exact-head Linux/Windows Architecture still required; independent FINAL **PENDING**; no READY, no integration Wave enrollment or merge from this document.
