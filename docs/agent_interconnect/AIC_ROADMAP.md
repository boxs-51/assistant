# AIC — Agent Interconnect & Communication roadmap

**Namespace:** `AIC-*`
**Planning baseline:** `main@a524ba870aec3ac73be7311d068d43fc1c963cda` (2026-09-27)
**State:** `RESERVED / NOT OPEN`; opening date unassigned
**Authority now:** documentation and namespace reservation only

## 1. Purpose

AIC is the interface through which connected user-owned Agent instances find each other, exchange requests/results and invoke permitted Agent capabilities. It defines durable identities and message/delivery contracts across connections. The existing `AgentSession`, `AgentMessage`, Task and Capability paths are starting points to audit and reuse; AIC does not replace their released execution semantics by naming a new protocol.

## 2. Identity and authorization contract

One Agent instance has one opaque durable `agent_instance_id`, one `owner_user_id`, a versioned Agent definition/template reference and lifecycle state. The same template used by two users produces two different instances. An Agent instance is distinct from a currently connected client, connection generation, session, Task, Branch and Execution. The server binds identity on registration and resolves it from trusted authority on every request. The owner may suspend or delete an instance; memory, subscriptions, outstanding messages and asset grants require coordinated revocation rules.

The first AIC scope is Agent-to-Agent communication **within one owner user**. An Agent must be invited/authorized for the addressed recipient or shared conversation; mere presence in an Agent directory does not grant access to private CTX Memory, another Task, or CAS assets. Cross-user Agent federation is a separate future decision.

## 3. Message and connection contract candidate

```text
AgentEnvelope:
  message_id, owner_user_id, sender_agent_instance_id,
  recipient_agent_instance_id, conversation_id?, task_id?,
  correlation_id, kind, schema_version, created_at,
  expires_at?, capability_id?, purpose?, payload_ref?,
  idempotency_key, delivery_state
```

Message kinds include request, response, notification and cancellation. Tool requests keep the mandatory purpose/description and parameters in their own DTO. Large payloads and assets use references; receipt of an `asset_id`, Context ref or Memory ref is never authorization to read it. AIC evaluates CTX/CAS rights at recipient retrieval/use time. Agent communication cannot silently copy another Agent's private Memory.

Connection loss preserves delivery state. At-least-once transport delivery may be deduplicated by `message_id`; result correlation and admission are durable. A message may ask AE/AAT to activate an Agent, but TBO first determines Task lifecycle eligibility, UBQ admits/charges renewable user resources, and AE owns Execution activation. Unknown tool side effects follow AE-R6 reconciliation, not blind message replay. AIC delivery is not a second Task/Execution state machine.

## 4. Reserved stages

| Stage | Deliverable | Gate |
|---|---|---|
| `AIC-0` | Exact-head Agent registry/identity audit; freeze per-user `agent_instance_id`, lifecycle and migration from name-based APIs | Dedicated issue/CLAIM, owner/CTX/AAT/CAS agreement, independent review |
| `AIC-1` | Agent directory and per-recipient communication permission DTO | Same-template/two-user isolation and unauthorized recipient tests |
| `AIC-2` | Versioned envelope, delivery receipts, ordering/correlation and dedupe | Duplicate/out-of-order/reconnect/restart tests |
| `AIC-3` | Connected request/response and notification API, bounded streaming | Disconnect, expiry, backpressure and cancellation tests |
| `AIC-4` | Agent capability discovery/invocation handoff through AAT/Capability | Mandatory purpose/parameters, permission and no recursion bypass tests |
| `AIC-5` | CTX/CAS/UBQ/TBO/AE integration and user-facing conversation controls | Private Memory, asset grant, user-resource quota and Task-lifecycle isolation tests |
| `AIC-6` | Full multi-worker fault, migration and exit audit | Linux/Windows Architecture, independent audit and compatibility evidence |

## 5. Ownership and opening gate

The Agent registration/Capability owner retains definition registration; AIC freezes the stable instance identity and communication contract. AE owns Task/Branch/Execution and remote side-effect reconciliation. AAT owns automated wakeups and Agent-owned tool publication. CTX owns Memory and personalization. CAS owns asset grants and content. UBQ owns renewable user-resource admission. TBO owns Task lifecycle eligibility/orchestration and cannot mint UBQ quota.

`AIC-*` remains `RESERVED / NOT OPEN`. AIC-0 requires an explicit opening record, current-main identity/path audit, dedicated issue, migration/compatibility plan for current `agent_id`/name consumers, independent review and Issue #85 claim. Listing these stages grants no implementation or merge authority.
