# AE-R12-H0 — Full Fault / Exit Matrix Freeze

Primary authority: Issue #107
Policy: Issue #85 v2.5
Independent release: comments #5979088017 and #5979528597
Release baseline: `main@522b543e66f309085729e57a2248258b7c4f2599`
Development baseline: `main@854ae02d3f896c5f40a51bd2734e2eb46992493e`
Current integration re-anchor baseline: `main@d967165a5c8cda6ffaf9a172f14b9dc0696bf140`
Stage: AE-R12-H0
Class: CONTRACT / INTEGRATION + ARCHITECTURE EVIDENCE ONLY
Status: ACTIVE REFRESH / H0-20 CLOSED BY CANONICAL H1-A / FINAL PENDING

## 1. Authority and purpose

H0 composes the already-landed R6/R7/R10/R11/R12 fault evidence into the final
AE-R12 exit gate. It adds no production recovery mechanism and does not repair
a matrix row by inference.

~~~text
production/runtime delta = ZERO
schema/migration delta = ZERO
provider/capability runtime delta = ZERO
TaskBudget/ResumeClaim/lease production delta = ZERO
R12-H production repair authority = NONE
R12-H final closure authority = PENDING H0 evidence + FINAL audit
merge authority = NONE
~~~

If any executable row fails, that row becomes GAP / HOLD. A production repair
requires a separate bounded PRE-CLAIM naming the invariant, production paths
and focused evidence.

## 2. Full fault / exit matrix

| ID | Required exit row | Disposition | Canonical executable evidence |
| --- | --- | --- | --- |
| H0-01 | Worker/process loss during provider call | PASS / INHERITED | `test_f3b_external_caller_cancellation_preserves_half_open_no_failure` starts a blocking physical provider call and proves in-flight owner cancellation propagates without retry/fallback or breaker-failure accounting; `test_f3b_reused_handoff_never_mints_or_sends_fresh_inference` preserves `RECOVERY_INFERENCE_CUT_UNPROVEN` and zero fresh inference send for an ambiguous recovered inference cut. |
| H0-02 | Worker/process loss during remote invocation | PASS | `test_r12_f3a_lease_loss_after_dispatch_preserves_r6_but_blocks_agent_projection`. |
| H0-03 | Restart with durable WAITING execution | PASS | `test_r12_g_restart_fresh_service_consumes_durable_created_claim`. |
| H0-04 | Orphan durable RUNNING through stale observation, recovery ownership and activation | PASS | D1 zero-mutation observation + `test_r12_e_iteration_zero_recovery_checkpoint_can_resume` + F2 atomic activation. |
| H0-05 | Lease acquire / renew / release / exact-expiry | PASS | `test_r12_c_acquire_renew_release_reacquire_preserves_semantics`. |
| H0-06 | Clock and expiry fail closed | PASS | `test_r12_c_renew_and_fence_fail_closed_for_stale_or_expired_authority`. |
| H0-07 | Competing recovery workers have one durable winner | PASS | `test_r12_g_multi_worker_task_recovery_has_one_winner_and_one_budget_slot`. |
| H0-08 | Stale old owner after authority loss performs zero new dispatch | PASS | `test_r12_f3a_initial_fence_loss_causes_zero_external_dispatch` plus F3-B provider guard evidence. |
| H0-09 | Recovery versus CLIENT_RECONNECT | PASS | G0 stale reconnect/recovery reciprocal rejection tests. |
| H0-10 | Recovery versus terminalization | PASS | `test_r12_e_terminalization_before_final_recovery_cas_wins`. |
| H0-11 | RETRY/FORK/Task terminal interaction without new R8/R9 authority | PASS / INHERITED | `test_r9_h_retry_vs_task_cancel_is_fail_closed` and durable branch-resolution race matrix. |
| H0-12 | Pending IN_FLIGHT reconciliation through R6 | PASS / INHERITED | `test_r6_e5_running_after_client_restart_reconciles_unknown_and_never_reexecutes`. |
| H0-13 | OUTCOME_UNKNOWN never blind-replays | PASS / INHERITED | R6 E5/E6 controlled replay and same-invocation authority. |
| H0-14 | Provider visible output is no-replay/no-fallback | PASS / INHERITED | `test_r10_h_public_stream_visible_failure_sequence_is_terminal_no_replay`. |
| H0-15 | TaskBudget slot/incarnation/accounting survives recovery | PASS | G0 one-slot race plus R12-E release-once and F2 capacity reacquire-once tests. |
| H0-16 | Terminal execution cannot resurrect | PASS | `test_r12_g_terminal_execution_rejects_stale_recovery_activation`. |
| H0-17 | No duplicate AgentRuntime activation for one durable cut | PASS | G0 one-winner matrix and F2 consumed replay without lease remint. |
| H0-18 | R11 REF_BACKED checkpoint restart reconstruction | PASS / INHERITED | `test_r11_g1_many_checkpoint_ref_backed_growth_is_bounded` and R11-H final matrix. |
| H0-19 | Linux + Windows full Architecture | PASS / PRIOR EXACT-HEAD CI / REFRESH REQUIRED | Architecture #2046 / run `37205039948` is GREEN/GREEN on prior H0 evidence HEAD `dfc0e1d84f7b607d1d9471137bc00fb4e0b6ef5e`; this current-main H0 refresh requires fresh exact-head Linux + Windows CI before FINAL. |
| H0-20 | Server crash cannot leave zombie RUNNING indefinitely | PASS / COMPOSED | Canonical H1-A wires `H1ARecoveryControlPlane` into application lifespan, runs an immediate bounded startup sweep before traffic, starts one periodic worker, and quiesces/drains it before dependency teardown. Executable H1-A evidence proves stale RUNNING evacuation to WAITING(RECOVERY) or FAILED, one-winner fencing, and fail-closed health. |

