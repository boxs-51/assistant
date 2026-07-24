import json
from typing import List, Generator
from src.gateway.schemas.request import GatewayChatRequest, RequestConfig, RequestMetadata
from src.gateway.schemas.message import GatewayMessage, MessageContentPart
from src.gateway.schemas.attachment import TextContent
from core.execution_control import ExecutionController

class AgentEngine:
    def __init__(self, registry, hitl, gateway_client):
        self.registry = registry
        self.hitl = hitl
        self.gateway_client = gateway_client
        self.controller = ExecutionController()

    def _build_tools_schema(self) -> list:
        gateway_tools = []
        for name, tool_data in self.registry.tools.items():
            meta = tool_data["metadata"]
            gateway_tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": meta.get("description", ""),
                    "parameters": meta.get("parameters", {})
                }
            })
        return gateway_tools

    def run_agent_session(
        self, 
        session, 
        user_input: str, 
        model_name: str = "gpt-4o", 
        max_steps: int = 10,
        render_cb = None
    ):
        """Vòng lặp ReAct chuẩn có hỗ trợ State, Pause, Tool execution & Gateway"""
        self.controller.start()

        # 1. Thêm tin nhắn của User vào Working Memory
        user_msg = GatewayMessage(
            role="user",
            content=[MessageContentPart(type="text", data=TextContent(data=user_input, format="plain"))]
        )
        session.add_message(user_msg)

        for step in range(max_steps):
            # KiỂM TRA TẠM DỪNG TẠI ĐẦU MỖI STEP
            if not self.controller.check_and_wait_if_paused(render_cb):
                break

            # 2. Dựng Messages hoàn chỉnh qua ContextBuilder
            final_messages = ContextBuilder.build_final_messages(session, self.registry.constitution)

            request_dto = GatewayChatRequest(
                model=model_name,
                messages=final_messages,
                tools=self._build_tools_schema(),
                config=RequestConfig(temperature=0.2, stream=False),
                metadata=RequestMetadata(user={"id": "dev_user"}, routing={"prefer_provider": "auto"})
            )

            # 3. Gửi Request sang Gateway
            response = self.gateway_client.send_request(request_dto)
            choice = response.choices[0]
            assistant_msg = choice.message
            session.add_message(assistant_msg)

            # Phản hồi văn bản thuần của Assistant
            if assistant_msg.content:
                if render_cb:
                    render_cb("assistant", assistant_msg.content)

            # 4. KiỂM TRA TOOL CALLS
            if not assistant_msg.tool_calls:
                # LLM không yêu cầu Tool nào nữa -> Kết thúc ReAct loop
                break

            for tool_call in assistant_msg.tool_calls:
                tool_name = tool_call.function.name
                tool_args = json.loads(tool_call.function.arguments)

                # Cảnh báo Tạm dừng trước khi chạy Tool
                if not self.controller.check_and_wait_if_paused(render_cb):
                    return

                # Phê duyệt HITL đối với các Tool nguy cơ cao
                tool_info = self.registry.tools.get(tool_name)
                risk_level = tool_info["metadata"].get("base_risk", "LOW") if tool_info else "HIGH"
                
                if not self.hitl.request_approval(tool_name, tool_args, risk_level):
                    if render_cb:
                        render_cb("error", f"❌ Tool `{tool_name}` đã bị từ chối bởi người dùng.")
                    return

                # 5. Thực thi Tool
                if tool_info:
                    # Truyền session vào run() để tool tự update scratchpad (e.g., update_topic_context)
                    tool_result = tool_info["func"](**tool_args, context_session=session)
                else:
                    tool_result = f"❌ Lỗi: Tool `{tool_name}` không tồn tại trong Registry."

                if render_cb:
                    render_cb("observation", tool_result)

                # 6. Đẩy kết quả Tool (Tool Message) vào Session để vòng lặp sau LLM đọc được
                tool_msg = GatewayMessage(
                    role="tool",
                    tool_call_id=tool_call.id,
                    content=[MessageContentPart(type="text", data=TextContent(data=str(tool_result)))]
                )
                session.add_message(tool_msg)

        self.controller.stop()