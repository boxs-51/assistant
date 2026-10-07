# AIC-0-R0 — Stable Agent-instance identity contract freeze

Canonical policy: Issue #85 v2.5  
Canonical workspace: Issue #365  
Consumer: APR #278 / APR-P0  
Development baseline: `main@b8a1989eb03c7ea15ecd6eb17e3fc90bd7450570`  
Baseline Architecture: `#2323 / 37649368535 = GREEN/GREEN`

## 1. Claim and authority

```text
stage = AIC-0-R0
class = CONTRACT + ARCHITECTURE EVIDENCE / ZERO PRODUCTION
contract CLAIM = ACTIVE
production CLAIM = NONE
schema/migration authority = NONE
API/runtime migration authority = NONE
merge authority = NONE
APR-P0 authority = HOLD
exact changed paths = 2 NEW / 2
third path = PROHIBITED
```

Exact claimed paths:

1. `docs/agent_interconnect/AIC_0_AGENT_INSTANCE_IDENTITY_CONTRACT_B8A1989E.md`
2. `se/tests/architecture/test_aic0_agent_instance_identity_contract.py`

This issue is the explicit opening record for **AIC-0-R0 contract/evidence work only**.
The existing AIC roadmap and namespace-registry `RESERVED / NOT OPEN` wording continues
to fence AIC production implementation and AIC-1+ work. R0 does not open production.

## 2. Canonical identity

A durable Agent instance is identified by:

```text
(owner_user_id, agent_instance_id)
```

`agent_instance_id` is an opaque stable server-controlled identifier for one
user-owned Agent instance. It is distinct from:

- `AgentDefinition.name`;
- current definition-selection `agent_id`;
- `AgentExecution.agent_id`;
- `AgentExecution.owner_instance_id`;
- `execution_id`;
- `session_id`;
- `task_id`;
- `branch_id`;
- `client_id`;
- `connection_id`;
- runtime-session identity.

`AgentExecution.owner_instance_id` remains AE lease/recovery worker ownership and
MUST NOT be reinterpreted as `agent_instance_id`.

A future durable Agent instance binds:

```text
owner_user_id
agent_instance_id
definition_id / definition_name
definition_version
lifecycle_state
```

The exact production representation and storage location are NOT frozen by R0.

## 3. Ownership and trust rules

The server derives `owner_user_id` from authenticated authority. A caller-, model-,
client-, connection-, request-, Memory-, Session-, Task-, Branch-, or source-supplied
owner/Agent identifier cannot establish durable Agent-instance ownership by itself.

Identity lookup MUST fail closed when:

- the Agent instance is unknown;
- the authenticated user does not own it;
- the instance lifecycle does not permit use;
- a definition/instance binding is stale or invalid;
- a request attempts cross-user substitution.

Same-template isolation is mandatory:

- two users selecting the same Agent definition receive distinct Agent instances;
- one user may have two distinct instances of the same definition;
- neither case may merge instance identity, private Memory, automation bindings, or
  asset grants by definition name.

## 4. Lifecycle and revocation handoff

R0 freezes the minimum lifecycle vocabulary:

```text
ACTIVE
SUSPENDED
DELETED
```

Production lifecycle storage/transitions remain future AIC-0-P1 authority.

- ACTIVE may be resolved subject to normal authorization.
- SUSPENDED fails closed for new Agent-instance use unless a later owner releases a bounded administrative operation.
- DELETED is not reusable identity and cannot silently alias a new instance.
- Revocation must be consumable by CTX/AAT/CAS without transferring those domains' own authority to AIC.

## 5. Current-source audit and migration classification

Current source on the development baseline is definition/name oriented:

```text
se/src/agent/registry.py
  AgentRegistry = in-memory Dict[str, AgentDefinition]
  key = AgentDefinition.name

se/src/transport/gateway/api/v1/agent_router.py
  /v1/agents/{agent_name}
  list/get/register = Agent definition/name compatibility surface

se/src/runtimes/agent/resolver.py
  agent_id / default_agent_id = current Agent-definition selection semantics

se/src/domain/schemas/agent_execution.py
  AgentExecution.agent_id = execution selected Agent definition/reference
  AgentExecution.owner_instance_id = AE lease/recovery ownership
```

| Surface | R0 classification | Future rule |
|---|---|---|
| `AgentRegistry` | DEFINITION_ONLY | Remains definition registry unless separately migrated |
| `/v1/agents` list/get/register | DEFINITION_COMPATIBILITY | Must not silently become instance API |
| `AgentResolver.agent_id` | DEFINITION_SELECTOR | May later bind through explicit compatibility logic |
| `default_agent_id` | DEFINITION_SELECTOR | Remains definition preference until separately migrated |
| `AgentExecution.agent_id` | EXECUTION_DEFINITION_REFERENCE | Does not prove Agent-instance ownership |
| `AgentExecution.owner_instance_id` | AE_LEASE_OWNER | Never Agent-instance identity |

Compatibility rule: current name/definition identity remains a definition selector
during migration and MUST NOT be silently reinterpreted as durable instance identity.

AIC-0-P1 must perform a fresh exact-main audit before freezing any production path
for durable representation, owner-bound registration/lookup, API compatibility,
lifecycle transitions, persistence, schema, or migration.

## 6. Cross-track authority fences

### APR
APR owns Agent profile/runtime composition. APR may consume canonical `agent_instance_id`
in a future `AgentExecutionBinding`, but APR MUST NOT mint, persist, migrate, or own Agent instances.

### CTX
CTX owns Memory, personalization, retrieval, ContextSnapshot, and Agent-private Memory semantics.
AGENT_PRIVATE storage/read authority remains CLOSED until CTX separately consumes canonical AIC identity.

### AAT
AAT owns schedules, event subscriptions, Agent-owned automation bindings, and activation handoff.
R0 does not create automation records or trigger authority.

### CAS
CAS owns asset identity, grants, discover/read/use authorization, provider binding, deletion, and lifecycle.
R0 does not create or mutate Agent grants or assets.

### AE
AE owns Task/Branch/AgentExecution lifecycle, recovery, retries, checkpoints, reconciliation, and lease ownership.
`owner_instance_id` remains AE authority.

### #156 / DCS / CRT / SBX
Capability selection, routing, target semantics, sandbox execution, and physical capability authority remain
with #156 and its child tracks. R0 grants no capability or sandbox authority.

## 7. Exit gate

AIC-0-R0 may reach FINAL only when:

1. exact changed paths remain 2 NEW / 2;
2. production/runtime/schema/migration/API/client delta remains ZERO;
3. this contract preserves the identity distinctions above;
4. architecture evidence proves current definition/name surfaces have not been silently reclassified as durable instance identity;
5. architecture evidence proves `AgentExecution.owner_instance_id` remains distinct from `agent_instance_id`;
6. AIC production and AIC-1+ remain RESERVED / NOT OPEN;
7. exact-head Linux Architecture is GREEN;
8. exact-head Windows Architecture is GREEN;
9. independent AIC-0-R0 contract FINAL is PASS;
10. unresolved blocking review threads = 0;
11. blocking P0/P1/P2 = 0/0/0;
12. no MATERIAL current-main drift invalidates the identity freeze.

Landing R0 does not itself authorize AIC-0-P1 or APR-P0 production work. APR-P0
contract PRE-CLAIM may only be reconsidered after R0 is LANDED / CANONICAL / HEALTHY
and a fresh overlap audit confirms APR will consume, not own, Agent-instance identity.
