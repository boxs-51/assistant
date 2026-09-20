# R6-B Completion — Live Remote Outcome / Safe Fallback

**Status:** COMPLETE

**Completion HEAD:** `35b885fbe95d0bfebeddcaef9b200fd74b9f0031`

## Delivered

R6-B makes remote delivery certainty durable and uses it as the authority for
fallback safety.

```text
remote selected
    -> NOT_DISPATCHED

remote dispatch begins
    -> IN_FLIGHT

transport proves no dispatch
    -> NOT_DISPATCHED

send/connection loss after dispatch may have begun
    -> OUTCOME_UNKNOWN

authoritative remote result/error
    -> TERMINAL_COMMITTED
```

Automatic fallback is now:

```text
NOT_DISPATCHED
    -> allowed

OUTCOME_UNKNOWN + IDEMPOTENT
    -> allowed with the same invocation_id

OUTCOME_UNKNOWN + DEDUPLICATED
    -> allowed with the same invocation_id

OUTCOME_UNKNOWN + NON_IDEMPOTENT
    -> blocked

OUTCOME_UNKNOWN + UNKNOWN
    -> blocked
```

R6-B also removes:

```text
server implementation exists == replay is safe
```

from Agent continuation behavior.

## Evidence

The first focused gate produced:

```text
39 passed
1 failed
```

The failure was expected under the new fail-safe contract: the E2E fixture
claimed to test IDEMPOTENT fallback but registered the capability as UNKNOWN.

After correcting only the fixture declaration:

```text
single E2E:
    1 passed in 6.56s

focused R6-B:
    40 passed in 6.71s

repository-wide:
    618 passed, 9 warnings in 97.79s
```

The warnings are existing dependency/configuration deprecations and are not
R6-B correctness failures.

## Exit decision

R6-B exit gate is satisfied:

```text
No automatic NON_IDEMPOTENT/UNKNOWN duplicate side effect is authorized after
remote outcome becomes unknown.
```

## Next phase

R6-C owns:

```text
capability.reconcile
capability.reconciliation
same-process RUNNING detection
exact terminal replay after reconnect
semantic fingerprint conflict rejection
same-principal / same-client-installation reconciliation authorization
```

R6-D remains the phase that makes the client terminal ledger durable across a
client process restart.
