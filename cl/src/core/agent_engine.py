import threading
import time
from typing import Any, Dict, List, Optional

import structlog

from ..core.execution_control import ExecutionController
from ..schemas.message import GatewayMessage, MessageContentPart
from ..schemas.request import GatewayChatRequest, RequestConfig, RequestMetadata
from ..schemas.tool import FunctionCall, GatewayToolCall
from .agent_helpers import extract_text_content, serialize_helper
from .content_processor import (
    build_tools_schema,
    merge_tool_call_delta,
    parse_text_action_fallback,
    process_attached_files,
)
from .mock_provider import MockLLMProvider
from .session_manager import AgentSessionManager
from .tool_executor import ExecutionCancelled, ExecutionContext, ToolExecutor

logger = structlog.get_logger(__name__)


class AgentEngine:
    """Agent execution coordinator chính của ứng dụng."""

    def __init__(self, registry, hitl, gateway_client, mock_mode: bool = False):
        self.registry = registry
        self.hitl = hitl
        self.gateway_client = gateway_client
        self.controller = ExecutionController()
        self.mock_mode = mock_mode

        self.mock_provider = MockLLMProvider(self.registry)
        self.tool_executor = ToolExecutor(self.registry, self.hitl)
        self.session_manager = AgentSessionManager()

        logger.info(
            "AgentEngine initialized successfully",
            registered_tools_count=(
                len(self.registry.tools) if hasattr(self.registry, "tools") else 0
            ),
            mock_mode=self.mock_mode,
        )

    def cancel_execution(self, execution_id: str) -> bool:
        return self.session_manager.cancel_execution(execution_id)

    def get_execution(self, execution_id: str) -> Optional[ExecutionContext]:
        return self.session_manager.get_execution(execution_id)

    def run_agent_session(
        self,
        session: Optional[Any] = None,
        user_input: Optional[str] = None,
        attached_files: Optional[List[str]] = None,
        provider_name: str = "gemini",
        model_name: str = "gemini-2.5-flash",
        max_steps: int = 15,
        render_cb=None,
        enable_stream: bool = False,
        mock_mode: Optional[bool] = None,
        runtime_confirm_cb=None,
        conversation_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        deadline_monotonic: Optional[float] = None,
        cancellation_event: Optional[threading.Event] = None,
    ):
        conversation_id, execution_id = self.session_manager.resolve_identity(
            session=session,
            conversation_id=conversation_id,
            execution_id=execution_id,
        )

        session = self.session_manager.get_or_create_session(
            conversation_id=conversation_id,
            session=session,
        )

        registered_conversation_id = getattr(session, "conversation_id", None)
        if registered_conversation_id and str(registered_conversation_id) != conversation_id:
            raise ValueError(
                "Session/conversation identity mismatch: "
                f"session={registered_conversation_id}, conversation={conversation_id}"
            )

        if deadline_monotonic is None and timeout_seconds is not None:
            if timeout_seconds <= 0:
                timeout_seconds = 0.0
            deadline_monotonic = time.monotonic() + float(timeout_seconds)

        execution_context = ExecutionContext(
            conversation_id=conversation_id,
            execution_id=execution_id,
            session=session,
            cancellation_event=cancellation_event,
            deadline_monotonic=deadline_monotonic,
        )

        is_mock = self.mock_mode if mock_mode is None else mock_mode
        conversation_lock = self.session_manager.get_or_create_conversation_lock(conversation_id)

        active_session_id = getattr(session, "session_id", None) or conversation_id

        logger.info(
            "Starting autonomous agent session",
            conversation_id=conversation_id,
            session_id=active_session_id,
            execution_id=execution_id,
            mock_mode=is_mock,
        )

        self.session_manager.register_execution(execution_context)

        with conversation_lock:
            self.controller.start()

            try:
                execution_context.check_cancelled()

                # 1. Tạo duy nhất prompt mới cho lượt gọi này
                content_parts = []
                if user_input and user_input.strip():
                    content_parts.append(MessageContentPart(type="text", text=user_input))

                if attached_files:
                    file_parts = process_attached_files(attached_files)
                    content_parts.extend(file_parts)

                latest_messages_to_send = []
                if content_parts:
                    new_user_msg = GatewayMessage(role="user", content=content_parts)
                    session.add_message(new_user_msg)
                    latest_messages_to_send = [new_user_msg]

                step = 0

                while step < max_steps:
                    execution_context.check_cancelled()
                    step += 1

                    logger.info(
                        "Executing ReAct Step",
                        step=step,
                        max_steps=max_steps,
                        conversation_id=conversation_id,
                        session_id=active_session_id,
                        execution_id=execution_id,
                    )

                    if not self.controller.check_and_wait_if_paused(render_cb):
                        logger.warning(
                            "Session execution paused or stopped by controller",
                            step=step,
                            execution_id=execution_id,
                        )
                        execution_context.cancellation_event.set()
                        break

                    execution_context.check_cancelled()

                    # 2. Khởi tạo Request DTO
                    request_dto = GatewayChatRequest(
                        session_id=active_session_id,
                        model=model_name,
                        messages=latest_messages_to_send,
                        config=RequestConfig(temperature=0.1, stream=enable_stream),
                        metadata=RequestMetadata(
                            user={"id": "dev_user"},
                            routing={"prefer_provider": provider_name, "type": "fallback"},
                        ),
                    )

                    # 3. Gửi Request tới Gateway
                    try:
                        execution_context.check_cancelled()
                        if is_mock:
                            response = self.mock_provider.generate_response(
                                latest_messages_to_send, step, enable_stream
                            )
                        else:
                            response = self._send_gateway_request(request_dto, execution_context)

                    except ExecutionCancelled as ex:
                        logger.warning("LLM request cancelled", execution_id=execution_id, error=str(ex))
                        if render_cb:
                            render_cb(role="system", btype="error", text=f"❌ {str(ex)}")
                        break

                    except Exception as ex:
                        logger.error("Failed to fetch LLM response", execution_id=execution_id, error=str(ex), exc_info=True)
                        if render_cb:
                            render_cb(role="system", btype="error", text=f"❌ Lỗi kết nối Gateway Client: {str(ex)}")
                        break

                    # 4. Cập nhật active_session_id
                    resp_session_id = response.get("session_id") if isinstance(response, dict) else getattr(response, "session_id", None)
                    if resp_session_id:
                        active_session_id = resp_session_id
                        if hasattr(session, "session_id"):
                            session.session_id = resp_session_id

                    # 5. Xử lý Streaming/Unary Response
                    accumulated_text = ""
                    accumulated_thought = ""
                    accumulated_tool_calls_raw = []

                    if enable_stream:
                        try:
                            for chunk in response:
                                execution_context.check_cancelled()

                                chunk_session_id = getattr(chunk, "session_id", None)
                                if chunk_session_id:
                                    active_session_id = chunk_session_id
                                    if hasattr(session, "session_id"):
                                        session.session_id = chunk_session_id

                                if not chunk.choices:
                                    continue

                                delta = chunk.choices[0].delta

                                if getattr(delta, "reasoning_content", None):
                                    accumulated_thought += delta.reasoning_content

                                if getattr(delta, "content", None):
                                    accumulated_text += extract_text_content(delta.content)

                                if getattr(delta, "tool_calls", None):
                                    merge_tool_call_delta(accumulated_tool_calls_raw, delta.tool_calls)

                                if render_cb:
                                    render_cb(
                                        role="assistant",
                                        btype="stream_content",
                                        data=serialize_helper(chunk),
                                    )

                        except ExecutionCancelled:
                            execution_context.cancellation_event.set()
                            logger.warning("LLM stream cancelled", execution_id=execution_id)
                            break

                        final_tool_calls = [
                            GatewayToolCall(
                                id=tc["id"],
                                type=tc["type"],
                                function=FunctionCall(
                                    name=tc["function"]["name"],
                                    arguments=tc["function"]["arguments"],
                                ),
                            )
                            for tc in accumulated_tool_calls_raw
                            if tc["function"]["name"]
                        ]

                        if not final_tool_calls and accumulated_text:
                            final_tool_calls = parse_text_action_fallback(accumulated_text)

                        assistant_msg = GatewayMessage(
                            role="assistant",
                            content=accumulated_text or "",
                            tool_calls=final_tool_calls or None,
                        )

                    else:
                        execution_context.check_cancelled()

                        choice = response.choices[0] if isinstance(response.choices, list) else response["choices"][0]
                        
                        if isinstance(choice, dict):
                            assistant_msg_raw = choice.get("message", {})
                            tool_calls = assistant_msg_raw.get("tool_calls")
                            raw_content = assistant_msg_raw.get("content")
                        else:
                            assistant_msg_raw = choice.message
                            tool_calls = getattr(assistant_msg_raw, "tool_calls", None)
                            raw_content = getattr(assistant_msg_raw, "content", None)

                        parsed_text = extract_text_content(raw_content)

                        assistant_msg = GatewayMessage(
                            role="assistant",
                            content=parsed_text,
                            tool_calls=tool_calls,
                        )

                        if not assistant_msg.tool_calls and parsed_text:
                            assistant_msg.tool_calls = parse_text_action_fallback(parsed_text)

                        if render_cb:
                            render_cb(
                                role="assistant",
                                btype="content",
                                data=serialize_helper(response),
                            )

                    if execution_context.cancelled:
                        break

                    session.add_message(assistant_msg)

                    if not assistant_msg.tool_calls:
                        logger.info("Goal reached or standard response completed", step=step, execution_id=execution_id)
                        break

                    execution_context.check_cancelled()

                    logger.info(
                        "Executing requested tools in parallel threads",
                        count=len(assistant_msg.tool_calls),
                        execution_id=execution_id,
                    )

                    tool_messages = self.tool_executor.execute_tools_parallel(
                        tool_calls=assistant_msg.tool_calls,
                        session=session,
                        render_cb=render_cb,
                        runtime_confirm_cb=runtime_confirm_cb,
                        execution_context=execution_context,
                    )

                    for tool_msg in tool_messages:
                        session.add_message(tool_msg)

                    latest_messages_to_send = tool_messages

                logger.info(
                    "Agent session loop terminated",
                    conversation_id=conversation_id,
                    session_id=active_session_id,
                    execution_id=execution_id,
                    total_steps=step,
                    cancelled=execution_context.cancelled,
                )

                return {
                    "conversation_id": conversation_id,
                    "execution_id": execution_id,
                    "session_id": active_session_id,
                    "steps": step,
                    "cancelled": execution_context.cancelled,
                }

            except ExecutionCancelled as ex:
                logger.warning("Agent execution cancelled", conversation_id=conversation_id, execution_id=execution_id, error=str(ex))
                if render_cb:
                    render_cb(role="system", btype="error", text=f"❌ {str(ex)}")

                return {
                    "conversation_id": conversation_id,
                    "execution_id": execution_id,
                    "session_id": active_session_id,
                    "steps": step if "step" in locals() else 0,
                    "cancelled": True,
                }

            except Exception as ex:
                logger.error("Unhandled agent execution error", conversation_id=conversation_id, execution_id=execution_id, error=str(ex), exc_info=True)
                if render_cb:
                    render_cb(role="system", btype="error", text=f"❌ Agent execution error: {str(ex)}")

                return {
                    "conversation_id": conversation_id,
                    "execution_id": execution_id,
                    "session_id": active_session_id,
                    "steps": step if "step" in locals() else 0,
                    "cancelled": False,
                    "error": str(ex),
                }

            finally:
                try:
                    self.controller.stop()
                finally:
                    self.session_manager.unregister_execution(execution_id)

    def _send_gateway_request(
        self,
        request_dto: GatewayChatRequest,
        execution_context: ExecutionContext,
    ):
        """Adapter gửi request tới LLM Gateway Client."""
        execution_context.check_cancelled()

        sender = self.gateway_client.send_request

        try:
            sig = __import__("inspect").signature(sender)
            params = sig.parameters

            kwargs = {}
            if "execution_context" in params or any(p.kind == p.VAR_KEYWORD for p in params.values()):
                kwargs["execution_context"] = execution_context

            if "cancellation_event" in params:
                kwargs["cancellation_event"] = execution_context.cancellation_event

            if "deadline_monotonic" in params:
                kwargs["deadline_monotonic"] = execution_context.deadline_monotonic

            return sender(request_dto, **kwargs)

        except (TypeError, ValueError):
            return sender(request_dto)