import asyncio
import logging
from typing import Dict, Any, Callable, Optional
from ..schemas.runtime.runtime import RuntimeCommand, RuntimeEvent
from .lock import DistributedSessionLock

logger = logging.getLogger("SessionKernel")

class SessionActor:
    def __init__(self, session_id: str, event_bus_publish_fn: Callable, lock_manager: DistributedSessionLock):
        self.session_id = session_id
        self.command_queue: asyncio.Queue[RuntimeCommand] = asyncio.Queue()
        self.state: Dict[str, Any] = {"status": "idle", "workflow_states": {}, "agent_states": {}}
        self.publish_event = event_bus_publish_fn
        self.lock = lock_manager
        self._is_running = False
        self._loop_task: Optional[asyncio.Task] = None
        self._lock_refresher_task: Optional[asyncio.Task] = None

    async def start(self) -> bool:
        """Khởi động Actor, yêu cầu phải chiếm được Distributed Lock thành công."""
        if await self.lock.acquire():
            self._is_running = True
            self._loop_task = asyncio.create_task(self._command_consumer_loop())
            self._lock_refresher_task = asyncio.create_task(self._auto_extend_lock_loop())
            logger.info(f"Session Actor {self.session_id} activated successfully.")
            return True
        logger.warning(f"Failed to activate Actor {self.session_id}. Lock occupied by another instance.")
        return False

    async def dispatch_command(self, command: RuntimeCommand):
        """Command Bus đẩy lệnh trực tiếp vào hàng đợi biệt lập này."""
        await self.command_queue.put(command)

    async def _command_consumer_loop(self):
        while self._is_running:
            try:
                command = await self.command_queue.get()
                await self._execute_command_logic(command)
                self.command_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Execution error on session {self.session_id}: {str(e)}")

    async def _execute_command_logic(self, command: RuntimeCommand):
        """Hàm xử lý cô lập đột biến trạng thái."""
        # Giả lập xử lý Command: Chạy một Automation Workflow dài hạn
        if command.command_type == "ExecuteWorkflow":
            self.state["status"] = "running_workflow"
            
            # Phát sinh Event tương ứng ra thế giới bên ngoài
            event = RuntimeEvent(
                event_type="WorkflowStarted",
                session_id=self.session_id,
                user_id=command.user_id,
                causation_id=command.command_id,
                correlation_id=command.correlation_id,
                payload={"workflow_id": command.payload.get("workflow_id")}
            )
            await self.publish_event(event)

    async def _auto_extend_lock_loop(self):
        """Vòng lặp chạy ngầm để liên tục giữ Lock nếu Session kéo dài nhiều giờ/ngày."""
        while self._is_running:
            await asyncio.sleep(10)  # Cứ sau 10 giây thực hiện gia hạn một lần
            await self.lock.extend()

    async def stop(self):
        self._is_running = False
        if self._loop_task: self._loop_task.cancel()
        if self._lock_refresher_task: self._lock_refresher_task.cancel()
        await self.lock.release()
        logger.info(f"Session Actor {self.session_id} stopped and lock released.")