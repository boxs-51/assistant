# APR-RT1 — Realtime Streaming and Duplex Session Contract Freeze

Canonical governance: Issue #85 v2.5; APR tracker #278; stage workspace #397.  
Independent contract/evidence PRE-CLAIM: Issue #397 comment #6062976282 (PASS / RELEASED).  
Owner exact two-path CONTRACT/EVIDENCE CLAIM: Issue #397 comment #6063174844.  
**Stable development baseline:** `main@edd8e053d8233039b301a67f133a4a6006fe4f6d`.  
The `BDFA82F2` filename suffix is a fixed document identity, not the claimed baseline SHA.

## 1. Frozen scope and authority

```text
stage = APR-RT1
class = CONTRACT + ARCHITECTURE EVIDENCE / ZERO PRODUCTION
independent PRE-CLAIM = PASS / RELEASED
contract CLAIM = ACTIVE
development base = main@edd8e053d8233039b301a67f133a4a6006fe4f6d
exact changed paths = 2 NEW / 2
third path = PROHIBITED
se/src/** delta = ZERO
cl/** delta = ZERO
provider/transport/API/client/schema/migration/DB/workflow delta = ZERO
runtime/AE state/CTX Memory/UBQ/TBO/Tools authority = NONE
production PRE-CLAIM = HOLD / NOT RELEASED
production CLAIM = NONE
independent FINAL = PENDING
READY / FROZEN = NO
merge authority = NONE
```

Only these two **ADD** paths are released:
1. `docs/agent_platform/APR_RT1_REALTIME_STREAMING_SESSION_CONTRACT_BDFA82F2.md`
2. `se/tests/architecture/test_apr_rt1_realtime_streaming_session_contract.py`

A third path requires an independent PRE-CLAIM amendment. This contract **does not implement** a streaming method, voice engine, audio/video transport, Agent RuntimeSession persistence, WebSocket protocol, checkpoint, provider conversion, UI, or Tool. Architecture tests prove frozen boundary assertions, **not deployed realtime behaviour**.

## 2. Source-grounded baseline and capability boundaries

Current `se/src/runtimes/agent/contracts/inference.py::InferencePort` declares:
```python
async def complete(self, request: InferenceRequest) -> InferenceResponse:
    """Execute one non-streaming inference turn."""
```

`ProviderInferenceAdapter.complete()` delegates to `ChatExecutionHandler.execute_with_fallback()`, preserving a logical provider-call deadline, cancellation and quota context. The provider layer has **separate stream-capable paths**; their existence does not add `InferencePort.stream()` or authorize an Agent realtime session.

`cl/src/core/realtime_client.py::GatewayRealtimeClient` owns one receiver thread for its authenticated persistent capability WebSocket. Existing `/v1/events/ws` multiplexes remote client capabilities and registration; it is **not an Agent Live audio/duplex session**. A future session/stream adapter must undergo its own provider, transport, client and AE owner release.

APR-RT1 is **REALTIME** interaction latency, duplex input and provisional output. APR-FC1 is **FAST_CONTROL** revision/freshness-sensitive action selection. REALTIME != FAST_CONTROL; activating either profile does not grant more Tools.

## 3. Independent identities and transport generation

The contract distinguishes:
- authenticated `owner_user_id` (account authority) from AIC `agent_instance_id` (durable Agent identity);
- AE `execution_id` and AE `AgentExecution.revision` (durable execution authority) from `runtime_session_id` (interaction attachment);
- client `connection_id` and **transport generation** from provider `stream_request_id`, response `chunk_sequence` and causal correlation ID;
- `AgentExecution.owner_instance_id` is an AE recovery/lease owner **not** `agent_instance_id`.

Every input/partial/terminal projection MUST retain authenticated owner, runtime session, execution and current transport generation provenance. A reconnect MUST NOT mint a new Agent identity or reuse a stale transport generation as authority. A foreign-owner, unknown session/generation or mismatched request is rejected before any Tool dispatch or durable adoption.

Runtime-session closure/disconnect is **not automatically an AE terminal transition**; resume/recovery state is owned by AE, with client transport registration owned by the client/connection service. No new identity table, serialization schema or persisted session is authorized.

