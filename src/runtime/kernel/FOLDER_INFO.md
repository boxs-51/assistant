# Metadata
- **Last Scan:** 2026-07-26
- **Source Files:** 7
- **Depends On:** `pydantic`
- **Scanned Files:** `bootstrap.py`, `context.py`, `kernel.py`, `lifecycle.py`, `manifest.py`, `registry.py`, `runtime.py`

# 📂 Thư Mục: `runtime/kernel`

## 1. Architecture Decisions & Design Patterns
This directory contains the heart of the **AI Runtime Backend**. The **Kernel** is a pure orchestration layer and contains no business logic itself. Its sole responsibility is to manage the lifecycle of all other `Runtime` modules in the system in a reliable and orderly fashion.

- **Architectural Style:**
  - **Microkernel:** The `kernel.py` is the central coordinator, but the actual functionality is provided by pluggable `Runtime` modules. The kernel itself is small and focused on orchestration.
  - **Dependency Injection:** The `RuntimeContext` object acts as a dependency injector, providing each runtime with access to shared services without creating tight coupling to the kernel or other runtimes.

- **Design Patterns:**
  - **Abstract Factory (`bootstrap.py`):** The bootstrap process will act as a factory for creating and initializing runtimes based on their manifests.
  - **Registry (`registry.py`):** The `RuntimeRegistry` provides a centralized lookup for all active runtimes.
  - **State Machine (`lifecycle.py`, `kernel.py`):** The lifecycle of each runtime is managed as a formal state machine (`CREATED` -> `INITIALIZING` -> ... -> `DISPOSED`).
  - **Interface (`runtime.py`):** The abstract `Runtime` class defines a strict contract that all pluggable modules must adhere to.

## 2. Dependency & Ownership Graph
- `RuntimeKernel` **owns** the `RuntimeRegistry`.
- `RuntimeRegistry` **owns** a collection of `RuntimeRecord`s.
- `bootstrap_kernel_from_directory` **populates** the `RuntimeKernel`.
- The Kernel **creates** and **injects** a `RuntimeContext` into each `Runtime` instance upon initialization.

## 3. Thread Model & Event/Data Flow
- **Thread Model:** Fully asynchronous (`asyncio`). All lifecycle methods are `async`.
- **Data Flow (Startup):**
  1.  `bootstrap_kernel_from_directory()` is called.
  2.  It discovers runtimes, reads manifests, and resolves the dependency order (topological sort).
  3.  It iterates through the sorted list, dynamically imports each `Runtime` module, instantiates it, and calls `kernel.registry.register()`.
  4.  The application then calls `kernel.startup()`.
  5.  The `kernel` iterates through the registered runtimes (in the correct order) and calls `initialize()` and `start()` on each, updating their `LifecycleState` in the registry.

## 4. Public APIs & Configuration
- **API:** The primary API is the `RuntimeKernel` class itself (`startup()`, `shutdown()`) and the `bootstrap_kernel_from_directory()` function.
- **Configuration:** Configuration is handled by external manifest files (`runtime.yaml`), which are parsed by the `bootstrap` process.

## 5. Risk Matrix & Error-Prone Areas
- **Dynamic Imports (Medium Risk):** The bootstrap process will rely on dynamically importing code based on file paths. This can be brittle and may hide import errors until runtime.
- **Dependency Resolution (Medium Risk):** A faulty topological sort algorithm could lead to deadlocks or runtimes starting before their dependencies are ready. Thorough testing of the bootstrap logic is critical.

## 6. Technical Debt (TODO / FIXME / HACK)
- The functions in `bootstrap.py` are currently placeholders (`stubs`). The core logic for discovery, dynamic loading, and topological sorting needs to be implemented.
- `RuntimeContext` is basic. It needs to be integrated with actual shared services like configuration, logging, and an event bus.
- Error handling in the `RuntimeKernel`'s startup/shutdown sequence is basic. A more robust strategy might involve concepts like compensating transactions or a more granular failure policy.
