# Phase 6.3 — Realtime Multiplex

## Status

Phase 6.3 is the transport/correlation layer between the Phase 6.2 connection
lifecycle and future Phase 6.4 remote-client execution.

This slice does **not** introduce client capability discovery or a remote driver.

## Responsibilities

1. Correlate `capability.invoke` with exactly one terminal response.
2. Route progress without consuming the pending invocation.
3. Send cancellation on timeout or explicit cancel.
4. Fail pending invocations bound to a disconnected connection.
5. Reject cross-connection response injection.
6. Keep duplicate terminal responses idempotent.

## Flow

```text
CapabilityRuntime / future RemoteClientDriver
              |
              v
       RealtimeMultiplexer
              |
       ConnectionRegistry
              |
          active socket
              |
       capability.invoke
              |
            Client
         /      |      \
     progress  result  error
         |       |       |
         +-------+-------+
                 |
         invocation_id correlation
```

Timeout path:

```text
wait_for timeout
      |
      v
capability.cancel
      |
      v
client
      |
capability.cancelled
```

Disconnect path:

```text
ConnectionRegistry.disconnect
          |
          v
RealtimeMultiplexer.disconnect
          |
          v
fail_connection(connection_id)
```

Only pending invocations for the affected connection are failed.

## Safety invariants

- `capability.invoke` requires `connection_id` and `invocation_id`.
- Result/error/cancelled/progress messages require `invocation_id`.
- An inbound envelope cannot claim a different `connection_id` than the socket
  on which it was received.
- Terminal messages remove their pending invocation before completing the future,
  making duplicate terminal messages harmless.
- AgentRuntime remains unaware of transport location.

## Exit gate

The Phase 6.3 gate is the realtime architecture test module:

```text
tests/architecture/test_phase6_3_realtime_multiplex.py
```

Required coverage:

- invoke/result correlation
- duplicate terminal result idempotency
- remote error correlation
- timeout -> cancel
- connection-scoped disconnect failure
- inbound connection identity enforcement