## 4. Conceptual provider-neutral InferenceStream

The following are **conceptual contract records**, NOT production Python classes or an API release:

```text
StreamOpen(owner_user_id, agent_instance_id, execution_id,
           runtime_session_id, connection_id, generation, stream_request_id,
           correlation_id, model, requested_modalities, capability_snapshot,
           caller_deadline)
StreamInput(owner_user_id, execution_id, runtime_session_id, connection_id,
            generation, stream_request_id, input_sequence, event_id,
            payload_type, inline_payload_or_immutable_payload_ref)
StreamChunk(owner_user_id, execution_id, runtime_session_id, connection_id,
            generation, stream_request_id, chunk_sequence, kind,
            provisional_payload)
StreamTerminal(owner_user_id, execution_id, runtime_session_id, connection_id,
               generation, stream_request_id, terminal_id, finish_reason,
               refusal_or_error, usage, final_payload)
StreamCancel(owner_user_id, execution_id, runtime_session_id, connection_id,
             generation, stream_request_id, reason, acknowledged_sequence?)
```

For every conceptual record, `owner_user_id`, `execution_id`, `runtime_session_id`, `connection_id`, `generation` and `stream_request_id` are **mandatory immutable provenance fields**. A trusted admission creates an immutable stream-request-to-provenance binding for the lifetime of the active request and bounded replay/terminal-retention horizon; the binding binds authenticated owner, AgentInstance, AE execution, runtime session, live connection generation and provider request identity. Every partial, input, cancel and terminal MUST validate against that binding and the currently authorized transport generation before presentation/adoption. A late terminal carrying an old generation or missing provenance MUST be rejected even if `stream_request_id` happens to match a valid logical request. On reconnect, a new transport generation requires fresh authenticated admission and a **freshly minted globally unique stream_request_id**; the prior stream_request_id MUST NEVER be re-bound, reassigned, reused, or rebound to a newer generation. The tuple (owner_user_id, execution_id, runtime_session_id, connection_id, generation, stream_request_id) has an immutable, append-only provenance entry through terminal-retention expiry; retain the old generation entry for bounded late-message rejection, not overwrite it. No old-generation partial/input/cancel/terminal can be adopted or presented in the new request, even if a correlation_id or logical conversation matches. A duplicate request ID crossing generations is a conflict and MUST fail closed.

`StreamInput.inline_payload_or_immutable_payload_ref` MUST carry **exactly one** of (a) a bounded inline payload whose type, byte-size ceiling and session authorization are validated or (b) a scoped immutable payload reference with validated owner, content identity/hash, length, media type, TTL and access authorization, resolved ONLY through the canonical CAS authenticated asset READ/USE authority and an explicitly released current Agent asset grant. Existing `se/src/application/assets/service.py::AssetService.get_asset/open_content` checks canonical authenticated `FileAsset.owner_user_id` and READY/readability for the user-owned asset; this user owner check is necessary but NOT sufficient to authorize the Agent. `docs/central_asset/CAS_AGENT_ASSET_GRANTS_ROADMAP.md` reserves per-asset Agent READ and USE permissions and revocation validation, but that grant implementation is RESERVED / NOT OPEN; therefore a future RT1 Agent inbound reference MUST be rejected until independently released CAS/Agent grant enforcement and hydration are actually present. F7-T `docs/central_asset/CAS_F7_T_TOOL_GENERATED_MEDIA_CONTRACT_522B543E.md` performs post-COMMITTED tool-result canonicalization only and explicitly does NOT fetch, open, resolve or follow references: it is NEVER an inbound reference dereference permission. `payload_type` alone is insufficient. No unspecified out-of-band input channel is allowed. Absent, expired, mutable, oversized, foreign or mismatched input content MUST fail closed without new Tool dispatch. This conceptual reference does not implement a new blob store, binary socket, microphone capture or Audio/VAD runtime; the existing owners must independently release any future production realization.

