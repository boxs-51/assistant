# APR-P0 — AgentDefinitionV2 / AgentProfile / AgentExecutionBinding contract freeze

Canonical policy: Issue #85 v2.5  
Canonical tracker: Issue #278  
Stage workspace: Issue #374  
Development baseline: `main@7c4c4d4251336c00f1358e7570e36b5b08ba8706`  
Baseline Architecture: `#2341 / 37660776240 = GREEN/GREEN`

## 1. Claim and authority

```text
stage = APR-P0
name = AgentDefinitionV2 / AgentProfile / AgentExecutionBinding contract
class = CONTRACT + ARCHITECTURE EVIDENCE / ZERO PRODUCTION
contract CLAIM = ACTIVE
production PRE-CLAIM = CLOSED / NONE
production CLAIM = NONE
schema/migration authority = NONE
runtime/API/client authority = NONE
AgentInstance persistence authority = NONE
APR-X1 authority = NONE
merge authority = NONE
exact changed paths = 2 NEW / 2
third path = PROHIBITED
se/src/** delta = ZERO
cl/** delta = ZERO
schema/migration delta = ZERO
runtime/API/router/registry/persistence delta = ZERO
```

Exact claimed paths:

1. `docs/agent_platform/APR_P0_AGENT_DEFINITION_PROFILE_BINDING_CONTRACT_7C4C4D42.md`
2. `se/tests/architecture/test_apr_p0_agent_definition_profile_binding_contract.py`

No third path is authorized by APR-P0 contract CLAIM.

APR-P0 freezes contracts only. It does not implement a production AgentDefinitionV2,
AgentProfile, AgentExecutionBinding, AgentInstance, runtime profile dispatcher, execution
lane, specialized runtime, persistence model, API shape, client runtime, or migration.

## 2. Preconditions and canonical dependencies

APR-P0 consumes these already-canonical contracts without taking their authority:

- APR-C0-A / Issue #279 — Agent identity/profile/runtime taxonomy;
- APR-C0-B / Issue #358 — APR namespace registration;
- AIC-0-R0 / Issue #365 — stable `(owner_user_id, agent_instance_id)` identity;
- AOS-1 / Issue #158 — Agent selection/default-Agent compatibility;
- SKV2-P0 / Issue #276 — trusted Skill metadata and ActiveSkillSet;
- DCS-1 / Issue #159 — CapabilityWorkingSet and selected-tool enforcement;
- DCS-2 / Issue #160 — grouping/ranking/lazy expansion/schema-token estimation;
- CRT-1 / Issue #161 — CapabilityInvocationTarget and target-aware routing;
- SBX-1 / Issue #162 — EPHEMERAL_SANDBOX boundary.

SBX-2 and later sandbox/tool migration stages are not prerequisites for this
zero-production contract freeze.

## 3. Identity layers remain distinct

APR-P0 preserves four distinct identities:

```text
AgentDefinitionV2 / AgentTemplate
  reusable versioned blueprint

AgentInstance
  stable user-owned identity
  canonical identity = (owner_user_id, agent_instance_id)
  AIC-owned

AgentExecution
  one durable execution
  execution_id and lifecycle = AE-owned

RuntimeSession / EnvironmentSession
  execution-attached runtime/environment session
  not Agent identity
```

Hard invariants:

```text
AgentDefinition.name != agent_instance_id
AgentExecution.agent_id != agent_instance_id
AgentExecution.owner_instance_id != agent_instance_id
runtime_session_id != agent_instance_id
```

`AgentExecution.owner_instance_id` remains AE lease/recovery worker ownership and
MUST NOT be reinterpreted as Agent-instance identity.

APR MUST NOT mint, persist, register, suspend, delete, reassign, or otherwise own
`agent_instance_id`. Those operations remain AIC/Agent-registration authority.

## 4. AgentDefinitionV2 contract

`AgentDefinitionV2` is a reusable, versioned blueprint. It is not a user-owned
Agent instance and is not a Memory owner.

Conceptual shape:

```text
AgentDefinitionV2
  definition_id
  definition_version

  name
  display_metadata?
  goal
  instruction_or_persona

  model_policy_or_preferences
  maximum_capability_envelope_or_policy_ref
  skill_policy_or_envelope_ref

  supported_runtime_profiles
  default_runtime_profile_or_selection_preference

  delegation_policy_or_defaults
  memory_defaults_or_preferences
```

The exact field names, wire representation, persistence model, registration API and
migration strategy remain future production authority.

### 4.1 Compatibility mapping from current AgentDefinition

Current `AgentDefinition` remains the compatibility blueprint during migration.

Its fields are classified as follows:

- `name` remains definition/name compatibility. It MUST NOT become
  `agent_instance_id`, owner identity, execution identity, or an implicit
  `definition_id`.
- `goal` and `instruction` remain blueprint behavior/configuration.
- `tools` is a maximum eligible capability envelope/policy input. It is not the
  per-turn model-visible Tool set.
