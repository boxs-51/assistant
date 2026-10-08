# APR-CU1 — Computer-Interactive Environment Session Contract Freeze

## 1. Authority and exact claim

Policy #85 v2.5. Canonical APR tracker #278; APR-CU1 stage owner #399. Independent zero-production contract/evidence PRE-CLAIM PASS/RELEASED #6063780497 and owner exact two-path CLAIM #6066501520. Stable development baseline main@48715edd3057302f4f39c7a41f7738616c246dfa. Newer main changes classified NON_MATERIAL only to these two new paths, not executable Computer Use.

Authority is CONTRACT + ARCHITECTURE EVIDENCE / ZERO PRODUCTION. Exact changed paths are 2 NEW / 2:
1. docs/agent_platform/APR_CU1_COMPUTER_INTERACTIVE_ENVIRONMENT_SESSION_CONTRACT_48715EDD.md
2. se/tests/architecture/test_apr_cu1_computer_interactive_environment_session_contract.py

A third path is PROHIBITED. se/src/** delta = ZERO; cl/** delta = ZERO; tools/v1/** delta = ZERO; provider/runtime/transport/API/schema/DB/migration/workflow delta = ZERO. Production PRE-CLAIM = HOLD / NOT RELEASED; production CLAIM = NONE; independent FINAL = PENDING; READY / FROZEN = NO; merge authority = NONE. A separate independent claim and release is mandatory before any implementation, live desktop access or merge.

APR-CU1 freezes how a future Agent COMPUTER_INTERACTIVE profile would conduct an authenticated observe -> reason -> act -> observe loop. It neither creates an environment runtime nor gives ambient host authority.

## 2. Canonical source inventory and non-equivalence

The canonical docs/agent_platform/AGENT_SPECIALIZATION_ROADMAP.md section 9 lists environment identity/session, bounded observation, Desktop/Window/browser Tool ownership, #156 routing/sandbox, privileged approval, stale-action validation and cancellation/recovery. It explicitly requires No ambient host authority.

tools/v1/desktop_tool.py currently owns bounded physical operations such as desktop.screenshot and non-idempotent desktop.mouse_click, desktop.type_text, desktop.press_key, and desktop.hotkey. Its MAX_SCREENSHOT_PNG_BYTES limits output; this is a Tool-level bound, NOT an authenticated Agent environment observation session.

tools/v1/window_tool.py supplies window.list, window.find, window.geometry and window.focus with specific selector/target schemas. An OS handle, title, process ID, focused foreground window or geometry snapshot is NOT a durable environment identity or a user grant. A changed/reused window handle requires target revalidation, never arbitrary first-match selection.

cl/src/core/realtime_client.py GatewayRealtimeClient has authenticated persistent capability WebSocket registration and a single receiver; it is NOT a deployed Agent computer-interactive session, OS foreground gate, browser sandbox or autonomous observation/action orchestrator.

docs/agent_platform/APR_P0_AGENT_DEFINITION_PROFILE_BINDING_CONTRACT_7C4C4D42.md owns conceptual Agent profile/binding design; docs/agent_platform/APR_X1_EXECUTION_LANE_EVENT_SEQUENCER_CONTRACT_C86BCC5E.md freezes sequencer ordering but not durable AE state. Existing individual Tools, transport sockets and profile names MUST NOT be misrepresented as deployed Computer Use.

## 3. Independent identities, placement and generation

The future conceptual contract distinguishes:
- trusted owner_user_id and AIC agent_instance_id from AE execution_id, AE AgentExecution.revision and AE recovery lease owner_instance_id;
- environment_session_id from runtime_session_id, client connection_id and authenticated transport_generation;
- CRT client target placement CLIENT_LOCAL from SBX EPHEMERAL_SANDBOX placement: a sandbox target is never a fallback to the host;
- target_id from application_identity, process identity, window_handle, browser tab and target_epoch; handles may be reused and focus may change;
- observation_id and observation_revision from AE durable execution revision and APR-X1 proposal sequence.

Admission verifies a currently authorized owner/AgentInstance/execution, placement, target and current connection generation. A reconnect MUST NOT mint a new Agent identity, environment-session grant or physical Tool authority. Every observation/action/result is bound to immutable owner, instance, execution, environment_session_id, placement, target_id, target_epoch, connection_id and transport_generation provenance; replay cannot fill missing provenance from an untrusted client payload.

## 4. Conceptual environment-session records

The following are design-only conceptual records, NOT public Python classes, provider DTOs, new network APIs or implemented runtime schemas.

    EnvironmentSession(owner_user_id, agent_instance_id, execution_id,
                       environment_session_id, placement, client_id,
                       connection_id, transport_generation, session_revision,
                       target_id, target_epoch, grant_id, deadline)
    EnvironmentObservation(owner_user_id, agent_instance_id, execution_id,
                           environment_session_id, placement, connection_id,
                           transport_generation, target_id, target_epoch,
                           observation_id, observation_revision, captured_at,
                           source_kind, source_fingerprint,
                           inline_payload_or_immutable_asset_ref, byte_length,
                           content_hash, media_type, expires_at)
    EnvironmentAction(owner_user_id, agent_instance_id, execution_id,
                      environment_session_id, placement, connection_id,
                      transport_generation, target_id, target_epoch,
                      expected_observation_id, expected_observation_revision,
                      grant_id, session_revision,
                      action_id, invocation_id, client_id, principal_id,
                      tool_id, capability_version, request_fingerprint,
                      arguments_ref, approval_id, idempotency_class, deadline)
    EnvironmentResult(owner_user_id, agent_instance_id, execution_id,
                      environment_session_id, placement, connection_id,
                      transport_generation, target_id, target_epoch,
                      grant_id, session_revision,
                      action_id, invocation_id, client_id, principal_id,
                      tool_id, capability_version, request_fingerprint, observed_effect,
                      result_id, result_status, observation_id,
                      uncertain_external_effect)

Every record carries mandatory immutable provenance, and a trusted admission stores or derives an immutable session-to-owner-to-target binding scoped to the active session and bounded replay horizon. The binding, not a caller-provided ID string, is authoritative. **NEW action dispatch, physical Tool effects and authority-bearing adoption** MUST validate the **current generation, target epoch, session/grant lifetime and original owner** before execution or a current-state transition. An expired grant, stale generation or changed/closed target NEVER authorizes a new action, replay or current-state adoption.

**Previously dispatched, immutable EnvironmentResult evidence follows a distinct NON-AUTHORIZING RECONCILIATION PATH.** An authenticated canonical AE-R6/Tool outcome for an originally admitted invocation MAY be received, verified and recorded as a late reconciliation observation even after reconnect, grant/session expiration, AE terminal transition or the Tool's own modification/closure of the target. It MUST retain the **original** owner_user_id/agent_instance_id/execution_id, environment_session_id, target_id/target_epoch, connection_id/transport_generation, action_id ↔ invocation_id, client_id/principal_id, tool_id, capability_version and request_fingerprint provenance. Validate the original immutable authority binding, including the original grant_id and admitted session_revision, and current principal access to that invocation's evidence against the canonical ledger; DO NOT demand that the historical generation, target epoch or grant is still current. The historical late result MUST retain the expired original grant_id/session_revision even when a distinct replacement grant_id is presently active; the replacement grant cannot retroactively validate its old physical execution or make its result authority-bearing. EnvironmentResult.tool_id MUST preserve the originally admitted EnvironmentAction.tool_id, and the canonical invocation record MUST corroborate the tool_id, capability_version and request_fingerprint. Reject a missing, substituted or inconsistent tool_id; never infer it from the currently selected Tool after reconnect or expiration. Reject forged, foreign-owner, mismatched or untraceable outcomes; recording verified outcome evidence is NOT a new Tool permission, replay, current observation for a new action, AE terminal rewrite, or CTX Memory promotion. Any later authority-bearing AE durable state adoption still requires AE expected-revision/CAS and active eligibility; after terminal state, keep evidence as reconciliation-only without reopening execution.

For observations, inline_payload_or_immutable_asset_ref MUST contain exactly one bounded inline payload or a scoped immutable CAS/F7-T asset reference. Source type, hash, length, media type, capture time, expiry and access-control scope are verified before the model sees it. A screenshot byte payload, DOM snapshot, window list or OCR text is untrusted observation DATA, not instructions or authorization. A missing, stale, oversized, foreign-owner, mutable or mismatched payload MUST fail closed. Asset references do not automatically become CTX Memory and do not grant physical host access.

For actions, arguments_ref MUST be a validated bounded inline argument value or immutable request-scoped argument reference bound to tool_id, target_epoch, action_id and current grant. No untyped out-of-band action payload. A Tool output or a new observation cannot retroactively authorize its preceding action.

Every EnvironmentAction MUST snapshot immutable grant_id and admitted session_revision from the exact trusted EnvironmentSession grant that originally admitted this invocation, NOT a free-form client-supplied ID or whichever grant is currently active. Every EnvironmentResult MUST carry that same original grant_id and admitted session_revision as immutable historical provenance, without silently rebinding to a renewed grant or a later environment revision. Action/result grant provenance must correlate to the canonical owner, execution, environment_session_id, target_id/target_epoch, action_id and invocation_id. A replacement grant for the same target/session is a DISTINCT authority with a DISTINCT grant_id, never an implicit renewal of old queued-action permission.

The conceptual action_id is an Agent/UI session correlation identity, NOT a second Tool dispatch/recovery ledger. At trusted admission bind each action_id **one-to-one and immutably** to the AE-R6 canonical invocation_id and stable (client_id, principal_id, invocation_id) ledger key, along with exact tool_id, capability_version and immutable request_fingerprint of the target, arguments and side-effect semantics. The canonical `cl/src/core/client_invocation_ledger.py` rejects an invocation_id already bound to a different capability_version or request_fingerprint. A missing mapping, changed client/principal, same action_id with different invocation_id, or same invocation_id with different capability_version/request_fingerprint MUST fail closed before physical dispatch. Reconnect, retry, worker restart and lost acknowledgements MUST query/reconcile the canonical invocation outcome and ambiguous external effect under AE-R6 and Tools; NEVER mint a second invocation merely because an Agent action_id was replayed. No new APR action ledger or production invocation authority is created.

## 5. Freshness, target selection and state-sensitive dispatch

No potentially state-changing action may be dispatched based on a stale screenshot, old focus snapshot, unknown target, ambiguous window selector or mismatched environment identity. Preconditions:
1. check owner_user_id, agent_instance_id, execution_id and AE active eligibility;
2. verify environment_session_id, CRT/SBX placement, client_id and current authenticated connection_id/transport_generation;
3. resolve exactly one permitted target_id and expected target_epoch with current application/window/browser fingerprint and foreground/focus requirement;
4. match expected_observation_id + expected_observation_revision to current target state with a bounded freshness window;
5. enforce DCS/CRT/SBX/Tools capability admission, Tool side-effect classification and required HITL approval_id prior to dispatch;
6. record a unique action_id, bounded deadline, cancellation and idempotency/reconciliation policy.

Pre-dispatch admission is necessary but NOT sufficient for queued/remote actions. At the **physical CLIENT_LOCAL/Tool execution boundary, immediately before the first external side effect**, the authoritative client/Tool executor MUST atomically revalidate against its live environment: authenticated owner/AgentInstance/AE eligibility, exact environment_session_id and current connection generation, CRT/SBX placement, target_id/target_epoch and current window/browser/DOM/geometry state fingerprint, foreground/focus target, expected_observation_id + expected_observation_revision or fresh equivalent trusted state version, the SAME immutable admitting grant_id and admitted session_revision from the original EnvironmentAction against currently active unrevoked grant/expiry/policy, plus per-invocation HITL approval_id. The physical Tool executor MUST check exact grant identity/expected admitted session revision, not merely existence of ANY active grant for the same target. When a queued action's admitting grant expires/is revoked and a DIFFERENT grant_id becomes active, reject the queued action before any physical side effect; never elevate stale admission using replacement permission. Validation MUST be serialized with initiation of the physical side effect against concurrent focus/target/approval changes: validation only upstream in an Agent queue or before dispatch is a TOCTOU bug. On changed or stale target state, focus or approval after dispatch but before execution, reject the invocation before click, type_text, drag, hotkey or browser submit. If the physical Tool/OS interface cannot atomically fence the state and effect, **FAIL CLOSED / REQUIRE NEW OBSERVATION** instead of guessing a foreground target. This conceptual boundary neither implements OS locking nor grants new runtime, client or Tool authority.

Fail closed on ambiguous target, stale observation, reused window handle, expired grant, changed foreground focus, lost auth, unsupported browser target, cross-owner target or sandbox-to-host fallback. Never guess first window, click a nearby coordinate on changed geometry, or assume a previously focused app remains safe. A new observation can be requested under existing authority; it does not authorize a side effect by itself.

## 6. Approval, untrusted UI and safety

Client-local foreground consent, microphone/camera/screen sharing permissions, confirmation UX and explicit HIGH-risk Tool HITL are governed by client/Tool/safety owners. Approval must bind owner, execution, environment session, target epoch, exact action/capability, risk class and expiry. Reconnect, retry or minor coordinate drift MUST NOT silently reuse an unrelated approval. Approval obtained for a screenshot is not approval for a click, file write, terminal operation, payment or browser submission.

Web content, DOM text, screenshot text, app titles, tool result bodies and clipboard data are untrusted. The Agent must treat UI prompt injection as data, never policy authority, Tool selection, instruction priority or credentials. A webpage asking to disable security, reveal secrets, change Tools, bypass consent or switch physical target cannot raise privileges.

P0 security revoke/emergency stop takes precedence over P1 AE terminal/HITL denial, P2 owner interruption, P3 deadline/session expiry and P4 normal observation/action progress. Cancel means stop new Tool side effects; pending irreversible external effects need Tool/AE-R6 outcome reconciliation, not a fictitious rollback or just an overlay animation.

## 7. Bounded observation and state reconciliation

Each observation has a capture ID, content hash, capture timestamp, target fingerprint, immutable target epoch, source_kind, media_type, bounded bytes and TTL. A screenshot from an expired connection, a stale DOM snapshot, a revoked CAS reference or a reused window ID is not admissible for a later action. Observation cache, screenshot history, replay and action result buffers have explicit limits; unbounded queues and unlimited replay are prohibited. Missing gaps or uncertain capture order fail closed.

A lost response from an already dispatched NON_IDEMPOTENT Tool is an ambiguous external effect. Do not automatically replay mouse_click, drag, type_text, hotkey, window.focus or browser submission after timeout, reconnect or worker restart. Re-observe the same authorized target, resolve action_id to its immutable AE-R6 invocation_id and canonical (client_id, principal_id, invocation_id, capability_version, request_fingerprint) ledger evidence, reconcile the outcome, and require fresh approval if needed. Conflicting semantic replay is forbidden even when action_id looks familiar. Exactly-once Tool execution cannot be inferred from a network ack. Idempotency classes originate with Tools, not the Agent prompt.

A **late authenticated canonical invocation outcome MUST NOT be discarded solely because** transport_generation, target_epoch, environment session or original grant is now expired/stale, or because the original action itself changed/closed its target. Verify original owner/execution binding and immutable action_id ↔ AE-R6 invocation_id + (client_id, principal_id, capability_version, request_fingerprint) against the canonical Tool ledger. Preserve the verified success/failure/uncertain external effect as an **immutable, non-authorizing reconciliation observation** through the owning AE/Tool durability boundary; do not treat it as a fresh target observation, valid HITL approval or permission to issue/replay any further action. If the target is no longer available, do not insist on re-observing it as a condition for recording the late result; retain uncertainty under canonical AE-R6 recovery rules. Late Tool outcome reconciliation MUST cross-check original EnvironmentResult.tool_id with EnvironmentAction.tool_id and the canonical invocation's immutable Tool identity before recording verified effect evidence; a mismatch is refused without creating new execution authority or another physical effect. Current AE terminal state cannot be rewritten or reopened; only a separately eligible AE expected-revision/CAS transition can adopt an effect into live state. This separates **outcome evidence acceptance** from **authority-bearing state adoption**.

Only AE expected-revision/CAS authorizes a durable transcript/checkpoint/terminal state. APR-X1 DecisionCommit is an adoption request, not persistence authority; its sequencer-local order is NOT AgentExecution.revision. Late Tool results, stale screenshots and resumed sessions cannot overwrite AE terminal state, create CTX Memory or mint AgentInstance authority. A session detach does not itself finish the AE execution; recovery decisions remain AE-owned.

## 8. Lifecycle, quota, placement and delegation

Open/attach/detach/reconnect/expire/close transitions are tied to current owner, Agent instance, AE revision, environment session, target epoch and transport generation. A client reconnect needs fresh authentication; session reattachment is bounded by active grant/expiry, target existence and AE eligibility. Old generations and old observation/action approvals fail closed for any NEW execution, active-session reattachment or authority-bearing state adoption. Historical authenticated Tool outcomes still enter the non-authorizing reconciliation path in §4 and §7 even after expired grant, changed target epoch or closed session; never reacquire Tool permission from the old result. Disconnect and local UI closure do not imply successful cancellation of an external effect. Cleanup releases transient screenshots, bounded buffers, capture workers, focus leases and in-flight Tool handles through existing owners, without deleting CAS assets or CTX Memory.

UBQ performs admission, usage and retry accounting; TBO owns deadline/horizon and task-eligibility gates. Reconnection, retry, observation refresh, large screenshots and reissued Tool requests MUST NOT refill budgets, extend deadlines or double count a known consumed operation. Existing CAS F7-T controls content references and lifecycle; no new blob persistence, schema or migration authority.

CRT-1 #161 owns CLIENT_LOCAL routing; SBX #162/#163/#164 owns EPHEMERAL_SANDBOX isolation and cannot silently execute on a host. Tools V1 #371 retains desktop/window implementation and physical effect ownership. GAC #221 retains action-admission controls; DCS and ToolCatalog retain capability eligibility. CL-UI #242 owns consent/UI and input accessibility. AIC owns AgentInstance lifecycle, CTX #15 owns Memory, AE #359 owns durable task/recovery/approval state, APR-P0/APR-X1/RT1 retain distinct specialized contract ownership.

## 9. Negative acceptance vectors and refusal semantics

The future executable admission/evidence owner MUST preserve at least these RED-FIRST test cases:
- same target_id but different owner or agent_instance_id;
- reused window_handle or different target_epoch after an observation;
- stale transport_generation after reconnect with apparently valid action_id;
- screenshot or DOM reference whose content hash, TTL, owner, target or grant changed;
- foreground focus lost after approval but before dispatch, OR after dispatch while queued but before physical effect-boundary validation;
- target_epoch or DOM/window/geometry fingerprint changes after admission but before actual click/type/browser effect, requiring an atomic execution-boundary fence or fail closed;
- reused action_id mapped to a different invocation_id, or invocation_id replayed with changed capability_version/request_fingerprint, without a second NON_IDEMPOTENT effect;
- high-risk Tool action lacking approval for exact invocation or with revoked grant;
- queued action originally admitted by grant_id G1, then G1 revoked/expired while new G2 becomes active for SAME owner/session/target: refuse physical effect despite active G2; no silent rebinding of grant_id or admitted session_revision;
- authenticated late result for original admitted G1 after expiry and G2 activation: retain original G1 grant_id/session_revision as non-authorizing reconciliation evidence only, NEVER authorize G1 effect replay or new Tool dispatch with G2;
- ambiguous selector or multiple matching windows, with no arbitrary first-match;
- CLIENT_LOCAL target silently rerouted to EPHEMERAL_SANDBOX or back to host;
- prompt injection inside screenshot/DOM text attempting a Tool or policy grant;
- lost ToolResult after a possible side effect, with unsafe repeated click/type;
- late authenticated NON_IDEMPOTENT result arriving after reconnect, expired session/grant or action-closed target: preserve immutable canonical invocation outcome as non-authorizing reconciliation evidence, without another click/type, current grant reuse, new action admission or AE terminal state regression;
- forged or mismatched late result with wrong original owner, invocation_id, target provenance or request_fingerprint: reject without granting execution;
- late Tool result missing tool_id or containing a tool_id different from the original Action or canonical invocation, even with matching action_id/invocation_id: reject forged Tool provenance and never replay a NON_IDEMPOTENT effect;
- cancellation or AE terminal concurrent with a late observation/action result;
- reconnect retry that resets TBO/UBQ deadline or reuses another generation;
- unavailable environment, missing session binding or unsupported capture type.

Fail closed with a typed, observable refusal; no untrusted payload can turn rejection into authorization. These are contract-level required future scenarios, NOT claims that this architecture evidence executes a browser or real OS focus isolation.

## 10. Evidence boundaries and exit gates

Architecture regressions may read existing docs, AgentExecution schema, Tools V1 descriptor/source and CL capability transport to prove current boundaries; they MAY NOT claim real computer-use E2E, OS isolation, sandbox escape resistance, browser event fidelity, UI/HITL approval or physical rollback. The current repository has individual Desktop/Window Tools and capability socket, not this integrated Agent environment session.

Before production: fresh independent PRE-CLAIM and owner CLAIM for every exact shared runtime/client/Tool/schema/asset/AE path, with bilateral CRT/SBX/Tools/AIC/CTX/CAS/UBQ/TBO/CL-UI/GAC approvals; real target/focus/reconnect/security/HITL and SQLite/PostgreSQL AE evidence. APR-CU1 contract FINAL requires exact-head Linux+Windows Architecture GREEN plus independent auditor PASS. READY/FROZEN only after separate Wave guard, and explicit user authorization governs merging that Wave. Neither this contract nor green architecture tests open production authority.

Current state remains independent contract/evidence PRE-CLAIM PASS/RELEASED; owner two-file CLAIM ACTIVE; independent FINAL PENDING; no merge authority.
