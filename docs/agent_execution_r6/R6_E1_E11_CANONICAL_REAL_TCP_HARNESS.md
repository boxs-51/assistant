# R6-E1→E11 Canonical Real-TCP Fault Harness

**Baseline:** `92e0b1a485c15076e39080135d8d34f48bf835fb`

**Dependency:** R6-E0 COMPLETE

**Status:** IMPLEMENTED / PENDING TEST EVIDENCE

## Harness boundary

The canonical module is:

```text
se/tests/e2e/test_r6_e_remote_reconciliation_faults.py
```

Every scenario uses:

```text
real Uvicorn/FastAPI gateway
real localhost TCP WebSocket
real GatewayRealtimeClient receiver thread
real connection.register
real capability.register
real CapabilityRuntime
real RemoteClientDriver / RealtimeMultiplexer
real ClientCapabilityRuntime / CapabilityDispatcher
real local Python capability
real SQLite ClientInvocationLedger where restart durability matters
```

The canonical scenarios do not use:

```text
FakeSocket
manual RealtimeMultiplexer.handle_inbound()
manual capability.result/error/cancelled injection
direct CapabilityDispatcher.dispatch() from test code
new invocation_id for reconciliation
```

## Fault injection policy

Faults are injected only at ownership boundaries while retaining the real
transport stack.

```text
E1:
    deterministic pre-send ConnectionStateError after routing selection

E2:
    close real client WebSocket while target is RUNNING

E3/E4:
    close real transport from send_result after durable TERMINAL commit

E5:
    tear down one dispatcher/process generation while durable row is RUNNING

E10:
    reject server K1 correlation while keeping real K1 WebSocket alive, then
    release its real terminal frame after K2 reconciliation
```

No production-only fault API is introduced.

## Scenario matrix

```text
E1  pre-dispatch loss
    -> NOT_DISPATCHED
    -> server alternate may execute
    -> client target count 0

E2  NON_IDEMPOTENT disconnect while RUNNING
    -> OUTCOME_UNKNOWN
    -> WAITING(CONNECTION)
    -> no fallback

E3  same-process result-send loss
    -> exact TERMINAL reconciliation on K2
    -> target count 1

E4  fresh dispatcher/client process generation
    -> same SQLite ledger
    -> exact TERMINAL recovery
    -> target count 1

E5  crash/restart with ledger RUNNING
    -> UNKNOWN
    -> no second target execution

E6  IDEMPOTENT controlled replay
    -> same invocation_id
    -> second server attempt allowed

E7  DEDUPLICATED controlled replay
    -> stable invocation_id is dedupe key
    -> external effect count remains 1

E8  semantic fingerprint conflict
    -> REMOTE_INVOCATION_CONFLICT
    -> no execution/replay

E9  foreign user/client installation
    -> CAPABILITY_UNAUTHORIZED

E10 late K1 terminal after K2 reconciliation
    -> K2 committed state/revision/output unchanged

E11 server timeout / capability.cancel
    -> remote outcome remains OUTCOME_UNKNOWN
    -> later client terminal cannot resurrect TIMED_OUT invocation
```

## Focused gate

```powershell
py -m pytest -q `
  se/tests/e2e/test_r6_e_remote_reconciliation_faults.py
```

## Transport regression gate

```powershell
py -m pytest -q `
  se/tests/e2e/test_r6_e_remote_reconciliation_faults.py `
  se/tests/e2e/test_phase6_10_true_websocket_agent_client_loop.py `
  se/tests/architecture/test_r6_e0_realtime_future_ownership.py `
  se/tests/architecture/test_r6_b_remote_outcome_safety.py `
  se/tests/architecture/test_r6_c_server_reconciliation.py `
  cl/tests/test_r6_c_reconciliation_contract.py `
  cl/tests/test_r6_d_client_invocation_ledger.py
```

## Broad gate

```powershell
py -m pytest -q se/tests tools cl/tests
```

## Exit rule

Do not formal-close R6-E from implementation alone.

Required evidence:

```text
all E1→E11 tests green
existing true-WebSocket Agent loop green
E0 ownership regression green
broad repository gate green
no "Future exception was never retrieved"
no automatic duplicate NON_IDEMPOTENT/UNKNOWN side effect
```
