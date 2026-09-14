import base64
import json
import mimetypes
import os
import re
import threading
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import List, Optional, Any, Dict

import structlog

from ..schemas.request import GatewayChatRequest, RequestConfig, RequestMetadata
from ..schemas.message import GatewayMessage, MessageContentPart
from ..schemas.tool import GatewayToolCall, FunctionCall, GatewayToolDefinition
from ..core.execution_control import ExecutionController
from ..schemas.enums import ToolType

from .mock_provider import MockLLMProvider
from .tool_executor import ToolExecutor, ExecutionContext, ExecutionCancelled

logger = structlog.get_logger(__name__)

def extract_text_content(content: Any) -> str:
    """Bóc tách văn bản thuần từ cấu trúc content dạng string, dict, object hoặc danh sách lồng ghép."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content

    # 1. Trường hợp là List (xử lý đệ quy cho list lồng nhau)
    if isinstance(content, list):
        return "".join(extract_text_content(item) for item in content)

    # 2. Trường hợp là Dict
    if isinstance(content, dict):
        text_val = content.get("text")
        if text_val is not None:
            return extract_text_content(text_val)
        
        data_val = content.get("data")
        if isinstance(data_val, dict):
            return extract_text_content(data_val.get("data"))
        if data_val is not None:
            return str(data_val)
        return ""

    # 3. Trường hợp là Object (Pydantic model / Class)
    text_attr = getattr(content, "text", None)
    if text_attr is not None:
        return extract_text_content(text_attr)

    if hasattr(content, "data"):
        data_attr = getattr(content, "data")
        if isinstance(data_attr, dict):
            return extract_text_content(data_attr.get("data"))
        if data_attr is not None:
            return str(data_attr)

    return str(content)

def serialize_helper(obj: Any) -> Any:
    """Hỗ trợ serialize linh hoạt cho Enum, Pydantic model, Dict và List."""
    if obj is None:
        return None
    # Xử lý Enum (MessageContentType, FinishReason, ...)
    if isinstance(obj, Enum):
        return obj.value
    # Dữ liệu nguyên thủy
    if isinstance(obj, (int, float, str, bool)):
        return obj
    # Pydantic v2
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    # Pydantic v1
    if hasattr(obj, "dict"):
        return obj.dict()
    # Dictionary
    if isinstance(obj, dict):
        return {k: serialize_helper(v) for k, v in obj.items()}
    # List / Tuple / Set
    if isinstance(obj, (list, tuple, set)):
        return [serialize_helper(i) for i in obj]
    # Trường hợp object có thuộc tính text
    if hasattr(obj, "text") and getattr(obj, "text") is not None:
        return obj.text
    # Trường hợp object có __dict__
    if hasattr(obj, "__dict__"):
        return {k: serialize_helper(v) for k, v in vars(obj).items()}
    return str(obj)

class AgentEngine:
    """
    Agent execution coordinator.

    P1 responsibilities added here:
      - sessions are indexed by conversation_id rather than one global _current_session
      - one conversation runs one agent turn at a time via a per-conversation RLock
      - every execution gets a unique execution_id
      - cancellation/deadline are carried through the complete execution context
      - ToolExecutor receives the same execution context
      - controller cleanup is exception-safe

    Compatibility:
      run_agent_session() still accepts an externally-created `session`.
      For real multi-conversation isolation, callers should pass `conversation_id`.
      When omitted, the engine derives it from session.conversation_id/session_id.
    """

    def __init__(self, registry, hitl, gateway_client, mock_mode: bool = False):
        self.registry = registry
        self.hitl = hitl
        self.gateway_client = gateway_client
        self.controller = ExecutionController()
        self.mock_mode = mock_mode

        self.mock_provider = MockLLMProvider(self.registry)
        self.tool_executor = ToolExecutor(self.registry, self.hitl)

        self._session_registry: Dict[str, Any] = {}
        self._conversation_locks: Dict[str, threading.RLock] = {}
        self._registry_lock = threading.RLock()

        self._executions: Dict[str, ExecutionContext] = {}
        self._executions_lock = threading.RLock()

        logger.info(
            "AgentEngine initialized successfully",
            registered_tools_count=(
                len(self.registry.tools)
                if hasattr(self.registry, "tools")
                else 0
            ),
            mock_mode=self.mock_mode,
        )

    # -----------------------------------------------------------------
    # EXECUTION / SESSION IDENTITY
    # -----------------------------------------------------------------

    def _get_or_create_conversation_lock(
        self,
        conversation_id: str,
    ) -> threading.RLock:
        with self._registry_lock:
            lock = self._conversation_locks.get(conversation_id)
            if lock is None:
                lock = threading.RLock()
                self._conversation_locks[conversation_id] = lock
            return lock

    def _get_or_create_session(
        self,
        conversation_id: str,
        session: Optional[Any] = None,
    ) -> Any:
        with self._registry_lock:
            existing = self._session_registry.get(conversation_id)

            if existing is not None:
                if session is not None and existing is not session:
                    logger.warning(
                        "Ignoring different session object for an already-registered conversation",
                        conversation_id=conversation_id,
                    )
                return existing

            if session is None:
                from ..schemas.context import AgentContextSession
                session = AgentContextSession()

            # Stamp identity on mutable session objects when supported.
            for attr, value in (
                ("conversation_id", conversation_id),
                ("session_id", getattr(session, "session_id", None)),
            ):
                try:
                    if attr == "conversation_id":
                        setattr(session, attr, value)
                    elif value is None and not getattr(session, attr, None):
                        setattr(session, attr, f"session_{uuid.uuid4().hex}")
                except Exception:
                    pass

            self._session_registry[conversation_id] = session
            return session

    def _resolve_identity(
        self,
        session: Optional[Any],
        conversation_id: Optional[str],
        execution_id: Optional[str],
    ):
        derived_conversation_id = (
            conversation_id
            or getattr(session, "conversation_id", None)
            or getattr(session, "session_id", None)
            or f"conversation_{uuid.uuid4().hex}"
        )

        derived_execution_id = (
            execution_id
            or f"exec_{uuid.uuid4().hex}"
        )

        return (
            str(derived_conversation_id),
            str(derived_execution_id),
        )

    def _register_execution(self, execution_context: ExecutionContext):
        with self._executions_lock:
            self._executions[execution_context.execution_id] = execution_context

    def _unregister_execution(self, execution_id: str):
        with self._executions_lock:
            self._executions.pop(execution_id, None)

    def cancel_execution(self, execution_id: str) -> bool:
        """Cooperatively cancel one execution without affecting other conversations."""
        with self._executions_lock:
            execution_context = self._executions.get(execution_id)

        if execution_context is None:
            return False

        execution_context.cancellation_event.set()

        logger.info(
            "Agent execution cancellation requested",
            execution_id=execution_id,
            conversation_id=execution_context.conversation_id,
        )
        return True

    def get_execution(self, execution_id: str) -> Optional[ExecutionContext]:
        with self._executions_lock:
            return self._executions.get(execution_id)

    # -----------------------------------------------------------------
    # TOOLS / PARSERS
    # -----------------------------------------------------------------

    def _build_tools_schema(self) -> List[GatewayToolDefinition]:
        gateway_tools = []
        for name, tool_data in self.registry.tools.items():
            meta = tool_data.get("metadata", {})
            is_mcp = tool_data.get("is_mcp", False)
            tool_type = ToolType.MCP if is_mcp else ToolType.WORKFLOW

            tool_def = GatewayToolDefinition(
                name=name,
                description=meta.get("description", f"Tool {name}"),
                parameters=meta.get("parameters", {}),
                tool_type=tool_type,
                source_server=meta.get("source_server", None),
            )
            gateway_tools.append(tool_def)

        return gateway_tools

    def _merge_tool_call_delta(self, acc_tool_calls: list, deltas: list):
        for delta in deltas:
            index = getattr(delta, "index", 0) or 0
            while len(acc_tool_calls) <= index:
                acc_tool_calls.append(
                    {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    }
                )

            target = acc_tool_calls[index]

            # Tool-call IDs are identifiers, not stream fragments. Keep the first value.
            delta_id = getattr(delta, "id", None)
            if delta_id and not target["id"]:
                target["id"] = delta_id

            function_delta = getattr(delta, "function", None)
            if function_delta:
                delta_name = getattr(function_delta, "name", None)
                delta_arguments = getattr(function_delta, "arguments", None)

                if delta_name:
                    target["function"]["name"] += delta_name
                if delta_arguments:
                    target["function"]["arguments"] += delta_arguments

    def _parse_text_action_fallback(self, text_content: str) -> List[GatewayToolCall]:
        if not text_content:
            return []

        if isinstance(text_content, list):
            extracted_texts = []
            for item in text_content:
                if isinstance(item, str):
                    extracted_texts.append(item)
                elif hasattr(item, 'text'):  # Nếu là đối tượng dạng ContentBlock
                    extracted_texts.append(item.text)
                elif isinstance(item, dict) and 'text' in item:  # Nếu là dict
                    extracted_texts.append(item['text'])
                else:
                    extracted_texts.append(str(item))
            text_content = "\n".join([t for t in extracted_texts if t is not None])
            
        tool_calls = []
        pattern = r"Action:\s*([a-zA-Z0-9_]+)\((.*?)\)"
        matches = re.findall(pattern, text_content, re.DOTALL)

        for name, raw_args in matches:
            args_dict = {}
            arg_matches = re.findall(
                r"([a-zA-Z0-9_]+)\s*=\s*['\"]([^'\"]*)['\"]",
                raw_args,
            )
            for k, v in arg_matches:
                args_dict[k] = v

            tool_calls.append(
                GatewayToolCall(
                    id=f"fallback_{name}_{uuid.uuid4().hex[:12]}",
                    type="function",
                    function=FunctionCall(
                        name=name,
                        arguments=json.dumps(args_dict),
                    ),
                )
            )

        return tool_calls

    def _process_attached_files(
        self,
        attached_files: List[Any],
    ) -> List[MessageContentPart]:
        parts = []
        for item in attached_files:
            if not item:
                continue

            if isinstance(item, dict):
                mime_type = item.get(
                    "mime_type",
                    "application/octet-stream",
                )
                b64_data = item.get("b64_data", "")
                data_uri = item.get("data_uri") or (
                    f"data:{mime_type};base64,{b64_data}"
                )
                filename = item.get("filename", "attached_file")

                if mime_type.startswith("image/"):
                    parts.append(
                        MessageContentPart(
                            type="image_url",
                            image_url={"url": data_uri},
                        )
                    )
                else:
                    parts.append(
                        MessageContentPart(
                            type="file",
                            file_data={
                                "filename": filename,
                                "mime_type": mime_type,
                                "data": b64_data,
                            },
                        )
                    )
                continue

            if not isinstance(item, str):
                continue

            item_str = item.strip()

            if item_str.startswith("data:"):
                parts.append(
                    MessageContentPart(
                        type="image_url",
                        image_url={"url": item_str},
                    )
                )

            elif item_str.startswith(("http://", "https://")):
                parts.append(
                    MessageContentPart(
                        type="image_url",
                        image_url={"url": item_str},
                    )
                )

            elif os.path.exists(item_str) and os.path.isfile(item_str):
                try:
                    mime_type, _ = mimetypes.guess_type(item_str)
                    if not mime_type:
                        mime_type = "application/octet-stream"

                    with open(item_str, "rb") as f:
                        file_bytes = f.read()
                        b64_data = base64.b64encode(file_bytes).decode("utf-8")

                    data_uri = f"data:{mime_type};base64,{b64_data}"

                    if mime_type.startswith("image/"):
                        parts.append(
                            MessageContentPart(
                                type="image_url",
                                image_url={"url": data_uri},
                            )
                        )
                    else:
                        parts.append(
                            MessageContentPart(
                                type="file",
                                file_data={
                                    "filename": Path(item_str).name,
                                    "mime_type": mime_type,
                                    "data": b64_data,
                                },
                            )
                        )
                except Exception as e:
                    logger.error(
                        "Failed to read local file",
                        file_path=item_str,
                        error=str(e),
                    )

            else:
                data_uri = f"data:image/jpeg;base64,{item_str}"
                parts.append(
                    MessageContentPart(
                        type="image_url",
                        image_url={"url": data_uri},
                    )
                )

        return parts

    # -----------------------------------------------------------------
    # AGENT EXECUTION
    # -----------------------------------------------------------------

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
        """
        Run one autonomous agent turn with state managed via Gateway session_id.
        """
        conversation_id, execution_id = self._resolve_identity(
            session=session,
            conversation_id=conversation_id,
            execution_id=execution_id,
        )

        session = self._get_or_create_session(
            conversation_id=conversation_id,
            session=session,
        )

        registered_conversation_id = getattr(
            session,
            "conversation_id",
            None,
        )
        if (
            registered_conversation_id
            and str(registered_conversation_id) != conversation_id
        ):
            raise ValueError(
                "Session/conversation identity mismatch: "
                f"session={registered_conversation_id}, conversation={conversation_id}"
            )

        if deadline_monotonic is None and timeout_seconds is not None:
            if timeout_seconds <= 0:
                timeout_seconds = 0.0
            deadline_monotonic = (
                time.monotonic() + float(timeout_seconds)
            )

        execution_context = ExecutionContext(
            conversation_id=conversation_id,
            execution_id=execution_id,
            session=session,
            cancellation_event=cancellation_event,
            deadline_monotonic=deadline_monotonic,
        )

        is_mock = self.mock_mode if mock_mode is None else mock_mode
        conversation_lock = self._get_or_create_conversation_lock(
            conversation_id
        )

        # Lấy session_id ban đầu từ session object hoặc conversation_id
        active_session_id = getattr(session, "session_id", None) or conversation_id

        logger.info(
            "Starting autonomous agent session",
            conversation_id=conversation_id,
            session_id=active_session_id,
            execution_id=execution_id,
            mock_mode=is_mock,
        )

        self._register_execution(execution_context)

        with conversation_lock:
            self.controller.start()

            try:
                execution_context.check_cancelled()

                # 1. Tạo duy nhất prompt mới cho lượt gọi này
                content_parts = []

                if user_input and user_input.strip():
                    content_parts.append(
                        MessageContentPart(
                            type="text",
                            text=user_input,
                        )
                    )

                if attached_files:
                    file_parts = self._process_attached_files(
                        attached_files
                    )
                    content_parts.extend(file_parts)

                latest_messages_to_send = []
                if content_parts:
                    new_user_msg = GatewayMessage(
                        role="user",
                        content=content_parts,
                    )
                    session.add_message(new_user_msg)
                    # Chỉ gửi duy nhất message mới
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

                    # 2. Khởi tạo Request DTO chỉ kèm theo Prompt mới và session_id
                    request_dto = GatewayChatRequest(
                        session_id=active_session_id,
                        model=model_name,
                        messages=latest_messages_to_send,
                        config=RequestConfig(
                            temperature=0.1,
                            stream=enable_stream,
                        ),
                        metadata=RequestMetadata(
                            user={"id": "dev_user"},
                            routing={"prefer_provider": provider_name, "type": "fallback"},
                        ),
                    )

                    # 3. Gửi Request tới LLM Gateway
                    try:
                        execution_context.check_cancelled()

                        if is_mock:
                            response = self.mock_provider.generate_response(
                                latest_messages_to_send,
                                step,
                                enable_stream,
                            )
                        else:
                            response = self._send_gateway_request(
                                request_dto,
                                execution_context,
                            )

                    except ExecutionCancelled as ex:
                        logger.warning(
                            "LLM request cancelled",
                            execution_id=execution_id,
                            error=str(ex),
                        )
                        if render_cb:
                            render_cb(
                                role="system",
                                btype="error",
                                text=f"❌ {str(ex)}",
                            )
                        break

                    except Exception as ex:
                        logger.error(
                            "Failed to fetch LLM response",
                            execution_id=execution_id,
                            error=str(ex),
                            exc_info=True,
                        )
                        if render_cb:
                            render_cb(
                                role="system",
                                btype="error",
                                text=f"❌ Lỗi kết nối Gateway Client: {str(ex)}",
                            )
                        break

                    # 4. Cập nhật session_id từ Response (nếu Gateway trả về session_id mới)
                    resp_session_id = None
                    if isinstance(response, dict):
                        resp_session_id = response.get("session_id")
                    else:
                        resp_session_id = getattr(response, "session_id", None)

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

                                # Cập nhật session_id nếu nằm trong chunk metadata/field
                                chunk_session_id = getattr(chunk, "session_id", None)
                                if chunk_session_id:
                                    active_session_id = chunk_session_id
                                    if hasattr(session, "session_id"):
                                        session.session_id = chunk_session_id

                                if not chunk.choices:
                                    continue

                                metadata = getattr(chunk, "metadata", None)
                                delta = chunk.choices[0].delta

                                if getattr(delta, "reasoning_content", None):
                                    accumulated_thought += delta.reasoning_content

                                if getattr(delta, "content", None):

                                    accumulated_text += extract_text_content(delta.content)

                                if getattr(delta, "tool_calls", None):
                                    self._merge_tool_call_delta(
                                        accumulated_tool_calls_raw,
                                        delta.tool_calls,
                                    )
                                if render_cb:
                                    render_cb(
                                        role="assistant",
                                        btype="stream_content",
                                        data=serialize_helper(chunk),
                                    )

                        except ExecutionCancelled:
                            execution_context.cancellation_event.set()
                            logger.warning(
                                "LLM stream cancelled",
                                execution_id=execution_id,
                            )
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
                            final_tool_calls = self._parse_text_action_fallback(
                                accumulated_text
                            )

                        assistant_msg = GatewayMessage(
                            role="assistant",
                            content=accumulated_text or "",
                            tool_calls=final_tool_calls or None,
                        )

                    else:
                        execution_context.check_cancelled()

                        choice = response.choices[0] if isinstance(response.choices, list) else response['choices'][0]
                        
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
                            assistant_msg.tool_calls = self._parse_text_action_fallback(
                                parsed_text
                            )

                        if render_cb:
                            render_cb(
                                role="assistant",
                                btype="content",
                                data=serialize_helper(response),
                            )

                    if execution_context.cancelled:
                        break

                    session.add_message(assistant_msg)

                    # Finish when there are no tool calls
                    if not assistant_msg.tool_calls:
                        logger.info(
                            "Goal reached or standard response completed",
                            step=step,
                            execution_id=execution_id,
                        )
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

                    # Nếu có tool call, gán tin nhắn trả về của tool làm payload gửi tiếp cho bước ReAct sau
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
                logger.warning(
                    "Agent execution cancelled",
                    conversation_id=conversation_id,
                    execution_id=execution_id,
                    error=str(ex),
                )

                if render_cb:
                    render_cb(
                        role="system",
                        btype="error",
                        text=f"❌ {str(ex)}",
                    )

                return {
                    "conversation_id": conversation_id,
                    "execution_id": execution_id,
                    "session_id": active_session_id,
                    "steps": step if "step" in locals() else 0,
                    "cancelled": True,
                }

            except Exception as ex:
                logger.error(
                    "Unhandled agent execution error",
                    conversation_id=conversation_id,
                    execution_id=execution_id,
                    error=str(ex),
                    exc_info=True,
                )

                if render_cb:
                    render_cb(
                        role="system",
                        btype="error",
                        text=f"❌ Agent execution error: {str(ex)}",
                    )

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
                    self._unregister_execution(execution_id)

    def _send_gateway_request(
        self,
        request_dto: GatewayChatRequest,
        execution_context: ExecutionContext,
    ):
        """Compatibility adapter for GatewayClient variants."""
        execution_context.check_cancelled()

        sender = self.gateway_client.send_request

        try:
            sig = __import__("inspect").signature(sender)
            params = sig.parameters

            kwargs = {}
            if "execution_context" in params or any(
                p.kind == p.VAR_KEYWORD for p in params.values()
            ):
                kwargs["execution_context"] = execution_context

            if "cancellation_event" in params:
                kwargs["cancellation_event"] = execution_context.cancellation_event

            if "deadline_monotonic" in params:
                kwargs["deadline_monotonic"] = (
                    execution_context.deadline_monotonic
                )

            return sender(request_dto, **kwargs)

        except (TypeError, ValueError):
            # Legacy gateway signature: send_request(request_dto)
            return sender(request_dto)
