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

**Normative canonicalization algorithm: RFC 8785 / JSON Canonicalization Scheme (JCS)**, applied to the complete **frozen normalized contract envelope**. **Do not use** ordinary Python `json.dumps(sort_keys=True)`, a generic sorted-key serializer, a language-native float `repr`, or an equivalent-looking implementation without RFC 8785 compatibility tests. JCS requires I-JSON input (RFC 7493), rejects duplicate object-member names at schema ingress *before* normalization, rejects invalid Unicode/lone surrogates and NaN/Infinity, and serializes all JSON numbers through the ECMAScript `JSON.stringify`/IEEE-754 binary64 rules (including the note-2 shortest roundtrip algorithm), with no nonstandard NaN literals. Canonical object members are sorted **recursively by UTF-16 code units** of decoded property names, not by UTF-8 bytes, Python Unicode code point ordering, locale or insertion order; array order is preserved. Strings are not Unicode-normalized; escaping is the exact RFC 8785/ECMAScript JSON escaping. Output UTF-8 bytes contain no BOM, no insignificant whitespace. Reject non-interoperable integer/precision inputs instead of silently rounding semantically significant schema constraints; integers outside the interoperable safe-integer range `[-(2**53-1), +(2**53-1)]` are rejected in V1 when supplied as JSON numeric schema constraints, and values requiring higher precision must be encoded as semantically suitable strings by a future separately reviewed schema mode. Provider lowering MUST NOT modify this canonical envelope.

**Numeric canonicalization is not implementation-defined.** The following frozen test vectors are JSON numeric *input token* → RFC 8785 canonical *output token*, and apply equally to schema `enum` values and nested JSON numeric constraints:

| Input numeric token | Canonical JCS token |
|---|---|
| `1` | `1` |
| `1.0` | `1` |
| `-0` / `-0.0` | `0` |
| `1e21` / `1E+21` | `1e+21` |
| `1e-7` | `1e-7` |
| `1e-6` | `0.000001` |

Two equivalent normalized JSON-schema envelopes differing only in the source lexeme `1` versus `1.0` MUST hash identically. The **full-envelope** JCS canonical UTF-8 bytes for the reference schema (the `enum` in its source can be `[1]` or `[1.0]`) are exactly:

```json
{"name":"jcs_numeric_v1","revision":"STRUCTURED_OUTPUT_V1","schema":{"additionalProperties":false,"properties":{"n":{"enum":[1],"type":"number"}},"required":["n"],"type":"object"},"strict":true,"type":"json_schema"}
```

**SHA-256 hex of those exact UTF-8 bytes:** `fe0c0d88eeb1ec9ea16eb5daf905282cdd9249954c0172826956c3515b055045`. Independent numeric edge reference bytes `{"a":1,"b":0,"c":1e+21,"d":1e-7,"e":0.000001}` have SHA-256 `67611fb1557b34eff0be79a4442c41f4647f122d978cf12d2f36c32cb56f450b`. JCS UTF-16 ordering reference `{"a":1,"😀":2,"":3}` has SHA-256 `8043baa23995777ba780997c1db8282dbe99a54db473636cd9ff149f37e4853e` (supplementary Unicode 😀 sorts before BMP U+E000). These bytes/hashes are portable goldens, not evidence of a production serializer. A future production implementation MUST prove the complete envelope and cross-language numeric + UTF-16 golden vectors on real serializer libraries before persisting `schema_hash`.

Schema identity is `SHA256(canonical JSON)` of `{"revision":"STRUCTURED_OUTPUT_V1","type":"json_schema","name":<validated_name>,"strict":true,"schema":<validated_normalized_schema>}`, where `canonical JSON` specifically means the exact **RFC 8785 UTF-8 bytes**, not a provider-native request. The pinned `schema_revision` participates in the hash. The hash is a *schema identity*, not an authorization token; owners bind hash to execution identity, tenant and durable AE/Session record via separately authorized implementation. JSON_OBJECT and TEXT have deterministic mode/revision identities but no fabricated JSON schema. Hash and authoritative schema snapshot, not a mutable profile pointer alone, must be recoverable.

**Cross-language recovery invariant:** ingress (Python), browser/client (JavaScript), persistent storage, and resumed provider-attempt logic either derive the **identical RFC 8785 canonical bytes and SHA256** from the same frozen semantic envelope or fail closed with `OUTPUT_SCHEMA_INVALID`; never accept a mismatch by reserializing with a language-native repr, never rewrite the pinned hash during recovery. Strictly distinguish a V1 schema's numeric `enum` value from a string `"1"` (hashes and validators must not coerce them).

