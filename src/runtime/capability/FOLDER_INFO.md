# Capability Runtime

## Purpose
This module is responsible for managing, dispatching, and executing all system "capabilities". A capability is a generalized unit of action, which can be a `Tool`, a `Skill`, a `Workflow`, a `Plugin`, or a call to an external `Agent`.

This runtime acts as a replacement for the older `GatewayToolManager`, providing a more extensible and abstract way to handle system actions.

## Key Components

- **`CapabilityRegistry`**: Discovers and registers all available capabilities from different sources.
- **`CapabilitySession`**: Manages the context and state for a sequence of capability executions.
- **`CapabilityDispatcher`**: The core component that receives a request and routes it to the correct capability driver for execution.
- **`CapabilityDriver`**: An interface or base class for different types of capability executors (e.g., `ToolDriver`, `SkillDriver`).

## Flow
1. A request to execute an action arrives at the `CapabilityDispatcher`.
2. The dispatcher uses the `CapabilityRegistry` to find the appropriate driver for the requested capability.
3. The dispatcher invokes the driver, which executes the capability.
4. The result is returned to the caller.
