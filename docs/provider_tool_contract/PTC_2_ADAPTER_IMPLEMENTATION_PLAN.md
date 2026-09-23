# PTC-2 — OpenAI / Gemini / Ollama Tool Adapter Contract

## Status

Issue: #8  
Baseline: main@31ca06ab3c9ef4cfd043c64af5d7a5649877034e  
Branch: work/ptc-2-31ca06ab  
Dependency: PTC-1 CLOSED / merged.

## 1. Purpose

PTC-2 wires the PTC-1 provider-local name/schema contract through actual
provider request and response adapters without changing canonical logical
capability identity.

The invariant is:

canonical logical ID
-> request-scoped provider name
-> provider declaration/history
-> provider tool call
-> reverse provider name
-> canonical logical ID

The name map is immutable and request-scoped. Provider API and converter
instances do not retain mutable alias state between calls.

## 2. Current provider authority

### OpenAI Chat Completions

Function tools use the native function envelope:

- type=function
- function.name
- function.description
- function.parameters

Assistant tool calls carry function.name and JSON-string arguments.
Tool-result continuation is identified by role=tool plus tool_call_id.

PTC-2 therefore:

- fixes OpenAIChats.prepare_request() to call adapt_chat_request(request=...);
- lowers neutral declarations to the native function envelope;
- applies one request-scoped alias to declaration and assistant history;
- removes neutral name/tool_name from role=tool wire messages while preserving
  tool_call_id;
- reverses provider aliases in normal and streaming responses;
- preserves raw provider response evidence separately from canonical decoding.

Reference:
https://developers.openai.com/api/docs/guides/function-calling

## 3. Gemini declaration/call asymmetry

The current Gemini GenerateContent API documents a broader FunctionDeclaration
name grammar than FunctionCall/FunctionResponse:

- FunctionDeclaration may include underscores, colons, dots, and dashes;
- FunctionCall and FunctionResponse permit letters, digits, underscores, and
  dashes only, max 128.

Therefore a dotted logical ID cannot safely remain dotted for a complete
declaration -> call -> result round trip.

PTC-2 applies the same PTC-1 alias to:

- function_declarations[].name;
- assistant history functionCall.name;
- tool-result history functionResponse.name;
- returned FunctionCall names before Gateway execution sees them;
- streaming FunctionCall names before Gateway execution sees them.

Reference:
https://ai.google.dev/api/generate-content

## 4. Ollama native tool contract

Current Ollama tool-calling documentation uses:

- tools[].type=function;
- tools[].function.{name,description,parameters};
- assistant tool_calls[].function.arguments as a JSON object;
- role=tool + tool_name + content for tool-result continuation.

The documented wire examples do not include tool_call_id on role=tool.
PTC-2 therefore preserves canonical tool_call_id in Gateway/Agent history but
does not invent an undocumented Ollama wire field.

PTC-2:

- lowers neutral definitions to Ollama ToolDefinition envelope;
- parses neutral/OpenAI-style JSON-string assistant arguments to objects;
- rejects malformed/non-object arguments before outbound transport;
- maps neutral role=tool name to Ollama tool_name;
- normalizes returned arguments to the Gateway JSON-string contract.

Reference:
https://docs.ollama.com/capabilities/tool-calling

## 5. Request-scoped context

se/src/provider/core/tool_request_context.py collects unique logical names from:

- tool declarations;
- assistant tool_calls / legacy function_call;
- role=tool/tool_result/function history;
- embedded tool_result/function_response content.

Duplicate references to the same logical ID are deduplicated before PTC-1
bijection construction. Collisions between distinct logical IDs still fail
closed in PTC-1.

## 6. Regression matrix

PTC-2 must prove:

1. one logical name referenced in declarations/history does not look like a
   duplicate declaration;
2. OpenAI native envelope, alias, JSON-string arguments, and tool_call_id
   continuation are correct;
3. the OpenAI wrong-keyword prepare_request regression is closed;
4. OpenAI response decoding restores logical identity while raw evidence keeps
   provider identity;
5. OpenAI API request and response use the same request-scoped map;
6. Gemini declaration/functionCall/functionResponse use exactly one alias;
7. Gemini normal and stream responses restore the dotted logical ID;
8. unknown Gemini provider tool names fail closed;
9. repeated Gemini converter calls cannot leak alias state across requests;
10. Ollama declarations use the native function envelope;
11. Ollama assistant history arguments are objects;
12. Ollama role=tool uses tool_name and does not invent tool_call_id;
13. malformed Ollama JSON-string arguments fail closed;
14. Ollama response returns Gateway JSON-string arguments and logical name;
15. canonical input bodies are not mutated.

## 7. AE-R10 boundary

At PTC-2 claim time AE-R10-B owns retry normalization in:

- se/src/provider/exceptions.py
- se/src/provider/policies/retry.py

PTC-2 does not edit either path and does not alter retryability, Retry-After,
backoff, deadlines, provider availability, fallback ordering, or shared retry
budgets.

PTC-3 remains closed until a fresh overlap audit with the then-current AE-R10
state.

## 8. Exit gate

PTC-2 closes only when:

- focused PTC-2 regressions pass;
- affected architecture regressions pass;
- Linux full-suite CI is green;
- Windows client-contract CI is green;
- diff audit shows no AE-R10-owned file changed;
- Issue #8 records exact candidate SHA and CI evidence;
- PTC-3 receives a new explicit claim after a fresh Issue #14 overlap audit.
