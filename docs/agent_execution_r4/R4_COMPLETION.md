# R4 COMPLETION RECORD

**Repository:** `boxs-51/assistant`  
**R4:** Active Budget / Wait TTL / Deadline Hierarchy  
**Completion HEAD:** `90065c730c33ab3261063183570278d4ea13a424`  
**Status:** `COMPLETE`

## Implementation inventory

```text
A1  durable representation                    COMPLETE
A2  clock + active budget                     COMPLETE
B1  WAITING freeze + TTL policy               COMPLETE
B2  resume + expiry + restart + race safety   COMPLETE
C1  iteration deadline                        COMPLETE
C2  capability timeout hierarchy              COMPLETE
C3  nested Agent budget inheritance           COMPLETE
D   exit gate                                 COMPLETE
```

## Architectural result

R4 provides:

```text
Execution active budget
        ↓
Iteration budget
        ↓
Operation budget
```

with durable suspension:

```text
RUNNING
  ↓ freeze remaining duration
WAITING
  ↓ wall-clock TTL only
RESUME
  ↓ restore same remaining duration
RUNNING
```

Normal TOOL:

```text
min(
  execution remaining,
  iteration remaining,
  tool timeout
)
```

AGENT/LONG_RUNNING:

```text
min(
  execution remaining,
  iteration remaining
)
```

Nested synchronous Agent:

```text
min(
  child configured timeout,
  parent execution remaining,
  parent iteration remaining,
  live invocation remaining
)
```

## Completion evidence — 2026-09-20

```text
R4 focused captured command      40 passed, 1 warning
Cross-phase regression           96 passed
Full se/tests + tools + cl/tests 534 passed, 5 warnings
Failures                          0
```

The full suite is a superset of the R4 tests and covers the A2 execution-budget
context test that was not explicitly listed in the captured focused command.

The remaining Windows asyncio Proactor/subprocess cleanup warnings are not R4
timing-semantic failures. They are carried forward as R5-A async-ownership
hardening evidence instead of reopening R4.

## Explicitly deferred

```text
TaskBudget / task cancellation tree       R5
durable claim lease / claim_expires_at    R7
checkpoint transaction redesign          R7
fork/retry branch scheduling              later roadmap
```

## Closure

R4 is formally closed on HEAD `90065c7`. Future work may rely on the following
contracts without reinterpreting them:

```text
WAITING freezes active budget.
Wait TTL continues in wall-clock time.
Resume restores the same execution budget and execution_id.
Iteration is bounded by execution remaining time.
Operation is bounded by iteration/execution remaining time.
One-shot tool timeout does not cap AGENT/LONG_RUNNING capability execution.
Nested synchronous Agent budget cannot exceed the parent timing envelope.
Raw monotonic timestamps are not durable state.
```

See `docs/agent_execution_r4/R4_EXIT_GATE.md` for the recorded gate evidence.
