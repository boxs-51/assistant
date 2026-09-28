# AAT — Agent Automation & Triggers roadmap

**Namespace:** `AAT-*`
**Planning baseline:** `main@a524ba870aec3ac73be7311d068d43fc1c963cda` (2026-09-27)
**State:** `RESERVED / NOT OPEN`; opening date unassigned
**Authority now:** documentation and namespace reservation only

## 1. Purpose

AAT is the future automation system for one user-owned Agent instance: the Agent may request to publish an interface/tool, schedule a wakeup, or subscribe to an event. AAT stores and delivers those requests durably and admits a single authorized activation per occurrence. It does not own renewable user-resource quota, Task orchestration policy, Agent execution lifecycle, the Capability registry, or communication between connected Agents.

## 2. Core boundaries

- Every Agent request uses a server-resolved `(owner_user_id, agent_instance_id)`. `AgentDefinition.name`, model output and client-supplied owner IDs cannot establish ownership. The identity contract is shared with AIC-0 and the Agent registration owner.
- Publishing an Agent-owned tool means requesting a versioned schema, name, purpose, input/output contract, permissions, owner and lifecycle. Existing Capability/Tools authorities validate and register it. AAT stores the automation binding and execution target; it does not create a second tool registry.
- A scheduled wakeup or event subscription is a durable request with selector, scope, start/expiry, occurrence limit, rate limit, state, revision and idempotency identity. AAT can remove/revoke it. Raw event payloads must be validated and filtered before Agent activation.
- AAT does not invoke an Agent recursively inside the publishing tool call. A trigger creates a separate admission attempt via AE and TBO, with a distinct occurrence receipt. A queued trigger does not mean the Agent already ran.
- AAT never mints renewable quota. It asks TBO whether a Task is lifecycle-eligible, asks UBQ whether the resolved user has resource admission for the activation, and asks AE whether an Execution can be activated. Exhausted, cancelled, expired or unauthorized work stays dormant or is rejected with a durable reason.
- Agent-created tools and triggers must not grant CTX Memory or CAS asset access. Those domains check their own per-Agent authorization at use time.

## 3. Durable contract candidate

```text
AutomationDefinition:
  automation_id, owner_user_id, agent_instance_id, kind, version,
  selector_or_schedule, capability_binding?, permission_scope,
  starts_at, expires_at, max_occurrences, rate_limit,
  state, revision

TriggerReceipt:
  automation_id, occurrence_id, source_event_id_or_due_slot,
  dedupe_key, observed_at, admission_state,
  task_id?, execution_id?, error_code?
```

The exact schema is reserved for AAT-0. A duplicate event, scheduler restart or competing worker must converge on one occurrence receipt and at most one AE activation. This is an admission guarantee, not a claim that arbitrary external tool side effects are exactly once; AE-R6 reconciliation remains the side-effect authority.

## 4. Reserved stages

| Stage | Deliverable | Gate |
|---|---|---|
| `AAT-0` | Fresh audit of Capability registration, event bus, Agent identity, AE waiting, TBO Task-eligibility handoff and UBQ resource-admission handoff; contract freeze | Dedicated issue/CLAIM, independent review, exact-head CI |
| `AAT-1` | Owner-bound automation/tool-publication DTO and permission policy; existing registry adapter contract | Cross-user/other-Agent denial and tool-schema validation |
| `AAT-2` | Durable one-shot schedule and cancellation; scheduler ownership/fencing | Restart, clock, duplicate due-slot and two-worker races |
| `AAT-3` | Filtered event subscription, unsubscribe and event deduplication | Duplicate/out-of-order event, expiry, backlog and rate-limit tests |
| `AAT-4` | Agent-owned tool publication/invocation handoff through Capability authority | Version/update/revoke, purpose/parameters, access and UBQ logical tool-charge tests |
| `AAT-5` | AE/TBO/UBQ activation integration and user controls | UBQ exhaustion, Task ineligibility, WAITING, cancellation, reconnect and self-loop tests |
| `AAT-6` | Full fault, metrics, rollout/rollback and exit review | Linux/Windows Architecture, independent audit, no duplicate activation |

Initial scope is the owner's Agents inside one installation/account boundary. Cross-user delegation and arbitrary external webhook ingestion require their own contract. Tool sharing between connected Agents uses AIC discovery/permission rules, then the AAT/Capability invocation path.

## 5. Opening gate

`AAT-*` remains `RESERVED / NOT OPEN`. AAT-0 requires an explicit opening record, a stable Agent-instance identity contract, current-main audit, dedicated issue, overlap agreement with Capability/TV1/PTC/AE/TBO/UBQ and a docs/tests-only claim under Issue #85. No scheduler, event subscription, or tool-publication production authority follows from this roadmap alone.
