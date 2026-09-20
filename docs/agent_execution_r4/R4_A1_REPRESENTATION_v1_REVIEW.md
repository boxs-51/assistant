# R4 Contract Freeze Review + A1 Representation v1

**Repository HEAD audited:** `5619e99830c92ceadebae1cfa3df028624437995`

## Contract review verdict

**FROZEN.**

The draft is internally consistent with R0/R3/R7 boundaries after one final
compatibility decision:

```text
legacy WAITING + remaining_active_budget_seconds=NULL
=> fail closed on future R4-B2 resume
```

A legacy row remains readable/representable, but runtime must not mint a fresh
timeout or estimate active time from wall-clock timestamps.

## Frozen decisions relevant to A1

```text
remaining_active_budget_seconds = durable duration
wait_expires_at = durable UTC wall-clock timestamp
raw monotonic deadlines are never persisted
both new DB columns are nullable for migration compatibility
wait_expires_at is indexed
legacy NULL remains representable
runtime fail-closed behavior is deferred to R4-B2
```

## R4-A1 patch scope

Production:

```text
MODIFY se/src/domain/schemas/agent_execution.py
MODIFY se/src/infrastructure/storage/models/sql/agent/execution.py
ADD    se/src/infrastructure/storage/migrations/sql/versions/10a_r4_active_budget_wait_ttl.py
```

Tests:

```text
ADD se/tests/architecture/test_r4_a1_representation.py
ADD se/tests/integration/test_r4_a1_migration.py
```

A1 intentionally does NOT modify:

```text
AgentExecutionContext
AgentRuntime
DurableAgentStore.resume_execution()
CapabilityExecutionContext
CapabilityToolExecutionAdapter
AgentCapabilityDriver
wait TTL policy
clock abstraction
iteration timeout enforcement
LONG_RUNNING timeout semantics
```

Those belong to later R4 A2/B/C work.

## A1 exit gate

```powershell
py -m pytest -v se/tests/architecture/test_r4_a1_representation.py
py -m pytest -v se/tests/integration/test_r4_a1_migration.py

py -m pytest -q `
  se/tests/architecture/test_roadmap_r0_r2.py `
  se/tests/architecture/test_r3_a1_a3_representation.py `
  se/tests/architecture/test_r3_execution_lineage.py `
  se/tests/integration/test_r3_migration_smoke.py `
  se/tests/integration/test_r3_nested_agent_durable_lineage.py

py -m pytest -q se/tests tools cl/tests
```

## Artifact SHA256

```text
R4_CONTRACT_FREEZE_v1.patch
c746522d5c676ee0807dbb63c728661c33c14cacb9479488302cf46e96bef320

R4_A1_REPRESENTATION_v1.patch
cc5bb5c636e3ea90eeedfb4f767325a1a92e169376fec82c998ff87c12ee7dc5
```


## Static validation performed

```text
R4_CONTRACT_FREEZE_v1.patch
  git apply --check  PASS
  1 file changed, 1783 insertions

R4_A1_REPRESENTATION_v1.patch
  git apply --check  PASS
  git apply --stat   PASS
  git apply --numstat PASS
  5 files changed
  248 insertions
  1 deletion

py_compile
  domain schema       PASS
  SQL model           PASS
  migration 10a       PASS
  A1 representation  PASS
  A1 migration test  PASS
```

Repository tests were not executed by this assistant. The commands above are the
required post-apply gate.
