# R4 COMPLETION RECORD

**Repository:** `boxs-51/assistant`  
**R4:** Active Budget / Wait TTL / Deadline Hierarchy  
**Status:** `PENDING EXIT-GATE EVIDENCE`

## Implementation inventory

```text
A1  durable representation
A2  clock + active budget
B1  WAITING freeze + TTL policy
B2  resume + expiry + restart + race safety
C1  iteration deadline
C2  capability timeout hierarchy
C3  nested Agent budget inheritance
D   exit gate
```

## Architectural result

After C1-C3 are applied, R4 provides:

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

## Explicitly deferred

```text
TaskBudget / task cancellation tree       R5
durable claim lease / claim_expires_at    R7
checkpoint transaction redesign          R7
fork/retry branch scheduling              later roadmap
```

## Evidence required before changing status to COMPLETE

See:

```text
docs/agent_execution_r4/R4_EXIT_GATE.md
```
