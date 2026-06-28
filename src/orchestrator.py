import json
import re
import asyncio
from typing import Dict, Any, List, Tuple, Callable

from config_loader import ConfigLoader
from context_engine.memory_manager import ContextEngine
from guardrail.guar import GuardrailSystem
from minitor import ExecutionMonitor
from capability import CapabilityManager
from gateway_client import AIGatewayClient
from model_selector import ModelSelector


class AgentOrchestrator:
    def __init__(self):
        # 1. Khởi tạo cấu hình
        self.cfg = ConfigLoader()
        
        # 2. Nâng cấp: Khởi tạo client để gọi AI Gateway
        # Toàn bộ logic routing, fallback, llm-calling sẽ do Gateway xử lý.
        self.gateway_client = AIGatewayClient()
        
        # 3. Khởi tạo các hệ thống hỗ trợ
        self.context_engine = ContextEngine(self.cfg.config)
        self.guardrail = GuardrailSystem()
        self.monitor = ExecutionMonitor(max_steps=5)
        self.capability_manager = CapabilityManager()

        # 4. Nâng cấp: Sử dụng ModelSelector thay cho SmartRouter cũ
        # Nhiệm vụ của nó là chọn model (e.g., 'gpt-4o') để gửi cho Gateway.
        self.model_selector = ModelSelector(self.cfg.get_routing_config())
        
        # Tối ưu hóa: Tạo prompt năng lực nền tảng một lần duy nhất khi khởi tạo để tiết kiệm tài nguyên
        self.base_action_prompt = self._build_action_prompt()

    async def _parse_llm_json_with_retry(self, llm_generate_func: Callable, system_prompt: str, user_prompt: str, max_retries: int = 2) -> dict:
        """
        Bóc tách JSON với cơ chế tự sửa lỗi (self-correction) bất đồng bộ.
        Nếu LLM trả về JSON không hợp lệ, hệ thống sẽ gửi lại thông báo lỗi để LLM tự sửa.
        """
        attempt = 0
        last_error = ""
        while attempt < max_retries:
            current_prompt = f"{user_prompt}\n\n{last_error}" if last_error else user_prompt
            
            # Kiểm tra xem hàm sinh câu trả lời có phải là async coroutine không
            #if asyncio.iscoroutinefunction(llm_generate_func):
                #response_text = await llm_generate_func(system_prompt, current_prompt)
            #else:
            response_text = await llm_generate_func(system_prompt, current_prompt)
            
            try:
                match = re.search(r"\{.*\}", response_text, re.DOTALL)
                if match:
                    return json.loads(match.group(0))
                return json.loads(response_text)
            except json.JSONDecodeError as e:
                attempt += 1
                print(f"⚠️ [JSON Parse] Lỗi phân tích JSON lần {attempt}, đang yêu cầu LLM tự sửa. Lỗi: {e}")
                last_error = f"Lưu ý từ hệ thống: Định dạng JSON ở lượt trước không hợp lệ. Lỗi: '{e}'. Hãy chắc chắn chỉ trả về duy nhất khối JSON cấu trúc."

        print("🛑 [JSON Parse] Thất bại sau nhiều lần thử. Tự động chuyển đổi về chế độ phản hồi trực tiếp.")
        return {"tool_action": "reply", "thought": "Hệ thống không thể phân tích JSON từ LLM sau khi thử lại.", "arguments": {}}

    async def _get_human_approval_async(self, tool_name: str, tool_args: dict) -> Tuple[bool, str]:
        """
        Thay thế input() block luồng bằng cơ chế chạy trên ThreadPoolExecutor, 
        giúp giữ an toàn cho Event Loop không bị đóng băng khi chờ con người phản hồi.
        """
        print(f"\n✋ [HUMAN-IN-THE-LOOP - CHỜ DUYỆT]")
        print(f"   Agent muốn kích hoạt công cụ NHẠY CẢM: [{tool_name}]")
        print(f"   Tham số truyền vào: {tool_args}")
        
        loop = asyncio.get_running_loop()
        user_choice = await loop.run_in_executor(None, lambda: input("👉 Bạn có cho phép thực thi không? (y/n): ").strip().lower())
        
        if user_choice == 'y':
            return True, ""
        else:
            user_feedback = await loop.run_in_executor(None, lambda: input("👉 Nhập lý do từ chối để gửi lại cho AI sửa sai: ").strip())
            return False, user_feedback

    async def run(self, session_id: str, user_request: str, operational_mode: str = "hybrid") -> str:
        print(f"\n⚡ [BẮT ĐẦU VÒNG LẶP CHÍNH ASYNC]")
        self.monitor.reset()

        # =====================================================================
        # BẢO VỆ ĐẦU VÀO (INPUT GUARDRAIL)
        # =====================================================================
        if not self.guardrail.verify_input(user_request):
            return "Yêu cầu bị từ chối hệ thống vì vi phạm chính sách bảo mật thông tin."

        collected_data_log = []
        original_request = user_request
        current_request_for_llm = user_request

        # =====================================================================
        # LUỒNG TƯ DUY & GỌI CÔNG CỤ TRONG VÒNG LẶP BẤT ĐỒNG BỘ
        # =====================================================================
        while True:
            # 1. Xây dựng ngữ cảnh động và tích hợp lịch sử chat
            context_data = self.context_engine.build_rich_context(session_id, original_request)
            dynamic_system_prompt = f"{self.cfg.get_system_prompt()}\n\n{context_data['full_prompt_context']}"

            # 2. Định tuyến hạ tầng thông minh dựa trên ngữ cảnh công việc hiện tại
            chat_history = context_data.get("chat_history", [])
            # ModelSelector sẽ chọn model phù hợp (local hoặc cloud) dựa trên cấu hình
            target_model = self.model_selector.select_model(current_request_for_llm, chat_history)
            print(f"ℹ️ [Orchestrator] Đã chọn model: '{target_model}' cho bước này.")
            
            # Cấu trúc prompt hành động tối ưu hóa Token (sử dụng base prompt đã cache)
            current_action_prompt = f"{self.base_action_prompt}\n\n=== YÊU CẦU HIỆN TẠI CỦA NGƯỜI DÙNG ===\n> {current_request_for_llm}"

            # Gọi cơ chế sinh mã tự sửa lỗi JSON
            decision = await self._parse_llm_json_with_retry(
                llm_generate_func=lambda sp, p: self.gateway_client.generate(sp, p, model=target_model),
                system_prompt=dynamic_system_prompt,
                user_prompt=current_action_prompt
            )
            
            tool_name = decision.get("tool_action", "reply")
            tool_args = decision.get("arguments", {})

            # Điều phối ngăn xếp tác vụ (Task Stack)
            self._handle_task_stack(session_id, decision)

            # Nếu AI chọn kết thúc chu kỳ tư duy -> Thoát vòng lặp
            if tool_name == "reply":
                break

            # =====================================================================
            # KIỂM DUYỆT HÀNH ĐỘNG VÀ BẢO VỆ CON NGƯỜI (HITL)
            # =====================================================================
            execution_status = self.monitor.validate_and_route_execution(tool_name, tool_args, mode=operational_mode)
            
            if execution_status == "reject_loop":
                print("🛑 [Monitor] Phát hiện vòng lặp vô tận! Cưỡng bức Agent dừng bước.")
                collected_data_log.append("Lỗi hệ thống: Quá trình bị ngắt do phát hiện vòng lặp vô tận.")
                break
                
            elif execution_status == "hitl":
                is_approved, user_feedback = await self._get_human_approval_async(tool_name, tool_args)
                if not is_approved:
                    print("❌ [Human] Người dùng từ chối thực thi.")
                    current_request_for_llm = f"Yêu cầu gốc: '{original_request}'. Lưu ý quan trọng từ người dùng: Hành động công cụ '{tool_name}' của bạn đã bị CON NGƯỜI TỪ CHỐI với lý do: '{user_feedback}'. Hãy suy nghĩ lại để tìm giải pháp khác thay thế."
                    continue

            # =====================================================================
            # THỰC THI CÔNG CỤ AN TOÀN VÀ KIỂM DUYỆT TRUNG GIAN (INTERMEDIATE GUARDRAIL)
            # =====================================================================
            print(f"🛠️ [Thực thi an toàn]: Kích hoạt '{tool_name}'...")
            
            # Lấy công cụ thông qua memory manager hoặc capability manager tuỳ cấu trúc thực tế của bạn
            execute_func = getattr(self.capability_manager.tools, 'execute_tool', self.context_engine.short_mem.tools.execute_tool)
            tool_result = execute_func(tool_name, tool_args)

            # Kiểm duyệt dữ liệu nhạy cảm đầu ra của từng công cụ ngay lập tức (Data Leakage Protection)
            safe_tool_result = self.guardrail.verify_output(tool_result)
            
            # Cộng dồn kết quả (Append) vào nhật ký thay vì ghi đè, hỗ trợ multi-step agent
            collected_data_log.append(f"Kết quả từ công cụ '{tool_name}':\n{safe_tool_result}")
            
            # Nạp dữ liệu vừa thu thập làm ngữ cảnh đầu vào mới cho bước tư duy tiếp theo
            current_request_for_llm = f"Tôi đã thực hiện công cụ '{tool_name}' và nhận được kết quả: '{safe_tool_result}'. Dựa trên thông tin này, hãy ra quyết định hành động tiếp theo để xử lý trọn vẹn yêu cầu: '{original_request}'."

        # =====================================================================
        # TỔNG HỢP VÀ PHẢN HỒI CUỐI CÙNG (SYNTHESIS)
        # =====================================================================
        synthesis_input = "\n\n".join(collected_data_log) if collected_data_log else "Không có dữ liệu ngoài."
        synthesis_prompt = f"[Yêu cầu gốc]: {original_request}\n[Dữ liệu đã thu thập qua các bước]:\n{synthesis_input}\nHãy tổng hợp toàn bộ dữ liệu trên và viết câu trả lời cuối cùng."
        
        # Thực hiện tổng hợp kết quả tổng quát
        final_response = await self.gateway_client.generate(system_prompt=dynamic_system_prompt, prompt=synthesis_prompt)

        # =====================================================================
        # BẢO VỆ ĐẦU RA CUỐI CÙNG (OUTPUT GUARDRAIL)
        # =====================================================================
        safe_response = self.guardrail.verify_output(final_response)

        # Cập nhật ký ức hội thoại dài hạn và ngắn hạn
        self.context_engine.update_context(session_id, original_request, safe_response)
        print("✅ [HOÀN THÀNH] Chu kỳ vận hành an toàn kết thúc.")
        return safe_response

    def _handle_task_stack(self, session_id: str, decision: dict):
        """Hàm bổ trợ xử lý Ngăn xếp tác vụ"""
        task_action = decision.get("task_action", "none")
        if task_action == "push" and decision.get("task_description"):
            self.context_engine.push_task(session_id, decision.get("task_description"))
        elif task_action == "pop":
            self.context_engine.pop_completed_task(session_id)

    def _build_action_prompt(self) -> str:
        """
        Xây dựng cấu trúc Prompt mẫu ép cấu trúc JSON tĩnh.
        Hàm này chỉ chạy 1 lần lúc khởi tạo object để tối ưu tài nguyên token.
        """
        capabilities_string = self.capability_manager.get_available_capabilities()

        action_prompt = f"""=== HỆ THỐNG NĂNG LỰC BẠN CÓ QUYỀN SỬ DỤNG ===
{capabilities_string}

=== BÀI TOÁN TƯ DUY & ĐIỀU PHỐI (ORCHESTRATION TASK) ===
Dựa vào lịch sử cuộc trò chuyện, các tác vụ đang treo trong bộ nhớ làm việc (Working Memory) và yêu cầu mới nhất từ người dùng, bạn bắt buộc phải thực hiện phân tích theo logic 3 bước sau:

1. **Quản lý Ngữ cảnh (Task Stack):**
   - `"push"`: Nếu người dùng đột ngột giao một việc mới không liên quan đến việc đang làm dở -> Treo việc cũ lại.
   - `"pop"`: Nếu việc xen ngang hiện tại đã được giải quyết triệt để và bạn cần quay lại xử lý nốt việc cũ.
   - `"none"`: Nếu người dùng vẫn đang nói về chủ đề cũ hoặc chỉ đang trò chuyện thông thường.

2. **Lựa chọn Hành động (Action Selection):**
   - Xác định xem bạn có cần dữ liệu từ thế giới thực (web, file, DB) để xử lý yêu cầu không. Nếu có, chọn chính xác tên một công cụ (Tool) phù hợp trong danh sách được cấp.
   - Nếu bạn đã có đủ dữ liệu từ các bước trước hoặc đây là câu hỏi kiến thức phổ thông, hãy chọn hành động là "reply".

3. **Trích xuất Tham số (Arguments Extraction):**
   - Đóng gói chính xác các tham số cần thiết cho công cụ đã chọn dưới dạng Key-Value dựa theo đúng Schema cấu trúc.

=== QUY TẮC ĐẦU RA (BẮT BUỘC TUÂN THỦ) ===
Bạn PHẢI trả lời duy nhất theo định dạng JSON mẫu dưới đây. Không được phép kèm theo bất kỳ lời thoại, lời giải thích hay ký tự thừa thãi nào ngoài khối JSON.

```json
{{
    "thought": "Suy nghĩ chi tiết bằng tiếng Việt về hướng giải quyết...",
    "task_action": "push" | "pop" | "none",
    "task_description": "Mô tả ngắn gọn tác vụ nếu là push, để trống nếu là pop/none",
    "tool_action": "tên_công_cụ_chính_xác" hoặc "reply",
    "arguments": {{
        "tên_tham_số": "giá_trị"
    }}
}}
"""
        return action_prompt.strip()
    