- `skills` is compatibility input only and cannot bypass trusted Skill V2
  authorization, version identity, or ActiveSkillSet.
- `workflow_definition` does not grant AAT scheduling, event-subscription, or
  automation authority.
- `memory_config` is a blueprint preference/default. It is not CTX durable Memory,
  Agent-private Memory ownership, or `agent_instance_id`.
- APR-P0 contract work MUST NOT silently derive or mint `definition_id` or
  `definition_version` from `name`.

Canonical capability invariant remains:

```text
AgentDefinition.tools != InferenceRequest.tools
```

DCS remains the sole owner of per-iteration model-visible capability selection.

## 5. AgentProfile contract

`AgentProfile` is reusable/versioned specialization configuration. It is not
Agent identity, not execution identity, and not an authorization grant.

Conceptual shape:

```text
AgentProfile
  profile_id
  profile_version

  runtime_profile
  model_preference_or_policy
  capability_ceiling_or_ref
  skill_preference_or_ref
  interaction_modes
  delegation_policy
  memory_preferences_or_defaults
  specialized_runtime_or_environment_requirements
```

### 5.1 Canonical runtime-profile vocabulary

APR-C0 runtime-profile names remain:

```text
STANDARD
REALTIME
FAST_CONTROL
COMPUTER_INTERACTIVE
BACKGROUND
MEDIA_ORCHESTRATED
```

A profile declaration says what execution semantics/configuration are requested or
supported. It does not prove that a specialized runtime is implemented, admitted,
authorized, available, or selected.

### 5.2 Profile is a policy ceiling, not a grant

An AgentProfile MUST NOT widen its parent Agent definition envelope.

Profile data cannot mint or widen:

- Tool/capability authority;
- Skill authorization or activation;
- CTX Memory or personalization access;
- CAS asset access or grants;
- AAT schedules/events/automation;
- UBQ quota or TBO Task eligibility;
- provider/model credentials;
- CLIENT_LOCAL affinity or client ownership;
- sandbox/file/process/network authority;
- environment/desktop/browser authority.

The effective capability set remains bounded by the owning authorities. In
particular:

```text
(task-derived candidates ∪ advisory Skill hints)
  ∩ Agent definition/profile maximum envelope
  ∩ authorization
  ∩ routability
  ∩ DCS policy
  = CapabilityWorkingSet
```

AgentProfile cannot write directly to CapabilityWorkingSet.

Unsupported or unavailable runtime-profile selection must fail closed in future
implementation; APR-P0 does not implement that failure path.

## 6. AgentExecutionBinding contract

`AgentExecutionBinding` describes the immutable/reproducible Agent definition,
profile, Agent-instance reference, and runtime-profile selection associated with one
AE-owned execution.

Conceptual shape:

```text
AgentExecutionBinding
  execution_id
  owner_user_id?            # trusted/server-derived; co-present with agent_instance_id
  agent_instance_id?

  definition_id
  definition_version

  profile_id?
  profile_version?

  active_runtime_profile
  runtime_session_id?
  interaction_channel?
```

This is a contract shape only. No production representation is created here.

### 6.1 AE remains execution authority

- `execution_id`, Task/Branch lineage, state, revision, WAITING, retry, recovery,
  lease, checkpoint and execution persistence remain AE authority.
- APR-P0 MUST NOT create a second execution state machine.
- Persisting this binding into AgentExecution, checkpoints, continuation records, or
  storage requires a fresh AE/APR production PRE-CLAIM and overlap audit.

### 6.2 AIC remains Agent-instance authority

When `agent_instance_id` is present, the binding consumes the full canonical AIC identity tuple
`(owner_user_id, agent_instance_id)`.

`owner_user_id` in this binding is trusted/server-derived authority. It is not accepted from
caller-, model-, client-, connection-, request-, Memory-, Session-, Task-, Branch-, or
source-supplied identity claims.

For an AIC-backed Agent-instance binding, `owner_user_id` and `agent_instance_id` are
co-present or both absent. A bare `agent_instance_id` is insufficient to establish or restore
Agent-instance ownership.

APR-P0 MUST NOT mint or infer either identity component.

On legacy compatibility paths where the trusted AIC tuple is absent, the system MUST NOT infer
`owner_user_id` or `agent_instance_id` from:

- `AgentDefinition.name`;
- current definition-selection `agent_id`;
- `AgentExecution.agent_id`;
- `AgentExecution.owner_instance_id`;
- `execution_id`;
- `session_id`;
- `task_id` or `branch_id`;
- `client_id` or `connection_id`;
- runtime-session identity;
- model output, request metadata, Memory metadata, or source metadata.

Absence remains absence until separately canonical AIC/registration authority resolves a trusted
`(owner_user_id, agent_instance_id)` binding. If a future production design chooses not to store
`owner_user_id` directly inside the binding record, it MUST instead resolve it from an
independently persisted, server-derived execution-owner authority before validating the
`agent_instance_id`; APR-P0 does not choose that storage mechanism.

### 6.3 Reproducible definition/profile binding

