# AE-R9-F Implementation — Explicit AGGREGATE

## Scope

R9-F adds explicit aggregate provenance and atomic aggregate-execution
admission. It never chooses Task result authority; a later ADOPT is mandatory.

## Durable receipt

`agent_task_aggregate_admissions` uses `(task_id, aggregate_request_id)` as its
logical identity and makes `execution_id` unique. It stores:

- target branch and aggregate execution;
- ordered branch snapshots;
- ordered execution snapshots;
- ordered durable-result fingerprints;
- plan and runtime-seed fingerprints;
- creator and creation time.

Migration `15b_r9_aggregate_admission` extends the single R8/R9 migration line.

## Admission transaction

The caller supplies at least two unique ordered source branches and includes
the target branch in that order. Task, budget, branches and current executions
are locked deterministically. Every source must still be OPEN with a COMPLETED
current execution and durable result. The transaction snapshots provenance,
charges one new execution, inserts `RUNNING@1` aggregate execution, moves only
the target branch head, writes reservation + receipt, and leaves Task
nonterminal with all branch resolution states unchanged.

## Verification plan

- Empty/reversible `15b` migration.
- Ordered provenance and result fingerprints survive replay.
- Same request returns the same execution and charges once.
- Changed source order conflicts.
- Task is not implicitly completed or adopted.