No row is silently omitted. H0-20 is now PASS / COMPOSED because canonical H1-A
landed the missing startup/periodic lifecycle trigger and stale-RUNNING evacuation
control plane. H0-19 records the prior H0 exact-head GREEN/GREEN result; this
current-main evidence refresh must obtain fresh exact-head Linux + Windows CI
before independent FINAL.

## 3. Executable binding rules

The H0 integration evidence reads the canonical landed regression sources and
requires the exact test functions named above. This keeps inherited proof
executable under the full suite without copying or weakening the original
fixtures.

The zombie-exit invariant requires every link below plus an eventual production trigger. Canonical H1-A now closes the previously missing lifecycle/startup/background link:

1. stale owned RUNNING rows are observable with a fixed UTC cutoff and without mutation;
2. recovery ownership advances durable authority using the canonical recovery cut;
3. activation consumes the durable ResumeClaim and acquires one fresh lease;
4. restart rebuilds authority from durable state using fresh persistence objects;
5. competing workers converge to one activation and one TaskBudget active slot;
6. stale or terminal authority fails closed;
7. production lifecycle/startup/background wiring invokes stale observation and evacuation after owner-process loss: `se/src/main.py` constructs `H1ARecoveryControlPlane`, awaits `start()` before serving traffic, and awaits `quiesce()` before dependency teardown; `test_h1a_start_is_immediate_periodic_and_quiesce_drains_worker` proves immediate/periodic/drain lifecycle.

## 4. Authority fences

H0 does not authorize:

- edits below `se/src/**` or `cl/src/**`;
- lease, scanner, recovery, ResumeClaim or TaskBudget production changes;
- a second recovery claim, winner token, lock or WAITING-to-RUNNING seam;
- retry/replay permission from lease expiry or process loss;
- replay outside R6 IN_FLIGHT/OUTCOME_UNKNOWN authority;
- provider retry/fallback/deadline/visible-output changes owned by R10/UBQ;
- MANUAL resume API or new transport authority;
- checkpoint representation or retention changes owned by R11;
- CAS lifecycle/generated-media, CTX Memory/promotion, UBQ accounting, or #156 routing/sandbox authority.

## 5. Current-main and cross-track disposition

Current canonical main is `d967165a5c8cda6ffaf9a172f14b9dc0696bf140`.
The movement from prior H0 main `c6b31234...` contains CAS-F7-T, UBQ-5D,
CTX-B6 and the separately released AE-R12-H1-A slice. CAS/UBQ/CTX remain
NON_MATERIAL to H0's zero-production matrix authority. H1-A is MATERIAL only
to H0-20 because it supplies the previously missing stale-RUNNING lifecycle
and startup evacuation seam; Architecture #2092 / run `37224467909` is GREEN/GREEN.

- UBQ-5D is LANDED/CANONICAL/HEALTHY; UBQ-5E is off-main and owns timeout schema compatibility, not durable recovery.
- CAS-F7-T persistence infrastructure is LANDED/CANONICAL/HEALTHY and transfers no Agent recovery authority.
- CTX-B6 activation is LANDED/CANONICAL/HEALTHY and transfers no Agent recovery authority.
- AE-R12-H1-A is LANDED/CANONICAL/HEALTHY and is the exact material dependency closing H0-20.
- #156 remains separate capability/routing/sandbox authority and transfers no H0 authority.

Any later movement into Agent persistence, recovery, final-send, external-outcome
truth, TaskBudget, lease or checkpoint reconstruction is MATERIAL and requires
a fresh H0 dependency audit.

## 6. Completion gate

H0 can reach FINAL only when:

1. changed files remain exactly the three released NEW docs/tests paths;
2. every H0-01 through H0-20 row is still explicit;
3. H0-19 is updated from PENDING to PASS with exact-head Linux + Windows evidence;
4. the evidence-only replacement/re-anchor HEAD also has fresh Linux + Windows GREEN/GREEN;
5. no row is GAP;
6. independent audit finds no blocking P0/P1/P2;
7. review threads are resolved and integration governance is satisfied.

Current disposition: H0-20 no longer violates gate 5 because the separately authorized H1-A production slice is canonical/healthy. AE-R12-H0 remains FINAL-PENDING only for this replacement head's fresh exact-head Linux + Windows Architecture, fresh current-main/dependency refresh, and independent FINAL. H0 itself still grants no production authority.
