# TV1-T9-G — Compatibility / Collision / Lifecycle Gate

**Repository:** `boxs-51/assistant`  
**Issue:** #12  
**Base:** `b55989b1e2b7c2afcf9df09c18709ed5e4eb2105`  
**Stage:** TV1-T9-G  
**Scope:** proof/evidence only — no production changes

## 1. Gate decision

The T9-G coverage audit found no missing production regression that justified
adding another duplicate test.

The required compatibility/lifecycle invariants are already exercised by the
repository's existing real loader, realtime, reconciliation, Agent, and MCP
test suites.

T9-G therefore freezes the exact regression matrix below and uses exact-head
CI as the execution authority.

## 2. V1 compatibility

CLIENT physical/V1 loader compatibility remains intentionally supported:

- `cl/tests/test_t8_metadata_v2_client_loader.py::test_v1_top_level_tool_loading_remains_compatible`
- `cl/tests/test_t8_metadata_v2_client_loader.py::test_v1_registration_payload_remains_compatible`
- package-root isolation and relative-import regressions in the same suite.

These tests prove that Metadata V2 logical migration did not silently remove
the compatibility path for still-V1 local packages.

## 3. V2 collision / tombstone / wildcard fences

CLIENT collision and monotonic tombstone behavior:

- `test_v1_v2_identity_collision_is_fail_closed_regardless_of_order`
- `test_multi_export_v1_v2_collision_disables_same_package_in_both_orders`
- `test_multi_export_v2_v2_collision_is_package_fail_closed_in_both_orders`
- `test_transitive_v2_collision_components_cannot_resurrect_ids`
- `test_v2_wildcard_selection_is_rejected`

Authority:
`cl/tests/test_t8_metadata_v2_client_loader.py`

SERVER/catalog identity and lifecycle collision fences:

- `test_divergent_existing_definition_is_rejected_before_mutation`
- `test_divergent_batch_has_zero_mutation_for_earlier_valid_entry`
- `test_duplicate_logical_ids_in_one_batch_are_rejected_before_mutation`
- `test_divergent_implementation_id_reuse_is_rejected`
- `test_removed_implementation_id_cannot_be_revived`

Authority:
`se/tests/architecture/test_t8_metadata_v2_convergence.py`

## 4. Registration batch atomicity

Two layers remain required:

1. architecture-level zero-mutation preflight:
   - `test_divergent_batch_has_zero_mutation_for_earlier_valid_entry`
2. real WebSocket boundary rollback:
   - `se/tests/e2e/test_t8_g_metadata_v2_convergence.py::test_g5_realtime_divergent_batch_rolls_back_atomically`

The real E2E test proves that an earlier valid entry is not committed when a
later same-batch definition diverges.

## 5. Snapshot / send / ACK and reconnect lifecycle

CLIENT registration sequencing:

- `cl/tests/test_t8_metadata_v2_client_loader.py::test_v2_register_preserves_snapshot_send_and_ack_flow`

It freezes:

```text
snapshot -> capability.register send -> wait -> capability.registered ACK
```

Real reconnect lifecycle:

- `se/tests/e2e/test_t8_g_metadata_v2_convergence.py::test_g6_v2_reconnect_reuses_definition_and_rebinds_implementation`

It proves:

- first connection receives an enabled CLIENT implementation;
- disconnect marks the old implementation REMOVED;
- reconnect uses a new connection generation;
- the canonical logical definition is unchanged;
- the new implementation becomes ENABLED;
- `capabilities_registered` becomes true after ACK.

Additional transport lifecycle coverage:

- `se/tests/transport/test_realtime_registration.py::test_websocket_registers_client_capabilities_and_unregisters_on_disconnect`
- `se/tests/architecture/test_phase6_5_client_registration.py`

## 6. R6/R7 identity / fingerprint / reconciliation fences

R6 client reconciliation:

- `cl/tests/test_r6_c_reconciliation_contract.py`
  - running invocation is not executed twice after generation change;
  - lost result send reconciles exact terminal state without re-execution;
  - reconciliation conflict never replays/re-executes.

R7 ResumePlan semantic fences:

- `se/tests/architecture/test_r7_d_resume_plan.py`
  - semantic fingerprint drift rejection;
  - foreign-client rejection;
  - capability/revision/outcome regression fences;
  - stable origin-client authority.

R7 same-invocation continuation:

- `se/tests/architecture/test_r7_e_existing_invocation_continuation.py`
  - invocation reuse with a new attempt;
  - replay-safe idempotent continuation;
  - unsafe non-idempotent replay rejection;
  - stale revision/fingerprint rejection before attempt;
  - durable argument fingerprint corruption rejection;
  - capability idempotency drift rejection.

These gates ensure T9 logical export identity does not alter R6/R7 invocation
or reconciliation authority.

## 7. Agent projection

Authority:
`se/tests/architecture/test_t9_e_projection_cleanup.py`

It proves the real production support-loader path:

```text
register_builtin_support()
  -> LocalSupportLoader.discover()
  -> AgentRegistry lazy loader
  -> AgentRegistry.get()
  -> real AgentDefinition.tools
```

Required projections remain:

- command-reviewer: exactly the frozen 8 File/Glob/Terminal logical IDs;
- web-researcher: exactly `web.search/web.read/web.read_many`;
- coordinator: the two Agent capability IDs;
- no physical roots;
- no implicit Window/Desktop Agent exposure.

## 8. MCP compatibility

Client ownership / real stdio lifecycle:

- `cl/tests/test_mcp_client_ownership.py`
  - dedicated owner loop;
  - real stdio call;
  - cancellation handoff;
  - bounded shutdown;
  - failed-start cleanup.

Agent MCP execution:

- `se/tests/agent/test_mcp_harness.py::test_agent_tool_loop_executes_mcp_capability_through_real_driver`
- `se/tests/agent/test_mcp_harness.py::test_mcp_capability_unavailable_is_reported_without_crashing_agent`

T9 does not modify MCP capability identity or ownership.

## 9. Web CLIENT exclusion

Default CLIENT policy:

- `cl/tests/test_t8_metadata_v2_client_loader.py::test_repository_default_config_uses_explicit_v2_ids_without_web`

Real Web Metadata V2 package boundary:

- `cl/tests/test_t8_metadata_v2_client_loader.py::test_real_web_package_is_discoverable_but_not_selected_by_default`
- `se/tests/e2e/test_t8_g_metadata_v2_convergence.py::test_g1_g2_real_web_is_server_only_and_preserves_physical_toolresult`

The E2E gate verifies that the real Web package is SERVER-only and that no
`web.*` ID appears in the default CLIENT registration payload.

## 10. T9 stage-specific proofs retained

T9-B/C:
- exact CLIENT placement and real top-level V2 loading.

T9-D:
- 24-definition SERVER/CLIENT canonical parity;
- coexistence;
- divergence atomicity;
- routing priority.

T9-E:
- model-facing/public/Agent projection cleanup.

T9-F:
- logical-to-physical execution equivalence;
- direct physical `run(...)` compatibility;
- fake-only GUI side effects.

## 11. Live harness boundary

T9-G does not execute or modify `tools/v1/live/**`.

Real-machine live harness work remains allocated to TV1-T10.

No source-tree live artifact, real desktop/window action, or old live script is
part of this gate.

## 12. Exact-head execution requirement

T9-G closes only after the branch containing this gate matrix passes:

- Architecture Baseline Linux full suite;
- Windows client contracts;
- Issue #12 / PR audit re-check.

T9-H remains a separate final integration stage and is blocked on the final
AE-R10 post-merge re-freeze/canonical main before re-anchor.