### 1.3 Frozen portable JSON Schema V1 subset (proposed strict intersection)

- Require root `type:object`, explicit `properties`, `required` listing **every** property, and `additionalProperties:false`; all nested object nodes use the same closed-object rule. Duplicate required names/unknown properties are invalid.
- Permitted structural keywords: `type`, `properties`, `required`, `additionalProperties`, `items`, `description`, `enum`. Types: object, array, string, integer, number, boolean, null; optionality by omission is disallowed under strict intersection. `items` requires one schema. `enum` applies only at a compatible primitive node.
- Disallow `$ref`, `$defs`, remote references, `anyOf`, `oneOf`, `allOf`, `not`, `patternProperties`, `additionalItems`, `unevaluatedProperties`, `format`, transforms/defaults and provider-specific dialect keywords unless a **separately independently released** version extends the subset. No lossy dropping of required semantic keywords.
- Conservative V1 caps (proposal, must be verified against actual supported models at future production eligibility): 32 KiB canonical schema bytes, maximum nested depth 8, maximum total object properties 128, maximum enum values per node 64, maximum schema name length 64 and ASCII `^[A-Za-z][A-Za-z0-9_]*$`. Exceed => OUTPUT_SCHEMA_INVALID. Portable subset is a deliberately bounded compatibility subset, not a claim that every provider supports all schemas within it.
- Validate schema syntax/metaschema and semantic limits before dispatch. Parse final output **without coercion** and validate the complete JSON instance. For example integer vs string, missing required, extra keys, truncated JSON, refusal and safety-filtered output all fail, including cases where native provider reports "success".

### 1.4 Strict generated JSON parsing: duplicate object members are terminally invalid

**Independent of the preceding schema-ingress duplicate-key gate**, **every** generated terminal text result in **both JSON_OBJECT and JSON_SCHEMA modes** MUST be parsed from the *exact assembled raw UTF-8 answer* by a strict JSON decoder that rejects duplicate object member names at **every nesting level**, including objects inside arrays, **before** application JSON Schema validation, Gateway success, AE/Session checkpoint/transcript persistence or public committed streaming response. This is mandatory even if the provider advertises native JSON/strict schema mode, claims a successful finish, or the duplicate values happen to be equal.

- Key identity is compared **after JSON string escape decoding** but **without Unicode normalization**: `{"x":1,"\u0078":2}` is duplicate and invalid; JSON strings with different Unicode scalar sequences remain different. Do not silently choose first/last member, merge values, overwrite a Python `dict`, or trust a default `json.loads` / `JSON.parse` path that discards duplicates before the check.
- The strict parser MUST operate on the original JSON token stream, using a duplicate-detecting per-object pair hook, streaming token parser or equivalent **before any lossy mapping or provider-specific text transformations**. Implementations may validate UTF-8/JSON syntax as part of that same pass. Reject trailing additional JSON documents, comments, leading prose, malformed escapes, unpaired surrogates, and non-finite literals as invalid syntax. Keep bounded byte/depth/timeout controls from the trusted request.
- Concrete **generated-output** negative vectors, applied to **both** `JSON_OBJECT` and `JSON_SCHEMA`, including nested objects:
  1. `{"status":"normal","status":"critical"}` — duplicate top-level `status`.
  2. `{"result":{"x":1,"x":2}}` — duplicate nested `x`.
  3. `{"items":[{"x":1,"\u0078":2}]}` — same decoded key inside array element.
  4. `{"status":"normal"}{"status":"critical"}` — two top-level JSON documents, not one valid final result.
  5. `{"result":{"a":1,"a":1}}` — duplicate even if values are identical.
- On any duplicate generated member or malformed/multiple-document JSON, terminal error = **OUTPUT_JSON_INVALID** (no successful final, no coercion, no local-schema success). Preserve original provider usage/deadline/failure attribution; **no free retry**, silent fallback or alternate parser that discards duplicates. This is not a Tool arguments parser change; PTC tool input schema/validation stays separately owned.
- For JSON_SCHEMA, run authoritative local schema validation **only after** strict duplicate-free JSON parsing succeeds; JSON_OBJECT must additionally require one top-level JSON object. A valid JSON shape but invalid JSON_SCHEMA instance uses **OUTPUT_SCHEMA_MISMATCH**. Streaming strict output remains provisional until this parsing and validation fence passes; unvalidated chunks cannot be published as a committed final.
- This section freezes **future production invariants only**. SO-C0 architecture tests may provide an isolated, source-independent parser/reference example for these five vectors, but **do not imply** that current provider adapters, DIRECT/AGENT, Session regenerate or SSE already enforce them.

## 2. Provider-native compatibility / capability matrix

