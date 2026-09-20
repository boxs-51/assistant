import asyncio
import concurrent.futures
import os
import shutil
import threading
import time
from typing import Any, Dict, List, Optional

import anyio
from anyio.from_thread import BlockingPortal
from mcp import Client, ClientSession, StdioServerParameters


class MCPClientAdapter:
    """Own one MCP stdio session on the MCPManager event loop."""

    def __init__(
        self,
        server_name: str,
        server_config: dict,
        *,
        manager: "MCPManager",
    ) -> None:
        self.server_name = server_name
        self.config = dict(server_config)
        self._manager = manager

        self.client: Optional[Client] = None
        self.session: Optional[ClientSession] = None
        self._owner_loop: Optional[asyncio.AbstractEventLoop] = None
        self._lifecycle_task: Optional[asyncio.Task] = None
        self._ready_future: Optional[asyncio.Future] = None
        self._close_event: Optional[asyncio.Event] = None
        self._inflight: set[asyncio.Task] = set()
        self._mapped_tools: Dict[str, Dict[str, Any]] = {}
        self._runtime_error: Optional[BaseException] = None
        self._startup_phase = "NEW"

    def _build_server_params(self) -> StdioServerParameters:
        env = os.environ.copy()
        if "env" in self.config:
            env.update(self.config["env"])

        cmd = self.config["command"]
        resolved_cmd = shutil.which(cmd)
        if resolved_cmd:
            cmd = resolved_cmd
        elif os.name == "nt" and not cmd.endswith((".cmd", ".exe", ".bat")):
            for ext in (".cmd", ".exe", ".bat"):
                candidate = shutil.which(cmd + ext)
                if candidate:
                    cmd = candidate
                    break

        return StdioServerParameters(
            command=cmd,
            args=self.config.get("args", []),
            env=env,
            cwd=self.config.get("cwd"),
        )

    def _assert_owner_loop(self) -> asyncio.AbstractEventLoop:
        loop = asyncio.get_running_loop()
        if not self._manager._owns_loop(loop):
            raise RuntimeError(
                f"MCP adapter '{self.server_name}' must run on the MCPManager owner loop."
            )
        if self._owner_loop is not None and loop is not self._owner_loop:
            raise RuntimeError(
                f"MCP adapter '{self.server_name}' cannot cross event-loop ownership."
            )
        return loop

    @staticmethod
    def _tool_input_schema(tool: Any) -> Dict[str, Any]:
        schema = getattr(tool, "input_schema", None)
        if schema is None:
            schema = getattr(tool, "inputSchema", None)
        return dict(schema or {"type": "object"})

    def _map_tools(self, tools: Any) -> Dict[str, Dict[str, Any]]:
        mapped_tools: Dict[str, Dict[str, Any]] = {}
        for tool in tools:
            namespaced_name = f"mcp__{self.server_name}__{tool.name}"
            mapped_tools[namespaced_name] = {
                "metadata": {
                    "name": namespaced_name,
                    "description": (
                        f"[{self.server_name.upper()} MCP] "
                        f"{getattr(tool, 'description', None) or ''}"
                    ),
                    "base_risk": self.config.get("base_risk", "MEDIUM"),
                    "parameters": self._tool_input_schema(tool),
                },
                "func": self._create_execution_handler(tool.name),
                "is_mcp": True,
            }
        return mapped_tools

    async def start(self) -> Dict[str, Dict[str, Any]]:
        loop = self._assert_owner_loop()
        if self._lifecycle_task is not None:
            if self._lifecycle_task.done():
                raise RuntimeError(f"MCP adapter '{self.server_name}' is not restartable.")
            return dict(self._mapped_tools)

        self._owner_loop = loop
        self._ready_future = loop.create_future()
        self._close_event = asyncio.Event()
        self._lifecycle_task = loop.create_task(
            self._lifecycle(),
            name=f"mcp-client-{self.server_name}",
        )
        self._lifecycle_task.add_done_callback(self._observe_lifecycle)

        try:
            mapped = await self._ready_future
        except BaseException:
            # A caller cancellation/timeout is not allowed to strand a partially
            # entered stdio context.  Cancel the lifecycle task so its own
            # finally/async-with stack unwinds in the same task that entered it.
            lifecycle = self._lifecycle_task
            if lifecycle is not None and not lifecycle.done():
                lifecycle.cancel()
            if lifecycle is not None:
                await asyncio.gather(lifecycle, return_exceptions=True)
            raise
        return dict(mapped)

    async def connect(self) -> None:
        """Compatibility facade; manager-owned callers should use start()."""
        await self.start()

    async def _lifecycle(self) -> None:
        self._assert_owner_loop()
        ready = self._ready_future
        close_event = self._close_event
        if ready is None or close_event is None:
            raise RuntimeError("MCP adapter lifecycle was not initialized.")

        params = self._build_server_params()
        self._startup_phase = "SPAWNING_STDIO"
        try:
            # MCP SDK 2.x has two protocol eras.  The first-class Client owns
            # negotiation: it probes server/discover for modern servers and
            # falls back to the legacy initialize handshake when necessary.
            # The transport and Client contexts are still entered/exited by
            # this same long-lived lifecycle task, preserving AnyIO ownership.
            self._startup_phase = "NEGOTIATING"
            async with Client(params, mode="auto", cache=None) as client:
                self.client = client
                self.session = client.session
                self._startup_phase = "DISCOVERING_TOOLS"
                listed = await client.list_tools()
                self._mapped_tools = self._map_tools(listed.tools)
                self._startup_phase = "READY"
                if not ready.done():
                    ready.set_result(dict(self._mapped_tools))
                await close_event.wait()
        except asyncio.CancelledError:
            if not ready.done():
                ready.cancel()
            raise
        except BaseException as exc:
            self._runtime_error = exc
            if not ready.done():
                ready.set_exception(exc)
            raise
        finally:
            self.session = None
            self.client = None

    @staticmethod
    def _observe_lifecycle(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        try:
            task.exception()
        except asyncio.CancelledError:
            pass

    async def get_mapped_tools(self) -> Dict[str, Dict[str, Any]]:
        self._assert_owner_loop()
        if self.client is None:
            raise RuntimeError(f"MCP adapter '{self.server_name}' is not connected.")
        listed = await self.client.list_tools()
        self._mapped_tools = self._map_tools(listed.tools)
        return dict(self._mapped_tools)

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        self._assert_owner_loop()
        if self.client is None:
            detail = (
                f": {self._runtime_error}"
                if self._runtime_error is not None
                else ""
            )
            raise RuntimeError(
                f"MCP adapter '{self.server_name}' is not connected{detail}."
            )

        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("MCP call has no owning asyncio task.")
        self._inflight.add(task)
        try:
            result = await self.client.call_tool(tool_name, arguments)
        finally:
            self._inflight.discard(task)

        output_texts = [
            content.text
            for content in result.content
            if getattr(content, "type", None) == "text"
            and hasattr(content, "text")
        ]
        return "\n".join(output_texts)

    async def close(self) -> None:
        self._assert_owner_loop()
        lifecycle = self._lifecycle_task
        if lifecycle is None:
            self.session = None
            self.client = None
            return

        current = asyncio.current_task()
        inflight = [task for task in self._inflight if task is not current]
        for task in inflight:
            task.cancel()
        if inflight:
            await asyncio.gather(*inflight, return_exceptions=True)

        ready = self._ready_future
        if ready is not None and not ready.done():
            # Startup is still inside initialize/discovery and cannot observe the
            # close event yet.  Cancellation forces the lifecycle task to unwind
            # its stdio/session contexts in-place.
            if not lifecycle.done():
                lifecycle.cancel()
        else:
            close_event = self._close_event
            if close_event is not None:
                close_event.set()

        if lifecycle is not current:
            await asyncio.gather(lifecycle, return_exceptions=True)

        self.session = None
        self.client = None
        self._mapped_tools = {}

    def _create_execution_handler(self, original_tool_name: str):
        def handler(
            *,
            invocation_id=None,
            connection_id=None,
            session_id=None,
            cancel_event=None,
            **kwargs,
        ) -> str:
            # Correlation values are CL runtime metadata, not MCP tool
            # arguments.  Keep only the model-supplied tool arguments.
            del invocation_id, connection_id, session_id
            return self._manager.invoke(
                self,
                original_tool_name,
                kwargs,
                cancel_event=cancel_event,
            )

        return handler


class MCPManager:
    """Own one AnyIO blocking portal for every client-side MCP stdio resource."""

    def __init__(
        self,
        *,
        startup_timeout_seconds: float = 15.0,
        shutdown_timeout_seconds: float = 15.0,
        cancellation_poll_seconds: float = 0.05,
    ) -> None:
        self.adapters: List[MCPClientAdapter] = []
        self.startup_timeout_seconds = startup_timeout_seconds
        self.shutdown_timeout_seconds = shutdown_timeout_seconds
        self.cancellation_poll_seconds = cancellation_poll_seconds

        self._state_lock = threading.RLock()
        self._lifecycle_lock = threading.Lock()
        self._owner_ready = threading.Event()
        self._portal_starting = False
        self._portal_start_error: Optional[BaseException] = None
        self._portal: Optional[BlockingPortal] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._owner_thread_id: Optional[int] = None
        self._owner_stop = threading.Event()
        self._owner_force_stop = threading.Event()
        self._closing = False
        self._closed = False
        self._active_calls: Dict[
            concurrent.futures.Future,
            Optional[threading.Event],
        ] = {}

    def _owns_loop(self, loop: asyncio.AbstractEventLoop) -> bool:
        with self._state_lock:
            return self._loop is loop and self._portal is not None and not self._closed

    @property
    def owner_thread_id(self) -> Optional[int]:
        with self._state_lock:
            return self._owner_thread_id

    @property
    def has_active_adapters(self) -> bool:
        with self._state_lock:
            return bool(self.adapters)

    @property
    def is_running(self) -> bool:
        with self._state_lock:
            loop = self._loop
            return bool(
                self._portal is not None
                and loop is not None
                and not loop.is_closed()
                and self._owner_thread_id is not None
                and not self._closed
            )

    def _portal_thread_main(self) -> None:
        async def run_owner() -> None:
            try:
                async with BlockingPortal() as portal:
                    loop = asyncio.get_running_loop()
                    with self._state_lock:
                        if self._closing or self._closed:
                            raise RuntimeError(
                                "MCPManager stopped while its owner portal was starting."
                            )
                        self._portal = portal
                        self._loop = loop
                        self._owner_thread_id = threading.get_ident()
                        self._portal_starting = False
                        self._owner_ready.set()

                    # A plain threading.Event is the shutdown control plane.  The
                    # caller never has to synchronously enter the portal just to
                    # stop it, so shutdown remains bounded even if the owner loop
                    # is unhealthy.
                    await anyio.to_thread.run_sync(self._owner_stop.wait)
                    if self._owner_force_stop.is_set():
                        await portal.stop(cancel_remaining=True)
            except BaseException as exc:
                with self._state_lock:
                    self._portal_start_error = exc
                    self._portal_starting = False
                    self._owner_ready.set()
                raise
            finally:
                with self._state_lock:
                    self._portal = None
                    self._loop = None
                    self._owner_thread_id = None
                    self._portal_starting = False
                    self._owner_ready.set()

        try:
            anyio.run(run_owner, backend="asyncio")
        except BaseException:
            # The exact exception is already published through
            # _portal_start_error.  Never let a background-thread traceback be
            # the only signal available to the synchronous manager boundary.
            pass

    def _ensure_owner_loop(self) -> asyncio.AbstractEventLoop:
        should_start = False
        thread: Optional[threading.Thread] = None
        with self._state_lock:
            if self._closed:
                raise RuntimeError("MCPManager is closed.")
            if self._closing:
                raise RuntimeError("MCPManager is shutting down.")

            existing = self._thread
            if (
                self._portal is not None
                and self._loop is not None
                and existing is not None
                and existing.is_alive()
            ):
                return self._loop

            if not self._portal_starting:
                self._portal_starting = True
                self._portal_start_error = None
                self._owner_ready.clear()
                self._owner_stop.clear()
                self._owner_force_stop.clear()
                thread = threading.Thread(
                    target=self._portal_thread_main,
                    name="mcp-client-event-loop",
                    daemon=True,
                )
                self._thread = thread
                should_start = True

        if should_start:
            assert thread is not None
            thread.start()

        if not self._owner_ready.wait(self.startup_timeout_seconds):
            self._owner_force_stop.set()
            self._owner_stop.set()
            if thread is not None:
                thread.join(timeout=self.startup_timeout_seconds)
            raise TimeoutError("Timed out starting MCP owner event loop.")

        with self._state_lock:
            if self._portal_start_error is not None:
                raise RuntimeError(
                    "MCP owner event loop failed to start."
                ) from self._portal_start_error
            loop = self._loop
            owner_thread = self._thread
            if (
                self._portal is None
                or loop is None
                or self._owner_thread_id is None
                or owner_thread is None
                or not owner_thread.is_alive()
            ):
                raise RuntimeError("MCP owner event loop failed to start.")
            return loop

    def _submit(
        self,
        coroutine_factory,
        *,
        allow_closing: bool = False,
    ) -> concurrent.futures.Future:
        if not allow_closing:
            self._ensure_owner_loop()

        with self._state_lock:
            if self._closed:
                raise RuntimeError("MCPManager is closed.")
            if self._closing and not allow_closing:
                raise RuntimeError("MCPManager is shutting down.")

            portal = self._portal
            owner_thread_id = self._owner_thread_id
            if portal is None or self._loop is None or owner_thread_id is None:
                raise RuntimeError("MCP owner event loop is not running.")
            if threading.get_ident() == owner_thread_id:
                raise RuntimeError(
                    "Synchronous MCP bridge cannot block the MCP owner thread."
                )

        async def runner():
            return await coroutine_factory()

        return portal.start_task_soon(runner)

    def _close_adapter_after_failed_start(self, adapter: MCPClientAdapter) -> None:
        try:
            future = self._submit(lambda: adapter.close())
            future.result(timeout=self.shutdown_timeout_seconds)
        except Exception:
            # The portal shutdown remains the final cleanup authority.
            pass

    def load_mcp_servers(
        self,
        mcp_servers_config: Dict[str, Any],
    ) -> Dict[str, Dict[str, Any]]:
        with self._lifecycle_lock:
            if self.adapters:
                raise RuntimeError(
                    "MCP reload is intentionally disabled until transactional "
                    "registry reload is implemented."
                )

            mcp_tools: Dict[str, Dict[str, Any]] = {}
            for server_name, server_config in mcp_servers_config.items():
                if not server_config.get("enabled", True):
                    print(f"⏸️ [MCP Disabled]: {server_name}")
                    continue

                adapter = MCPClientAdapter(
                    server_name,
                    server_config,
                    manager=self,
                )
                future: Optional[concurrent.futures.Future] = None
                try:
                    future = self._submit(lambda: adapter.start())
                    server_tools = future.result(
                        timeout=self.startup_timeout_seconds
                    )
                except Exception as exc:
                    if future is not None:
                        future.cancel()
                    self._close_adapter_after_failed_start(adapter)
                    print(
                        f"❌ Lỗi kết nối MCP Server [{server_name}]: "
                        f"{type(exc).__name__}: {exc!r} "
                        f"(phase={adapter._startup_phase})"
                    )
                    continue

                mcp_tools.update(server_tools)
                with self._state_lock:
                    self.adapters.append(adapter)
                print(
                    f"🔌 [MCP Connected]: {server_name} "
                    f"({len(server_tools)} tools)"
                )

            return mcp_tools

    def invoke(
        self,
        adapter: MCPClientAdapter,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        cancel_event: Optional[threading.Event] = None,
        timeout_seconds: Optional[float] = None,
    ) -> str:
        future = self._submit(
            lambda: adapter.call_tool(tool_name, dict(arguments))
        )
        with self._state_lock:
            if self._closing or self._closed:
                future.cancel()
                raise RuntimeError("MCPManager is shutting down.")
            self._active_calls[future] = cancel_event

        try:
            if cancel_event is None and timeout_seconds is None:
                return future.result()

            deadline = (
                None
                if timeout_seconds is None
                else time.monotonic() + timeout_seconds
            )
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    future.cancel()
                    return ""

                wait_for = self.cancellation_poll_seconds
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        future.cancel()
                        raise TimeoutError(
                            f"MCP tool '{tool_name}' exceeded its timeout."
                        )
                    wait_for = min(wait_for, remaining)

                try:
                    return future.result(timeout=wait_for)
                except concurrent.futures.TimeoutError:
                    continue
                except concurrent.futures.CancelledError:
                    if cancel_event is not None and cancel_event.is_set():
                        return ""
                    raise RuntimeError(
                        f"MCP tool '{tool_name}' was cancelled during shutdown."
                    )
        finally:
            with self._state_lock:
                self._active_calls.pop(future, None)

    async def _close_all(self) -> None:
        with self._state_lock:
            adapters = list(self.adapters)

        results = await asyncio.gather(
            *(adapter.close() for adapter in adapters),
            return_exceptions=True,
        )
        with self._state_lock:
            self.adapters.clear()

        errors = [
            result
            for result in results
            if isinstance(result, BaseException)
            and not isinstance(result, asyncio.CancelledError)
        ]
        if errors:
            raise RuntimeError(
                "One or more MCP adapters failed to close cleanly."
            ) from errors[0]

    def shutdown(self, *, timeout_seconds: Optional[float] = None) -> None:
        timeout = (
            self.shutdown_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        started_at = time.monotonic()
        deadline = started_at + timeout
        # Reserve part of the total deadline for force-cancel + owner-thread
        # unwind.  Spending the entire budget on graceful adapter close would
        # leave zero time to reap the owner thread after a timeout.
        graceful_deadline = started_at + (timeout * 0.8)

        with self._lifecycle_lock:
            with self._state_lock:
                thread = self._thread
                if thread is None:
                    self._closed = True
                    return
                if thread is threading.current_thread():
                    raise RuntimeError(
                        "MCPManager.shutdown() cannot join its owner thread."
                    )
                if self._closed:
                    return

                self._closing = True
                active_calls = list(self._active_calls.items())

            for future, cancel_event in active_calls:
                if cancel_event is not None:
                    cancel_event.set()
                future.cancel()

            close_error: Optional[BaseException] = None
            close_future: Optional[concurrent.futures.Future] = None
            try:
                remaining = max(0.0, graceful_deadline - time.monotonic())
                if remaining <= 0:
                    raise TimeoutError("MCP graceful shutdown deadline expired before close.")
                close_future = self._submit(
                    self._close_all,
                    allow_closing=True,
                )
                close_future.result(timeout=remaining)
            except BaseException as exc:
                close_error = exc
                if close_future is not None:
                    close_future.cancel()
                self._owner_force_stop.set()
            finally:
                # Never call BlockingPortal.__exit__ from this thread.  Its
                # helper owns an unbounded thread.join().  Signal the manager-
                # owned owner thread instead, then apply our own deadline.
                self._owner_stop.set()

            remaining = max(0.0, deadline - time.monotonic())
            thread.join(timeout=remaining)
            thread_alive = thread.is_alive()

            if thread_alive:
                with self._state_lock:
                    self._closing = True
                    self._closed = False
                raise RuntimeError(
                    "MCP owner thread did not stop within the shutdown timeout."
                ) from close_error

            with self._state_lock:
                self._thread = None
                self._portal = None
                self._loop = None
                self._owner_thread_id = None
                self._portal_starting = False
                self._portal_start_error = None
                self._owner_ready.clear()
                self._owner_stop.clear()
                self._owner_force_stop.clear()
                self.adapters.clear()
                self._active_calls.clear()
                self._closing = False
                self._closed = True

            if close_error is not None:
                raise RuntimeError(
                    "MCP resources required forced portal shutdown."
                ) from close_error