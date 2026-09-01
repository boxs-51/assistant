from pathlib import Path

patch = r'''diff --git a/src/runtimes/agent/tool_execution/coordinator.py b/src/runtimes/agent/tool_execution/coordinator.py
--- a/src/runtimes/agent/tool_execution/coordinator.py
+++ b/src/runtimes/agent/tool_execution/coordinator.py
@@ -16,7 +16,7 @@ RetryDecider = Callable[[ToolExecutionResult, int], bool]

 @dataclass
 class _ExecutionEntry:
     task: asyncio.Task[ToolExecutionResult]
     fingerprint: str
+    waiters: int = 0
+    cancel_requested: bool = False


 @dataclass(frozen=True)
@@ -74,31 +76,58 @@ class AgentToolExecutionCoordinator(ToolExecutionPort):
         key = (request.execution_id, request.invocation_id)
         fingerprint = self._request_fingerprint(request)

-        async with self._ledger_lock:
-            completed = self._completed.get(key)
-            if completed is not None:
-                if completed.fingerprint != fingerprint:
-                    raise ValueError(
-                        "Conflicting request for existing invocation_id."
-                    )
-                return completed.result
-
-            existing = self._inflight.get(key)
-            if existing is not None:
-                if existing.fingerprint != fingerprint:
-                    raise ValueError(
-                        "Conflicting request for in-flight invocation_id."
-                    )
-                task = existing.task
-            else:
-                task = asyncio.create_task(
-                    self._execute_once_or_retry(context, request),
-                    name=(
-                        f"tool:{request.execution_id}:"
-                        f"{request.invocation_id}"
-                    ),
-                )
-                self._inflight[key] = _ExecutionEntry(
-                    task=task,
-                    fingerprint=fingerprint,
-                )
+        while True:
+            cleanup_task: asyncio.Task[ToolExecutionResult] | None = None
+
+            async with self._ledger_lock:
+                completed = self._completed.get(key)
+                if completed is not None:
+                    if completed.fingerprint != fingerprint:
+                        raise ValueError(
+                            "Conflicting request for existing invocation_id."
+                        )
+                    return completed.result
+
+                existing = self._inflight.get(key)
+                if existing is not None:
+                    if existing.fingerprint != fingerprint:
+                        raise ValueError(
+                            "Conflicting request for in-flight invocation_id."
+                        )
+
+                    if existing.cancel_requested:
+                        cleanup_task = existing.task
+                    else:
+                        existing.waiters += 1
+                        task = existing.task
+                else:
+                    task = asyncio.create_task(
+                        self._execute_once_or_retry(context, request),
+                        name=(
+                            f"tool:{request.execution_id}:"
+                            f"{request.invocation_id}"
+                        ),
+                    )
+                    self._inflight[key] = _ExecutionEntry(
+                        task=task,
+                        fingerprint=fingerprint,
+                        waiters=1,
+                    )
+
+            if cleanup_task is None:
+                break
+
+            await asyncio.gather(
+                cleanup_task,
+                return_exceptions=True,
+            )
+
+            async with self._ledger_lock:
+                current = self._inflight.get(key)
+                if current is not None and current.task is cleanup_task:
+                    self._inflight.pop(key, None)
 
         try:
             result = await self._await_with_cancellation(
                 context,
                 task,
             )
         except BaseException:
+            cancel_task = False
             async with self._ledger_lock:
                 current = self._inflight.get(key)
                 if current is not None and current.task is task:
-                    self._inflight.pop(key, None)
+                    current.waiters -= 1
+                    if current.waiters <= 0:
+                        current.cancel_requested = True
+                        if not task.done():
+                            task.cancel()
+                            cancel_task = True
+                        else:
+                            self._inflight.pop(key, None)
+
+            if cancel_task:
+                await asyncio.gather(
+                    task,
+                    return_exceptions=True,
+                )
             raise

         async with self._ledger_lock:
             current = self._inflight.get(key)
             if current is not None and current.task is task:
-                self._inflight.pop(key, None)
-                self._completed[key] = _CompletedEntry(
-                    result=result,
-                    fingerprint=fingerprint,
-                )
+                current.waiters -= 1
+                if task.done():
+                    self._inflight.pop(key, None)
+                    self._completed[key] = _CompletedEntry(
+                        result=result,
+                        fingerprint=fingerprint,
+                    )

         return result

@@ -153,25 +182,33 @@ class AgentToolExecutionCoordinator(ToolExecutionPort):
         request: ToolExecutionRequest,
     ) -> ToolExecutionResult:
         attempt = 1
+        max_retry_attempts = context.limits.max_retry_attempts
+        if max_retry_attempts < 0:
+            raise ValueError("max_retry_attempts must be >= 0.")
+
+        execution_max_attempts = 1 + max_retry_attempts
+        if self._max_attempts is not None:
+            execution_max_attempts = min(
+                execution_max_attempts,
+                self._max_attempts,
+            )

         while True:
             context.ensure_active()

             result = await self._executor.execute(
                 context,
@@ -181,12 +218,8 @@ class AgentToolExecutionCoordinator(ToolExecutionPort):
                 return self._with_attempt(result, attempt)

-            if self._max_attempts is not None:
-                if attempt >= self._max_attempts:
-                    return self._with_attempt(result, attempt)
-            else:
-                if (
-                    context.limits.max_retry_attempts < 1
-                    or attempt >= context.limits.max_retry_attempts + 1
-                ):
-                    return self._with_attempt(result, attempt)
+            if attempt >= execution_max_attempts:
+                return self._with_attempt(result, attempt)

             if not self._retry_decider(result, attempt):
                 return self._with_attempt(result, attempt)

diff --git a/tests/architecture/test_phase5_tool_execution_coordinator.py b/tests/architecture/test_phase5_tool_execution_coordinator.py
--- a/tests/architecture/test_phase5_tool_execution_coordinator.py
+++ b/tests/architecture/test_phase5_tool_execution_coordinator.py
@@ -245,6 +245,7 @@ async def test_coordinator_retry_hook_is_bounded_and_opt_in():
     coordinator = AgentToolExecutionCoordinator(
         Executor(),
         retry_decider=lambda result, attempt: True,
         max_attempts=3,
     )

     result = await coordinator.execute(context, req)
-    assert attempts == 3
+    assert attempts == 2
     assert result.retryable is True
-    assert result.metadata["attempt"] == 3
+    assert result.metadata["attempt"] == 2
+    assert context.retry_attempts_used == 1
@@ -351,6 +352,101 @@ async def test_coordinator_cancellation_cancels_downstream():
     assert cancelled.is_set()


+@pytest.mark.asyncio
+async def test_coordinator_caller_cancellation_does_not_cancel_shared_execution():
+    context = make_context()
+    req = request(context, "call-1", "tool.a")
+
+    started = asyncio.Event()
+    finished = asyncio.Event()
+
+    class Executor:
+        async def execute(self, context, request):
+            started.set()
+            try:
+                await asyncio.sleep(10)
+            except asyncio.CancelledError:
+                finished.set()
+                raise
+            return result_for(request)
+
+    coordinator = AgentToolExecutionCoordinator(Executor())
+
+    first = asyncio.create_task(coordinator.execute(context, req))
+    await started.wait()
+
+    second = asyncio.create_task(coordinator.execute(context, req))
+    first.cancel()
+
+    with pytest.raises(asyncio.CancelledError):
+        await first
+
+    assert not finished.is_set()
+
+    result = await asyncio.wait_for(second, timeout=1)
+    assert result.tool_call_id == req.tool_call_id
+    assert not finished.is_set()
+
+
+@pytest.mark.asyncio
+async def test_coordinator_cancels_shared_execution_when_last_waiter_detaches():
+    context = make_context()
+    req = request(context, "call-1", "tool.a")
+
+    started = asyncio.Event()
+    cancelled = asyncio.Event()
+
+    class Executor:
+        async def execute(self, context, request):
+            started.set()
+            try:
+                await asyncio.sleep(10)
+            except asyncio.CancelledError:
+                cancelled.set()
+                raise
+
+    coordinator = AgentToolExecutionCoordinator(Executor())
+
+    first = asyncio.create_task(coordinator.execute(context, req))
+    second = asyncio.create_task(coordinator.execute(context, req))
+    await started.wait()
+
+    first.cancel()
+    with pytest.raises(asyncio.CancelledError):
+        await first
+
+    assert not cancelled.is_set()
+
+    second.cancel()
+    with pytest.raises(asyncio.CancelledError):
+        await second
+
+    await asyncio.wait_for(
+        coordinator._wait_for_task_cleanup(context, req),
+        timeout=1,
+    )
+
+    assert cancelled.is_set()
+    assert not coordinator._inflight
+
+
+@pytest.mark.asyncio
+async def test_coordinator_new_caller_waits_for_cancelled_shared_task_before_redispach():
+    context = make_context()
+    req = request(context, "call-1", "tool.a")
+
+    started = asyncio.Event()
+    finished = asyncio.Event()
+    active = 0
+    peak_active = 0
+    calls = 0
+
+    class Executor:
+        async def execute(self, context, request):
+            nonlocal active, peak_active, calls
+            calls += 1
+            active += 1
+            peak_active = max(peak_active, active)
+            started.set()
+            try:
+                if calls == 1:
+                    await asyncio.sleep(10)
+                return result_for(request)
+            except asyncio.CancelledError:
+                finished.set()
+                raise
+            finally:
+                active -= 1
+
+    coordinator = AgentToolExecutionCoordinator(Executor())
+
+    first = asyncio.create_task(coordinator.execute(context, req))
+    second = asyncio.create_task(coordinator.execute(context, req))
+    await started.wait()
+
+    first.cancel()
+    second.cancel()
+
+    with pytest.raises(asyncio.CancelledError):
+        await first
+    with pytest.raises(asyncio.CancelledError):
+        await second
+
+    third = asyncio.create_task(coordinator.execute(context, req))
+    result = await asyncio.wait_for(third, timeout=1)
+
+    assert result.tool_call_id == req.tool_call_id
+    assert calls == 2
+    assert peak_active == 1
+    assert finished.is_set()
+
+
+@pytest.mark.asyncio
+async def test_coordinator_retry_budget_is_hard_ceiling_over_local_max_attempts():
+    context = make_context()
+    context.limits.max_retry_attempts = 1
+    req = request(context, "call-1", "tool.a")
+    attempts = 0
+
+    class Executor:
+        async def execute(self, context, request):
+            nonlocal attempts
+            attempts += 1
+            return ToolExecutionResult(
+                execution_id=request.execution_id,
+                iteration=request.iteration,
+                invocation_id=request.invocation_id,
+                tool_call_id=request.tool_call_id,
+                capability_id=request.capability_id,
+                success=False,
+                error_code="CAPABILITY_TIMEOUT",
+                retryable=True,
+            )
+
+    coordinator = AgentToolExecutionCoordinator(
+        Executor(),
+        retry_decider=lambda result, attempt: True,
+        max_attempts=3,
+    )
+
+    result = await coordinator.execute(context, req)
+
+    assert attempts == 2
+    assert context.retry_attempts_used == 1
+    assert result.metadata["attempt"] == 2
+
+
+@pytest.mark.asyncio
+async def test_coordinator_retry_budget_zero_disables_retry_even_with_max_attempts_override():
+    context = make_context()
+    context.limits.max_retry_attempts = 0
+    req = request(context, "call-1", "tool.a")
+    attempts = 0
+
+    class Executor:
+        async def execute(self, context, request):
+            nonlocal attempts
+            attempts += 1
+            return ToolExecutionResult(
+                execution_id=request.execution_id,
+                iteration=request.iteration,
+                invocation_id=request.invocation_id,
+                tool_call_id=request.tool_call_id,
+                capability_id=request.capability_id,
+                success=False,
+                error_code="CAPABILITY_TIMEOUT",
+                retryable=True,
+            )
+
+    coordinator = AgentToolExecutionCoordinator(
+        Executor(),
+        retry_decider=lambda result, attempt: True,
+        max_attempts=3,
+    )
+
+    result = await coordinator.execute(context, req)
+
+    assert attempts == 1
+    assert context.retry_attempts_used == 0
+    assert result.metadata["attempt"] == 1
+
+
 @pytest.mark.asyncio
 async def test_coordinator_reuses_completed_invocation():
     context = make_context()
@@ -379,6 +475,7 @@ async def test_coordinator_reuses_completed_invocation():
 
     assert calls == 1
     assert first == second
+
+
 @pytest.mark.asyncio
 async def test_coordinator_rejects_conflicting_reuse_of_invocation_id():
     context = make_context()
diff --git a/tests/architecture/test_phase5_tool_execution_coordinator.py b/tests/architecture/test_phase5_tool_execution_coordinator.py
@@ -0,0 +1,13 @@
+@@
+async def test_coordinator_waits_for_inflight_cleanup_before_reusing_cancelled_invocation():
+    context = make_context()
+    req = request(context, "call-1", "tool.a")
+
+    # This test intentionally observes the private ledger because the race
+    # protection is an internal coordinator invariant.
+    ...
'''
# The patch above intentionally uses a helper referenced by a test; provide the complete
# corrected version below rather than leaving an incomplete patch artifact.
patch = patch.replace(
'''        await asyncio.wait_for(
        coordinator._wait_for_task_cleanup(context, req),
        timeout=1,
    )
''',
'''        await asyncio.sleep(0)
''',
)
# Replace the final accidental placeholder hunk with nothing.
patch = patch.replace(
'''diff --git a/tests/architecture/test_phase5_tool_execution_coordinator.py b/tests/architecture/test_phase5_tool_execution_coordinator.py
@@ -0,0 +1,13 @@
+@@
+async def test_coordinator_waits_for_inflight_cleanup_before_reusing_cancelled_invocation():
+    context = make_context()
+    req = request(context, "call-1", "tool.a")
+
+    # This test intentionally observes the private ledger because the race
+    # protection is an internal coordinator invariant.
+    ...
''', ""
)
path = Path("docs/phase5/phase5_6_fix_v2_2.patch")
path.write_text(patch, encoding="utf-8")
print(path)
