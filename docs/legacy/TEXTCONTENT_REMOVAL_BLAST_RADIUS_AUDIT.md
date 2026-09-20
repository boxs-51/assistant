# TextContent Removal — Blast Radius Audit & Migration Plan

Repository: `boxs-51/assistant`  
Audited commit: `7d8fda84c3b106d064361c1921c37c568b325129`  
Scope: remove only the `TextContent` DTO while preserving all other content parts, multimodal types, tool contracts, citations, reasoning fields, and transport structure.

## 1. Target contract

Before:

```python
MessageContentPart(
    type="text",
    text=None,
    data=TextContent(
        data="...",
        format="structured" | "code" | ...,
        language=...,
    ),
)
```

After:

```python
MessageContentPart(
    type="text",
    text="...",
    data=None,
)
```

`MessageContentPart.data` remains available for non-text structured content:

- `ImageContent`
- `AudioContent`
- `VideoContent`
- `DocumentContent`
- `GatewayAttachment`
- `UrlContent`

No other content model is removed.

For thinking/reasoning content that previously used `TextContent`:

```python
MessageContentPart(
    type="thinking",
    text="...",
    data=None,
)
```

Streaming remains unchanged at the public contract level:

```python
GatewayStreamDelta.content: str | None
GatewayStreamDelta.reasoning_content: str | None
```

## 2. Direct production blast radius

### MUST CHANGE

| File | Current dependency | Migration |
|---|---|---|
| `se/src/domain/schemas/attachment.py` | Defines `TextContent` | Delete class |
| `se/src/domain/schemas/message.py` | Imports `TextContent`; includes it in `MessageContentPart.data` union | Remove import and union member; add legacy-shape normalization |
| `se/src/provider/gemini/converters/chats/response.py` | Imports and creates `TextContent` in text, thinking, executable-code and execution-result paths; stream extraction reads `part.data.data` | Emit `MessageContentPart.text`; stream extraction reads `part.text` |
| `cl/src/schemas/attachment.py` | Defines client-side `TextContent` | Delete class and now-unused `computed_field` import |
| `cl/src/schemas/message.py` | Imports `TextContent`; includes it in `data` union | Remove import and union member; add legacy-shape normalization |

### NO PRODUCTION CHANGE REQUIRED

| Area | Audit result |
|---|---|
| Gemini request converter | Already consumes `part["text"]` for normal text. Once Gemini response emits flat text, the existing request path becomes correct without a TextContent-specific adapter. |
| OpenAI provider | No `TextContent` symbol dependency found. |
| Ollama provider | No `TextContent` symbol dependency found. |
| AgentRuntime | No direct `TextContent` dependency found. |
| ProviderInferenceAdapter | Uses generic JSON-safe message conversion; no direct `TextContent` dependency. |
| WorkflowRuntime | No direct `TextContent` dependency. |
| SessionRuntime | No direct class dependency. Persistence can contain historical nested TextContent-shaped JSON, handled by compatibility normalization when parsed back into `MessageContentPart`. |
| Client `agent_engine.py` | Already creates `MessageContentPart(type="text", text=...)`. |
| Client `mock_provider.py` | Already emits `text=` for text and thinking parts. |
| Client console renderer | Current renderer uses `part.text` for `text` and `thinking`; this migration aligns server output with the current UI. |
| Image/audio/video/document/file/url | Their `data` payloads remain unchanged. |
| Tool calls/results | Unchanged. |
| Citation metadata | Unchanged. |
| MCP test `FakeTextContent` | Test-local MCP fixture with a different semantic type; must not be renamed or removed. |
| Historical docs/patch files | No production effect; leave untouched. |

## 3. Wire-format change

This is an intentional wire-format migration for text parts.

Old:

```json
{
  "type": "text",
  "text": null,
  "data": {
    "data": "hello",
    "format": "structured",
    "encoding": null,
    "token_count": null,
    "line_count": null,
    "language": null
  }
}
```

New:

```json
{
  "type": "text",
  "text": "hello",
  "data": null
}
```