Admission MUST check the **actual provider/model/endpoint/mode capability**, independently for TEXT token streaming, AUDIO_INPUT, AUDIO_OUTPUT and true DUPLEX audio. Unsupported modalities MUST fail closed or negotiate an explicitly authorized compatible mode **before** publishing output; neither a provider marketing label nor an existing generic WebSocket implies support. No silent audio/video/VAD/speech codec guarantee.

A stream needs a monotonically increasing per-request `chunk_sequence` and stable terminal identity. Chunks are **provisional presentation events**; no chunk is a final inference response or new Tool call authority. StreamTerminal carries final/refusal/length/error/usage semantics without assuming every provider returns every field. Unknown terminal or schema state fails closed.

Provider retry or model fallback MAY occur only when safe under the already-owned provider/UBQ retry policy. **After the first visible chunk, fallback MUST NOT replay published content**, silently restart the caller deadline or present two terminal results as one completion. Partial-failure handling distinguishes pre-first-output failure, post-first-output failure, terminal refusal and caller cancellation.

## 5. Duplex input, barge-in and deterministic preemption

Full-duplex means session-scoped incoming events may arrive while output or authorized Tool work is in flight. The source of input, `input_sequence`, current transport generation, execution revision and cancellation cause MUST be correlated. The runtime must reject stale input and must not invent reliable exactly-once network delivery.

Priority under this conceptual contract:
1. **P0** trusted security revocation/emergency stop, authenticated owner revocation;
2. **P1** AE committed cancel/terminal state or active HITL denial;
3. **P2** validated live user barge-in/interrupt against the current session/generation;
4. **P3** caller deadline, TBO horizon or explicit session expiry;
5. **P4** eligible next stream chunk/input/Tool progress event.

Barge-in requires output cancellation and an acknowledgement/terminal-or-aborted boundary, **not merely muting playback**. Stop new emission and new Tool side effects when cancellation is effective; in-flight external effects require AE-R6 reconciliation and the Tool's own safety/cancel contract. Late chunks, late Tool results and stale cancellation acknowledgements must become observations or be rejected, never bypass AE durability.

## 6. APR-X1 sequencer and AE durable adoption

`APR-X1` observes before adopting, enforces stable same-revision ordering and serializes adoption requests. Its sequencer-local order is not durable `AgentExecution.revision`. A `DecisionCommit` is an adoption request, **not independent persistence authority**.

Only **AE expected-revision/CAS** can commit durable execution state, final transcript/checkpoint/terminal result and recovery state. A provisional `StreamChunk`, Tool progress, speech fragment, acknowledgement or `ResponseEmission` MUST NOT directly mutate AE state, CTX Memory, authorization, provider quota ledger, or irreversible Tool side effects.

If AE CAS loses, adoption fails closed: reload the canonical revision, reconcile then reevaluate authorized observations. Stream disconnect/replay cannot synthesize a newer AE revision. **Exactly-once adoption is not exactly-once network delivery**. Replayed chunk identities must be deduplicated or suppressed before display/adoption according to explicit consumer policy, not treated as a second durable completion.

Existing `AgentEventEnvelope.event_id` is a public projection identity, not AE revision or a substitute sequence number for this conceptual stream.

## 7. Bounded buffering and reconnect semantics

Session output/input queue and replay history need explicit **bounded buffer and backpressure** ceilings; unbounded queues are prohibited. A slow consumer must cause a bounded pause/coalesce/reject/close policy with recorded terminal reason; silently dropping required cancellation, refusal, usage or terminal signals is prohibited.

A reconnect has a new transport generation and **fresh authentication/authorization**. Reattachment requires a live authorized session, current AE execution eligibility and explicit replay horizon. No replay of Tool side effects, no renewed stale cancellation token, no promotion of provisional chunks to committed transcript. Missing gap/ack state must fail closed rather than claim gap-free playback. An absent or expired session MUST not resume from only a connection ID.

Cleanup cancels provider/transport child tasks within owner policies, releases bounded buffers, drains or reconciles in-flight Tool work, and preserves AE-owned durable state. Session cleanup does not delete CTX Memory or durable assets.

## 8. Deadline, provider fallback, UBQ and TBO fences

