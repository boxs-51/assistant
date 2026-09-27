# Agent activity in chat SSE

Clients opt in with `agent_enabled: true` and `config: {"stream": true,
"agent_activity_stream": true}` on `POST /v1/chat/completions`.

Each nonfinal activity is an SSE `data:` frame with `object: "agent_stream_event"`:

```json
{
  "object": "agent_stream_event",
  "event_id": "exec-1:agent.tool.started:...",
  "event_type": "agent.tool.started",
  "timestamp": 1790000000.0,
  "execution_id": "exec-1",
  "turn_id": "turn_...",
  "channel": "tool",
  "data": {
    "tool_call_id": "call-1",
    "invocation_id": "invocation-1",
    "name": "skill.load",
    "purpose": "Load instructions for skill web-research",
    "arguments": {"skill_id": "web-research"},
    "status": "started"
  }
}
```

The `channel` is `lifecycle`, `progress`, or `tool`. Tool status progresses
through `requested`, `started`, and `completed` or `failed`. The same
`execution_id` and `tool_call_id` identify updates to one tool call. The
`agent.context.ready` event lists available capability and skill IDs; it does
not expose raw context. `agent.inference.requested` means the model is being
called. `agent.progress` carries assistant text supplied alongside tool calls,
when the model provides it. It is not private model reasoning.

The final answer remains a `gateway_stream_chunk` in the existing `choices`
shape, followed by `data: [DONE]`. Activity events do not contribute to the
final answer. Clients should key activity rendering by `event_id` to avoid
duplicates. The connection receives heartbeat comments while execution is
idle. Tool arguments are bounded and redact common secret fields.

Activity is delivered on the active SSE connection. This endpoint has no
event replay after a disconnect; clients must not retry the chat request to
resume observation because that can start a second execution.