For code, the new wire payload keeps fenced Markdown as flat text so the client can render it:

```json
{
  "type": "text",
  "text": "```python\nprint('hello')\n```"
}
```

No media wire shape changes.

## 4. Backward compatibility / persisted sessions

Old session rows may already contain serialized TextContent-shaped objects.

A destructive DB migration is not necessary. The migration patch adds a `model_validator(mode="before")` to SE and CL `MessageContentPart`.

It recognizes only legacy `text` / `thinking` payloads and converts:

```json
{
  "type": "text",
  "data": {
    "data": "legacy",
    "format": "structured"
  }
}
```

into:

```json
{
  "type": "text",
  "text": "legacy",
  "data": null
}
```

Legacy `format="code"` is converted back to fenced Markdown using the old `language` field so previously persisted code remains renderable.

The compatibility layer does not recreate or retain the `TextContent` class.

## 5. Gemini-specific impact

The Gemini response converter is the only production provider converter found that constructs `TextContent`.

Required changes:

1. Plain text:
   - `data=TextContent(...)`
   - becomes `text=...`.

2. Markdown/code blocks:
   - Preserve the original fenced Markdown in `text`.
   - Do not pass provider formatting metadata downstream as a nested TextContent object.

3. Thinking:
   - `type="thinking", data=TextContent(...)`
   - becomes `type="thinking", text=...`.

4. Executable code / code execution output:
   - Continue using fenced Markdown, but store it directly in `text`.

5. Gemini streaming:
   - Replace `part.data.data` extraction with `part.text`.

This directly eliminates the observed `{"text": None}` round-trip failure between Gemini response conversion and the next Gemini request.

## 6. Client impact

The current client renderer already does:

```javascript
case 'thinking':
  return createThoughtBlock(role, part.text || part.thought);

case 'text':
  return createTextBlock(role, part.text, ...);
```

Therefore the new wire shape matches current rendering behavior.

The client schema keeps all non-text `data` types untouched.

The compatibility validator allows a newer client to consume old-server TextContent-shaped responses during a rolling upgrade.

## 7. Breaking source-level change

After the migration, direct imports such as:

```python
from ...attachment import TextContent
```

will fail by design.

Repository search found no production consumers other than the Gemini converter and message schema sites included in the patch.

External code importing `TextContent` must migrate to `MessageContentPart(text=...)`.

## 8. Regression gates

Run at minimum:

```bash
py -m pytest -q se/tests/architecture/test_text_content_removal_contract.py
py -m pytest -q se/tests/architecture/test_phase5_adapters.py
py -m pytest -q cl/tests/test_schema_contracts.py
```

Then run existing provider and chat suites:

```bash
py -m pytest -q se/tests/providers
py -m pytest -q se/tests/architecture/test_streaming_p0_regressions.py
py -m pytest -q se/tests/architecture/test_phase2.py
```

Live exit gate:

```text
Gemini response
→ MessageContentPart.text
→ Agent transcript
→ Gemini request
→ no {"text": None}
→ SSE delta.content remains str
→ client renders text/Markdown
```

Assertions:

- no production `TextContent` symbol remains;
- text/thinking parts carry text through `.text`;
- text/thinking `data` is `None`;
- all media parts still use their existing typed `data`;
- old TextContent-shaped payloads still deserialize;
- old code TextContent payloads become fenced Markdown;
- Gemini stream `delta.content` is a string;
- Gemini response → Agent → Gemini request round-trip never emits `{"text": None}`.

## 9. Explicitly out of scope

This migration does **not**:

- remove `MessageContentPart`;
- remove or redesign image/audio/video/document/url/file parts;
- remove tool calls or tool results;
- move citations;
- redesign reasoning;
- change `GatewayMessage.content`;
- change `GatewayStreamDelta`;
- change Session DB schema;
- change AgentRuntime;
- change CapabilityRuntime;
- fix the independent `PythonCapabilityDriver` bug where a synchronous wrapper can return an awaitable/Task.

That PythonCapabilityDriver issue remains a separate P0 and should be applied as a separate patch rather than mixed into the TextContent migration.
