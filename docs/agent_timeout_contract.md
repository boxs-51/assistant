# Agent timeout contract

`agent_limits` in `POST /v1/chat/completions` controls Agent execution budgets. It does not configure the TCP connection, HTTP client, or SSE idle timeout. In Agent chat mode without explicit limits, the initial negotiation window is 120 seconds, with 60 seconds per iteration, 45 seconds per model call, and 30 seconds per ordinary tool. The Agent may then propose a task duration.

| Field | Meaning | Default |
| --- | --- | ---: |
| `agent_limits.timeout_seconds` | Total active Agent execution time. Durable `WAITING` time is excluded. | 60 s |
| `agent_limits.iteration_timeout_seconds` | One Agent loop iteration, including context, inference, and tools. Also bounded by the total budget. | 20 s |
| `agent_limits.inference_timeout_seconds` | One model call. Also bounded by the iteration and total budgets. | 15 s |
| `agent_limits.tool_timeout_seconds` | One ordinary tool call. Also bounded by the iteration and total budgets. Long running capabilities use the remaining iteration and total budgets instead. | 10 s |
| `agent_limits.task_timeout_seconds` | Optional hard wall-clock limit for the task. It keeps running during durable `WAITING` and cannot be reset by the Agent. | Agent proposes |

The first budget that expires ends or fails that operation. A synchronous five minute tool call exceeds the initial tool, iteration, and execution budgets. Its own tool implementation may impose another limit.

In Agent chat mode, the Agent can call the internal `agent.budget.configure` tool to propose `task_seconds` and set `iteration_seconds`, `inference_seconds`, and `tool_seconds`. The first proposal starts a hard task deadline measured from the request start. Later calls can reallocate operation budgets but cannot reset that deadline. Without a user supplied task limit, the server caps the first proposal at 24 hours. If the user supplies `agent_limits.task_timeout_seconds`, that is the hard limit. If the user supplies other `agent_limits` without a task limit, `timeout_seconds` is the maximum the Agent may propose. The deadline and allocations are saved with the Agent execution checkpoint for durable resume.

Ordinary tool failures such as `CAPABILITY_TIMEOUT` are returned as tool results in the next model context. A model call that times out before responding is retried in the next Agent iteration with a larger inference allowance and a system message describing the failure. Retry still consumes an iteration and the task clock keeps running. An expired task deadline ends the task with `AGENT_TASK_TIMEOUT`; the Agent cannot renew it.

To allow a five minute tool call in one iteration, the user may send an explicit budget with some margin:

```json
{
  "model": "gemma3-tools-v3:latest",
  "messages": [{"role": "user", "content": "Run the requested five minute command."}],
  "agent_enabled": true,
  "agent_limits": {
    "timeout_seconds": 600,
    "task_timeout_seconds": 600,
    "iteration_timeout_seconds": 420,
    "inference_timeout_seconds": 90,
    "tool_timeout_seconds": 330
  },
  "config": {"stream": true, "agent_activity_stream": true}
}
```

The UI bridge accepts the same `agent_limits` object in `set_chat_preferences` and sends it with Agent chat requests. The nonstreaming Gateway response wait follows the maximum task budget plus five seconds. Streaming responses use SSE; Gateway sends a `: ping` heartbeat roughly every 15 seconds while waiting. A client or reverse proxy can still close an idle connection according to its own settings. The bundled Python Gateway client currently does not set a separate HTTP connect or read timeout.

Agent timeout failures carry `error_code`, `timeout_scope`, and `timeout_seconds` in the public error payload. The scopes are `task`, `execution`, `iteration`, `inference`, and `tool`. `timeout_seconds` names the configured budget for that scope; it is not the total elapsed time. The Gateway's nonstreaming response wait has `GATEWAY_RESPONSE_TIMEOUT` and `timeout_scope=response_wait`. A capability can also return its own `CAPABILITY_TIMEOUT` result, which the Agent may receive as a tool result and handle in a later iteration.