The matrix describes **intended lowering on supported endpoint revisions only**; every actual model+provider+endpoint+schema+tool+stream combination requires capability evidence. Provider capability strings alone never grant eligibility.

| Provider endpoint | JSON_OBJECT native lowering | JSON_SCHEMA native lowering | Strict eligibility / stop rule |
|---|---|---|---|
| OpenAI **Chat Completions** | `response_format={"type":"json_object"}` | `response_format={"type":"json_schema","json_schema":{"name":name,"strict":true,"schema":schema}}` | Gate exact model, Chat Completions API revision, supported strict schema subset, tool usage and streaming. Reject unsupported schema/model before send. This contract does **not** target OpenAI Responses API native schema envelope. |
| Google Gemini **GenerateContent** supported new revision | `generationConfig.responseFormat.text={"mimeType":"APPLICATION_JSON"}` (new `TextResponseFormat.mimeType` **enum**) | `generationConfig.responseFormat.text={"mimeType":"APPLICATION_JSON","schema":schema}` when actually accepted by version | Gate exact endpoint/API revision, provider model, schema dialect, tools, stream; do NOT send legacy MIME string to enum field. |
| Google Gemini **GenerateContent** legacy revision | `generationConfig.responseMimeType="application/json"` | `generationConfig.responseMimeType="application/json"` plus `generationConfig.responseJsonSchema=schema`, **only** on a revision that supports the pair | Do not infer backward/forward support; `responseMimeType` MUST be a string, never the format dictionary. `parametersJsonSchema` on function declarations is a **tool input contract**, not an output schema. |
| Ollama native `/api/chat` | `format="json"` | `format=schema` (JSON Schema object) | Gate native route and exact supported local model. Ollama Cloud / incompatible backends are NOT assumed to support structured output. |

**Gemini MIME wire-version fence (SO-C0; future adapter only):** Current Gemini GenerateContent `TextResponseFormat.mimeType` is the `MimeType` **enum** and the JSON value is `APPLICATION_JSON` (not `application/json`); its companion `schema` is only valid with that enum. Conversely, legacy `GenerationConfig.responseMimeType` is a **string** using `application/json` and legacy `responseJsonSchema` is a distinct field. Both forms are endpoint/version gated: do not silently substitute either value into the other field, and fail closed when the endpoint does not support the requested representation. Source: [Google Gemini GenerateContent API, TextResponseFormat/MimeType](https://ai.google.dev/api/generate-content#TextResponseFormat). This SO-C0 contract asserts **future mapping requirements only**; it does not imply existing adapter support or confer production authority.

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

JSON_OBJECT and JSON_SCHEMA chunks are *provisional*; both modes require bounded buffering and strict terminal JSON validation before committed SSE publication. On the current SSE interface without a new versioned provisional/commit protocol, the safe V1 design is **bounded buffer → assemble exact raw answer → terminal finish/refusal checks → parse → validate → atomically publish committed result**. For both JSON_OBJECT and JSON_SCHEMA, strict terminal parsing MUST reject duplicate decoded keys at any nesting level, trailing JSON documents, invalid syntax and a non-object root BEFORE any SSE public chunk, successful event, persisted transcript or checkpoint. JSON_SCHEMA additionally requires local schema validation; JSON_OBJECT must NOT skip the duplicate-key and single-object gates. On parse failure, interruption or insufficient bounded buffer budget, emit only a typed error, not an early success or unvalidated partial answer. No unvalidated partial JSON is success, checkpoint truth or client-displayable validated data; a separate provisional UI protocol requires new independent release. Bound output bytes and buffered duration by existing request deadline/UBQ controls; overflow or timeout is fail-closed. Stream stop, disconnect, cancellation, refusal, safety/recitation filter, MAX_TOKENS/length, malformed UTF-8/JSON, missing required key or invalid final marker cannot publish COMPLETE. Trailing usage-only events do not mutate validated answer. No CAS generated asset payloads enter JSON-shaped text validation or bypass post-success asset fences.

## 4. Canonical failure and state matrix

| Fault | Terminal error class proposal | Must NOT happen |
|---|---|---|
| Unknown mode, malformed schema/dialect/hash, disallowed reference, excess caps | `OUTPUT_SCHEMA_INVALID` | Provider request sent / coercion |
| Model/endpoint/schema/tool/stream combination unsupported | `OUTPUT_FORMAT_UNSUPPORTED` | Silent format downgrade |
| Invalid JSON bytes, duplicate generated object members at any nested level, multiple JSON documents or non-object JSON_OBJECT result | `OUTPUT_JSON_INVALID` | Gateway success / checkpoint / streaming commit |
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