An execution binding must identify the definition/profile versions whose semantics
apply to that execution.

A mutable later edit to a definition/profile MUST NOT silently rewrite an existing
execution's meaning across retry, recovery, reconnect, checkpoint restore or
continuation.

The eventual production design may use immutable snapshots, stable version IDs,
content fingerprints, or another independently released mechanism. APR-P0 freezes
the reproducibility requirement, not the storage mechanism.

### 6.4 RuntimeSession boundary

`runtime_session_id` is optional execution-attached runtime/environment identity.
It is not:

- Agent identity;
- Memory owner identity;
- AE lease owner identity;
- user identity;
- capability authorization;
- durable asset identity.

Runtime-session lifecycle remains owned by the applicable future specialized
runtime stage.

## 7. Current-source compatibility freeze

On the APR-P0 development baseline:

- `se/src/domain/schemas/agent.py::AgentDefinition` is still the lightweight
  legacy/current blueprint;
- no production `AgentDefinitionV2` exists;
- no production `AgentProfile` exists;
- no production `AgentExecutionBinding` exists;
- `AgentExecution` contains current `agent_id` and AE
  `owner_instance_id`, but no canonical `agent_instance_id`;
- `AgentExecutionContext` carries current `agent_id` and optional
  `AgentDefinition`, but no canonical AgentProfile/AgentExecutionBinding.

APR-P0 contract landing MUST NOT be interpreted as production migration of those
surfaces.

## 8. Cross-track authority fences

### AIC

Owns stable Agent-instance identity, owner binding, future AgentInstance
representation/storage/registration and lifecycle. APR consumes identity.

### AE

Owns Task/Branch/AgentExecution lifecycle, revision, retries, recovery, leases,
checkpoints, continuation and execution persistence.

### #156 / DCS / CRT / SBX

Own capability selection, CapabilityWorkingSet, routing/target semantics and sandbox
execution boundaries. An AgentProfile cannot bypass these systems.

### SKV2

Owns trusted Skill metadata, authorization, activation and ActiveSkillSet semantics.

### CTX

Owns Memory, personalization, retrieval, ContextSnapshot and Agent-private Memory.
Profile memory preferences do not grant Memory access.

### AAT

Owns schedules, event subscriptions, triggers and automation bindings.

### CAS

Owns durable asset identity, grants, content and lifecycle.

### UBQ / TBO

Own resource admission/accounting and Task lifecycle eligibility/orchestration.

### Tools / PTC / providers

Own logical operations, physical/provider implementations and provider behavior.

### GAC #221

Owns game-local FAST_CONTROL implementation under `cl/src/game_automation/**`.
APR-P0 defines no game-local runtime.

## 9. Specialized runtime stages remain closed

APR-P0 does not implement or release:

- APR-X1 execution lanes / event sequencer;
- APR-FC1 FAST_CONTROL runtime;
- APR-RT1 realtime runtime;
- APR-CU1 computer-interactive runtime;
- APR-MD1 media/job runtime integration.

Those stages require their own current-main dependency/path audit, PRE-CLAIM,
focused tests, Architecture and independent FINAL.

## 10. Production implementation gate

After this contract becomes LANDED / CANONICAL / HEALTHY, any APR-P0 production
implementation still requires a fresh independent PRE-CLAIM.

Potential future paths such as:

```text
se/src/domain/schemas/agent.py
se/src/domain/schemas/agent_execution.py
se/src/runtimes/agent/contracts/profile.py
se/src/runtimes/agent/contracts/instance_binding.py
se/src/runtimes/agent/contracts/context.py
```

are NOT authorized by this contract CLAIM.

A production release must re-audit current AIC, AE, DCS/SKV2, CTX, AAT, CAS,
UBQ/TBO and active-PR ownership before freezing an exact path set.

## 11. Exit gate

APR-P0 contract FINAL requires:

- exact changed paths remain `2 NEW / 2`;
- third path remains absent;
- production/runtime/schema/migration/API/client delta remains zero;
- current AgentDefinition/AgentExecution/AgentExecutionContext compatibility facts
  remain true or any drift is independently classified;
- all six runtime-profile names are present;
- profile-is-not-grant and no-envelope-widening semantics are explicit;
- `AgentDefinition.tools != InferenceRequest.tools` is preserved;
- `AgentExecution.owner_instance_id != agent_instance_id` is explicit;
- trusted `owner_user_id` is co-bound with `agent_instance_id` (or resolved from independently persisted server-derived execution-owner authority);
- AIC/AE/DCS/SKV2/CTX/AAT/CAS/UBQ/TBO/Tools/GAC authority fences are preserved;
- exact-head Linux Architecture is GREEN;
- exact-head Windows Architecture is GREEN;
- independent APR-P0 contract FINAL is PASS;
- unresolved blocking review threads = 0;
- blocking P0/P1/P2 = 0/0/0;
- no MATERIAL current-main drift invalidates the freeze.

Contract landing grants no production, schema/migration, runtime/API/client,
AgentInstance persistence, APR-X1, or merge authority.
