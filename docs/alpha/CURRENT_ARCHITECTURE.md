# Alpha architecture — observed versus target (MARM v1.0)

**Snapshot:** source-only assessment at alpha@76842cf56f40faab3083c6de390373ef56fb65f2, 2026-10-19. Runtime deployment topology and complete E2E are UNVERIFIED. Source presence does not establish production wiring.

## Observed module map
| Layer | Code evidence | Role / status |
|---|---|---|
| Desktop CL | cl/src/main.py, cl/src/core/client_runtime.py, cl/src/ui, cl/src/hitl | PyWebView client, local tools, consent, gateway, CODE_PRESENT |
| Gateway/identity | se/src/main.py, se/src/transport/gateway/api/v1, se/src/domain/schemas/request.py | FastAPI bootstrap, request-mode split, HTTP, authentication, CODE_PRESENT |
| Composition | se/src/application/container.py, se/src/kernel | shared application/runtime dependency graph, CODE_PRESENT |
| AGENT | se/src/runtimes/agent/runtime.py | durable Agent orchestration, recovery and execution, CODE_PRESENT |
| DIRECT compatibility | se/src/runtimes/chat, se/src/main.py | separate DirectChatRuntime, CODE_PRESENT (migration target is Agent-only) |
| Tool routing | se/src/runtimes/capability, cl/src/core/capability_dispatcher.py | caller origin, registration, invocations, local/remote boundaries, CODE_PRESENT |
| Security sandbox | se/src/runtimes/capability/sandbox.py, native_tree_recovery_b0.py, se/src/runtimes/agent/native_receipt_verifier.py | deny/quarantine when OS-owned Native receipt unknown, CODE_PRESENT; positive OS authorization UNVERIFIED/HOLD |
| Storage | se/src/infrastructure/storage, Alembic | SQLite/PG17 lifecycle, CAS/CTX/UBQ/AIC ownership and schema, P0 HOLD |
| OTP | se/src/transport/gateway/authentication/services/otp_service.py, se/src/infrastructure/storage/repositories/user_data/pending_registrations.py | SQL issuance fence before Redis SET, scope-limited evidence; HA/reissue UNVERIFIED |
| Planned extensions | docs/agent_automation, docs/skill_v2, docs/agent_platform, docs/central_asset | document contracts and some source primitives; activated features MUST be proved stage by stage |

## Logical trust and data flow
Caller/browser/UI → CL consent/installation/principal → authenticated SE gateway → request DTO/authorization → either DIRECT compatibility runtime or AGENT runtime → logical capability → trusted server or remote client driver → authenticated effect/OS boundary → durable database/object/Redis stores. Identity, owner, generation, invocation ID and resource scope MUST be validated at the actual effect boundary. A model/tool argument, JSON receipt or process PID is never a privileged issuer.

- **CAS:** user owns assets; per-Agent grants remain separate and cannot be inferred from a context reference.
- **AIC:** Agent-instance identity differs from Agent definition, task execution, worker lease and user.
- **CTX:** user-wide versus Agent-private classification must not escape owner/access filters.
- **UBQ:** user quota accounting and timeout semantics remain separate from Agent task budgets.
- **Redis/SQL OTP:** cache ACK loss means uncertain outcome, not license to create an additional challenge.
- **Native:** OS-verified tree custody and signed issuer must be real, not simulations or client self-attestations.

## Migration graph finding
- 30a_ctx_f5_user_wide_memory_scope is predecessor for 31a_aic0_agent_instance_identity AND 31t_direct_terminal_permission.
- 31b_aic0_pg_guard_forward follows 31a.
- This yields independent terminal revisions **31b and 31t**. No common merge revision was observed at the pinned SHA. Generic alembic upgrade head is therefore unsafe/unresolved; NEVER stamp, drop or delete history as a workaround.
- Safe design needs actual revision-head inventory, supported linearization/merge revision, populated SQLite/PG17 historical upgrade, current live schema provenance, crash/rollback and account integrity checks.

## MARM MA-01..MA-14 decisions
| Cases | Observation / current posture |
|---|---|
| MA-01/02 | No claim a single legacy-only or modern-only production runtime is active; active runtime proof UNVERIFIED. |
| MA-03/12 | DIRECT and AGENT coexist intentionally for compatibility in code; no automatic rewrite. |
| MA-04/05/11/13 | De-duplication, migration/cutover and old-branch divergence require functional parity and an explicit reversible decision. |
| MA-06/07/10 | Adapters, client/server DTOs, aliases and errors need authenticated compatibility E2E; no trust expansion. |
| MA-08/09 | Redis+SQL unknown commits and shared CAS/UBQ/CTX/AIC database ownership need single-writer, race/rollback proof. |
| MA-14 | Host process list, production launcher and OS containment not observed: UNVERIFIED. |

## Architecture decisions not yet authorized
ADR-01 (candidate): retain DIRECT during compatibility phase; do not infer a security defect merely because Agent-only is a future target. Alternatives are no-change, bounded adapter and reversible strangler migration. Reject destructive migration without parity evidence.
ADR-02 (candidate): no new permission, Native execution, CAS grant or cross-principal visibility based solely on capabilities, Skill metadata or client assertions.
ADR-03 (candidate): preserve the Alembic graph and original deployed states; choose a verified merge revision path, not migration squashing.
**All ADR candidates: PROPOSED, no implementation authority.** Details and owner decisions belong in stage issues.

## Review limitations
Static source/doc audit covers architecture and critical boundaries, not full code-line review, runtime process verification, Windows kernel isolation or PostgreSQL deployment safety. Negative tests passing in a single workflow do not prove positive Native effect safety. No source or test oracles changed.
