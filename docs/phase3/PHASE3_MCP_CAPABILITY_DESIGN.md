# Phase 3 — MCP Capability Driver

## Architectural goal

Move MCP **execution semantics** out of `src/tool/_mcp` while keeping MCP
**transport, connection lifecycle, reconnect, health and discovery cache** in
infrastructure. `CapabilityRuntime` becomes the Gateway execution boundary.

```text
MCP Server
    |
    v
infrastructure.mcp
    | transport / session / reconnect / health / discovery
    v
McpToolDescriptor
    |
    v
CapabilityRuntime.discover_mcp_capabilities()
    |
    +--> CapabilityDefinition
    +--> McpCapabilityDriver
    |
    v
CapabilityRegistry
    |
    v
CapabilityRuntime.execute_capability()
```

## Security boundary

The driver never reads tokens from `Identity.scopes`. Credentials are resolved
through an explicit `McpCredentialResolver` dependency. The default resolver
injects nothing. A later security phase can provide the real resolver without
coupling MCP infrastructure to authentication storage.

## Compatibility boundary

`src/tool/_mcp/*` is reduced to import/adaptor shims. It remains only so older
imports can survive while the rest of `src/tool` is migrated. No MCP network
or connection logic remains there.

## Invariants

1. MCP infrastructure does not import `ToolRegistry`.
2. MCP infrastructure does not perform Gateway authorization.
3. Discovery creates `McpToolDescriptor`; CapabilityRuntime creates the domain
   `CapabilityDefinition` and driver.
4. Only CapabilityRuntime invokes MCP capabilities in the new path.
5. A disconnected MCP server makes the driver unhealthy/unavailable without
   deleting the capability definition.
6. Remote MCP name and Gateway capability id are distinct and explicitly
   mapped (`metadata.mcp_tool_name`).
