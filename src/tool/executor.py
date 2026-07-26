from abc import ABC, abstractmethod
from typing import Dict, Any, Callable, Optional
import json
import re
import structlog

from ..schemas import GatewayToolDefinition, ToolType
from ..schemas.workflow import WorkflowDefinition
from .credential import CredentialManager
from .base.executor import BaseExecutor

import asyncio
logger = structlog.get_logger(__name__)


# ─── EXECUTOR CHO LOCAL TOOL ───
class LocalExecutor(BaseExecutor):
    def __init__(self):
        self._funcs: Dict[str, Callable] = {}

    def register_func(self, name: str, func: Callable):
        self._funcs[name] = func

    async def execute(self, definition: GatewayToolDefinition, arguments: Dict[str, Any], user_metadata: Dict[str, Any]) -> str:
        func = self._funcs.get(definition.name)
        if not func:
            return f"❌ Không tìm thấy hàm vật lý cho Local Tool: {definition.name}"
        try:
            if asyncio.iscoroutinefunction(func):
                return str(await func(**arguments))
            return str(func(**arguments))
        except Exception as e:
            return f"❌ Lỗi thực thi Local Tool: {str(e)}"

# ─── EXECUTOR CHO MCP TOOL ───
class McpExecutor(BaseExecutor):
    def __init__(self, mcp_manager):
        self.mcp_manager = mcp_manager

    async def execute(self, definition: GatewayToolDefinition, arguments: Dict[str, Any], user_metadata: Dict[str, Any]) -> str:
        # Gọi sang CredentialManager để lấy token tương ứng
        # user_metadata giờ là một dict chứa thông tin từ Identity
        creds = CredentialManager.get_mcp_credentials(definition.source_server, user_metadata.get("scopes", set()))
        # Gộp credentials vào chung với arguments truyền đi
        extended_args = {**arguments, **creds}
        
        # Gọi sang MCP Manager (truyền tên gốc của tool trên MCP Server)
        session = self.mcp_manager.get_raw_session(definition.source_server)
        if not session:
            return f"❌ [MCP EXECUTE LỖI] Server '{definition.source_server}' hiện đang mất kết nối hoặc ngoại tuyến."
        result = await session.call_tool(definition.name, arguments=extended_args)
        text_contents = [c.text for c in result.content if hasattr(c, 'text')]
        return "\n".join(text_contents)

# ─── EXECUTOR CHO NATIVE TOOL ───
class NativeExecutor(BaseExecutor):
    async def execute(self, definition: GatewayToolDefinition, arguments: Dict[str, Any], user_metadata: Dict[str, Any]) -> str:
        # Trường hợp này trên lý thuyết không xảy ra vì Provider tự execute, 
        # nhưng thiết kế sẵn để bảo vệ hệ thống nếu có cấu hình sai từ LLM Response Parser.
        return f"⚠️ Tool '{definition.name}' là Native Tool, việc thực thi thuộc trách nhiệm của AI Provider."

# ─── EXECUTOR CHO WORKFLOW TOOL ───
class WorkflowExecutor(BaseExecutor):
    def __init__(self, executor_registry: 'ExecutorRegistry', tool_registry):
        self._executor_registry = executor_registry
        self._tool_registry = tool_registry

    def _resolve_placeholders(self, template: Any, context: Dict[str, Any]) -> Any:
        """
        Đệ quy thay thế các placeholder trong arguments của một bước.
        Ví dụ: '{{steps.step_1.output}}' hoặc '{{initial_input.user_id}}'
        """
        if isinstance(template, str):
            # Regex để tìm các placeholder dạng {{...}}
            matches = re.findall(r"\{\{([^}]+)\}\}", template)
            if not matches:
                return template

            # Xử lý trường hợp placeholder là toàn bộ chuỗi
            if len(matches) == 1 and f"{{{{{matches[0]}}}}}" == template:
                keys = matches[0].strip().split('.')
                value = context
                for key in keys:
                    if isinstance(value, dict):
                        value = value.get(key)
                    else:
                        return template # Không tìm thấy, trả về template gốc
                return value

            # Xử lý trường hợp placeholder là một phần của chuỗi
            for match in matches:
                keys = match.strip().split('.')
                value = context
                for key in keys:
                    if isinstance(value, dict):
                        value = value.get(key)
                    else:
                        value = None
                        break
                if value is not None:
                    template = template.replace(f"{{{{{match}}}}}", str(value))
            return template

        elif isinstance(template, dict):
            return {k: self._resolve_placeholders(v, context) for k, v in template.items()}
        elif isinstance(template, list):
            return [self._resolve_placeholders(item, context) for item in template]
        else:
            return template

    async def execute(self, definition: GatewayToolDefinition, arguments: Dict[str, Any], user_metadata: Dict[str, Any]) -> str:
        try:
            workflow_def = WorkflowDefinition.model_validate(definition.parameters)
        except Exception as e:
            logger.error("Workflow definition validation failed", tool_name=definition.name, error=str(e))
            return f"❌ Lỗi định dạng Workflow: {e}"

        # Context chứa kết quả của các bước và input ban đầu
        execution_context = {
            "initial_input": arguments,
            "steps": {}
        }
        last_step_id = ""

        for step in workflow_def.steps:
            logger.info("Executing workflow step", workflow=definition.name, step_id=step.step_id, tool_name=step.tool_name)
            step_tool_def = self._tool_registry.get(step.tool_name)
            if not step_tool_def:
                return f"❌ Lỗi Workflow: Tool '{step.tool_name}' trong bước '{step.step_id}' không tồn tại."

            # Chuẩn bị arguments cho tool của bước này
            resolved_args = self._resolve_placeholders(step.arguments, execution_context)

            # Lấy executor tương ứng và thực thi
            executor = self._executor_registry.get_executor(step_tool_def.tool_type)
            if not executor:
                return f"❌ Lỗi Workflow: Không tìm thấy executor cho loại tool '{step_tool_def.tool_type}'."

            step_output_str = await executor.execute(step_tool_def, resolved_args, user_metadata)
            
            # Cố gắng parse output thành JSON, nếu không được thì giữ nguyên string
            try:
                execution_context["steps"][step.step_id] = {"output": json.loads(step_output_str)}
            except json.JSONDecodeError:
                execution_context["steps"][step.step_id] = {"output": step_output_str}
            last_step_id = step.step_id
        
        # Thêm alias 'last' để dễ dàng tham chiếu đến output của bước cuối cùng
        if last_step_id:
            execution_context["steps"]["last"] = execution_context["steps"][last_step_id]

        final_output = self._resolve_placeholders(workflow_def.output_template, execution_context)
        return json.dumps(final_output) if isinstance(final_output, (dict, list)) else str(final_output)

# ─── BỘ ĐĂNG KÝ VÀ ĐIỀU PHỐI CHÍNH (EXECUTOR REGISTRY) ───
class ExecutorRegistry:
    def __init__(self):
        self._executors: Dict[ToolType, BaseExecutor] = {}

    def register(self, tool_type: ToolType, executor: BaseExecutor):
        self._executors[tool_type] = executor

    def get_executor(self, tool_type: ToolType) -> Optional[BaseExecutor]:
        return self._executors.get(tool_type)