Current `InferenceRequest.deadline_monotonic` and logical provider retry budget have a **process-local clock domain**; raw monotonic timestamps must not be persisted or transported as a globally comparable deadline. A reconnect, new audio chunk, fallback model/provider, Tool progress event or retransmission MUST NOT reset the caller's absolute logical deadline or replenish exhausted budget.

The **UBQ owner** performs admission, streaming usage accounting and attribution. **TBO** owns task horizon and activation eligibility. A terminal response, replay or fallback MUST NOT double-charge or lose ambiguous usage; ambiguous provider accounting retains safe conservative attribution by UBQ policy. Cancellation and disconnection do not waive usage already incurred.

Deadline expiry, budget exhaustion, quota rejection, provider error and caller cancellation have distinct terminal/cancel classifications. The contract grants no new UBQ debit ledger, retry logic or TBO lifecycle authority.

## 9. SO-C0, schema validation and client/live UX

SO-C0 **JSON_SCHEMA terminal validation is atomic**: provisional chunks are not accepted as a validated final structured object, not emitted as trusted schema-conformant JSON, and not converted into Tool arguments before final owner validation. No schema-validation waiver via streaming, JSON_OBJECT or provider-native output support.

CL-UI #242 owns Live transcript presentation, barge-in button, playback/audio affordances, consent/HITL and user interaction. The client connection/transport owner retains authenticated WebSocket lifecycle, registration and generation fencing. No invented deployed voice stack, VAD, microphone capture, client UI event or `/v1/events/ws` protocol modification is permitted here.

AIC owns AgentInstance identity, CTX owns Memory promotion/retrieval, AE owns durability/faults/HITL, APR-X1 owns contract sequencing, DCS/SBX/Tools own capability selection and physical invocation; all retain their own future production PRE-CLAIM requirements.

## 10. Explicit cross-owner dependency exit matrix

| Domain | Owner authority | Contract-only fence |
|---|---|---|
| Provider streams/fallback/structured output | PTC #8 and SO-C0 #385 | capability negotiation; no adapter/provider modifications |
| AE durability and APR-X1 sequencing | AE-R14 #359, APR-X1 #377 | CAS/expected revision; no new durable commit loop |
| UBQ usage, retries and TBO task horizon | UBQ #141, TBO #381 | no per-chunk fresh budget or duplicate terminal debit |
| Client duplex transport and Live UI | CL-UI #242 and client transport | no new WS protocol or client audio driver |
| Agent identity, Memory | AIC; CTX #15 | runtime session not Agent identity/Memory owner |
| Agent capability/runtime/sandbox | AOS/DCS #156; Tools/SBX #164/#371 | no new Tool visibility or privileged action |
| FAST_CONTROL | APR-FC1 #382 | realtime stream != deadline-sensitive control policy |

A future executable RT1 stage MUST obtain independent bilateral disposition from all materially touched owners and a fresh exact-main production PRE-CLAIM. THIS DOCUMENT alone grants no production scope, no stream method, no provider dispatch, no UI/transport alteration, and no integration authority.

## 11. Evidence and exit gates

The exact claimed architecture test may read existing source/contracts but **must not modify them**. Focused evidence should check:
- the current Agent `InferencePort.complete()` remains non-streaming and the existing WS capability transport remains distinct;
- distinct owner/session/generation/revision/request identities and stale-generation rejection;
- ordered provisional chunks, terminal identity, capability matrix and post-visible-fallback fence;
- duplex barge-in/cancellation, bounded backpressure, reconnect cleanup and safe recovery;
- AE expected-revision/CAS, APR-X1 deterministic observation/adoption and SO-C0 atomic final validation;
- UBQ/TBO unchanged deadlines, quota usage and cross-owner production boundaries.

```text
focused architecture evidence = REQUIRED
exact-head Linux and Windows Architecture GREEN = REQUIRED
independent contract FINAL PASS = REQUIRED
independent FINAL = PENDING
READY / FROZEN = NO
merge authority = NONE
production PRE-CLAIM = HOLD / NOT RELEASED
```

The stage closes only after a separate independent FINAL, guarded Integration Wave authorization and **post-merge exact-new-main** Linux/Windows Architecture GREEN. These exit gates do not certify a realtime runtime implementation.
