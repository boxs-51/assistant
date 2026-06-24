import json
import re
from src.config_loader import ConfigLoader
from src.router import SmartRouter
from src.llm.ollama_client import OllamaClient
from src.context_engine.memory_manager import ContextEngine
from src.guardrail.guar import GuardrailSystem
from src.minitor import ExecutionMonitor
from src.capability import CapabilityManager




class AgentOrchestrator:
    def __init__(self):
        # 1. Khởi tạo cấu hình và định tuyến
        self.cfg = ConfigLoader()
        self.router = SmartRouter(self.cfg.config)
        
        # 2. Khởi tạo các kết nối LLM (Ví dụ với Local)
        self.local_llm = OllamaClient(self.cfg.get_provider_config("local"))
        
        
        # 4. Khởi tạo Hệ thống Bộ nhớ (Memory)
        self.context_engine = ContextEngine(self.cfg.config)

        self.guardrail = GuardrailSystem()
        self.monitor = ExecutionMonitor(max_steps=5)
        self.capability_manager = CapabilityManager()


    def _parse_llm_json(self, response_text: str) -> dict:
        """Hàm bổ trợ bóc tách cấu trúc JSON gọi Tool từ LLM"""
        try:
            match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            return json.loads(response_text)
        except Exception:
            # Nếu lỗi, coi như AI muốn tự trả lời trực tiếp mà không gọi tool
            return {"action": "reply", "thought": "Không thể phân tích JSON, tự động chuyển về phản hồi trực tiếp.", "arguments": {}}

    def run(self, session_id: str, user_request: str, operational_mode="hybrid") -> str:
        print(f"\n⚡ [BẮT ĐẦU VÒNG LẶP CHÍNH]")
        self.monitor.reset()

        # =====================================================================
        # BẢO VỆ ĐẦU VÀO (INPUT GUARDRAIL)
        # =====================================================================
        if not self.guardrail.verify_input(user_request):
            return "Yêu cầu bị từ chối hệ thống vì vi phạm chính sách bảo mật thông tin."

        # =====================================================================
        # LUỒNG TƯ DUY & GỌI CÔNG CỤ (THINKING & FUNCTION CALLING LOOP)
        # =====================================================================
        collected_data = "Không có dữ liệu ngoài."
        
        while True:
            # Xây dựng ngữ cảnh động bao gồm lịch sử và trạng thái task hiện tại
            context_data = self.context_engine.build_rich_context(session_id, user_request)
            dynamic_system_prompt = f"{self.cfg.get_system_prompt()}\n\n{context_data['full_prompt_context']}"

            # Router chọn hạ tầng (Có tính năng bảo vệ giữ nguyên ngữ cảnh đa tác vụ)
            target_provider = self.router.route_request(user_request, context_data.get("chat_history", []))
            
            # Gọi LLM sinh mã quyết định hành động (JSON) với cơ chế Fallback của Router
            action_prompt = self._build_action_prompt(user_request)
            
            # Sử dụng hàm thực thi có Fallback bảo vệ hệ thống
            llm_output = self.router.execute_with_fallback(
                provider=target_provider,
                generate_func_local=self.local_llm.generate,
                #generate_func_cloud=self.cloud_llm.generate if self.cloud_llm else self.local_llm.generate,
                generate_func_cloud = self.local_llm.generate,
                prompt=f"{dynamic_system_prompt}\n\n{action_prompt}"
            )
            
            decision = self._parse_llm_json(llm_output)
            tool_name = decision.get("tool_action", "reply")
            tool_args = decision.get("arguments", {})

            # Cập nhật Task Stack dựa vào phân tích ngữ cảnh của AI
            self._handle_task_stack(session_id, decision)

            # Nếu AI bảo chỉ cần trả lời (Không gọi công cụ ngoại vi nữa) -> Thoát vòng lặp tư duy
            if tool_name == "reply":
                break

            # =====================================================================
            # KIỂM DUYỆT HÀNH ĐỘNG (MONITOR INTERRUPTION & HITL)
            # =====================================================================
            execution_status = self.monitor.validate_and_route_execution(tool_name, tool_args, mode=operational_mode)
            
            if execution_status == "reject_loop":
                print("🛑 [Monitor] Phát hiện vòng lặp vô tận! Ép Agent dừng và trả lời.")
                collected_data = "Hệ thống tự động ngắt quãng do phát hiện tiến trình bị lặp lại quá nhiều lần."
                break
                
            elif execution_status == "hitl":
                # Kích hoạt Human-in-the-loop: Chờ con người cấp quyền
                is_approved = self.monitor.request_human_approval(tool_name, tool_args)
                if not is_approved:
                    print("❌ [Human] Người dùng từ chối cấp quyền thực thi công cụ.")
                    user_feedback = input("👉 Nhập lý do từ chối để gửi lại cho AI sửa sai: ")
                    # Nhồi lý do từ chối vào biến thu thập dữ liệu để lượt sau AI biết đường xử lý tiếp
                    user_request = f"{user_request} (Lưu ý từ hệ thống: Hành động {tool_name} trước đó đã bị con người từ chối với lý do: {user_feedback})"
                    continue # Bắt buộc AI phải tư duy lại hướng đi khác dựa trên feedback của người dùng

            # Thực thi công cụ an toàn sau khi đã qua bộ lọc
            print(f"🛠️ [Thực thi an toàn]: Kích hoạt '{tool_name}'...")
            collected_data = self.context_engine.short_mem.tools.execute_tool(tool_name, tool_args)
            
            # Đoạn này có thể lặp tiếp nếu AI muốn gọi chuỗi nhiều công cụ (Multi-step) liên tiếp

        # =====================================================================
        # TỔNG HỢP VÀ PHẢN HỒI (SYNTHESIS)
        # =====================================================================
        synthesis_prompt = f"[Yêu cầu]: {user_request}\n[Kết quả thu thập]: {collected_data}\nHãy viết câu trả lời cuối cùng."
        final_response = self.local_llm.generate(dynamic_system_prompt, synthesis_prompt)

        # =====================================================================
        # BẢO VỆ ĐẦU RA (OUTPUT GUARDRAIL)
        # =====================================================================
        safe_response = self.guardrail.verify_output(final_response)

        # Cập nhật ký ức ngắn hạn
        self.context_engine.update_context(session_id, user_request, safe_response)
        print("✅ [HOÀN THÀNH] Chu kỳ vận hành an toàn kết thúc.")
        return safe_response

    def _handle_task_stack(self, session_id, decision):
        task_action = decision.get("task_action", "none")
        if task_action == "push" and decision.get("task_description"):
            self.context_engine.push_task(session_id, decision.get("task_description"))
        elif task_action == "pop":
            self.context_engine.pop_completed_task(session_id)

    def _build_action_prompt(self, user_request: str) -> str:
            """
            Xây dựng cấu trúc Prompt ép JSON để hướng dẫn LLM đưa ra quyết định hành động,
            quản lý ngăn xếp tác vụ (Task Stack) và gọi công cụ (Tool/Skill).
            """
            # Thu thập toàn bộ danh sách công cụ và kỹ năng hiện có từ CapabilityManager
            # (Đảm bảo bạn đã khởi tạo self.capability_manager trong hàm __init__)
            capabilities_string = self.capability_manager.get_available_capabilities()

            action_prompt = f"""
    === YÊU CẦU HIỆN TẠI CỦA NGƯỜI DÙNG ===
    > {user_request}

    === HỆ THỐNG NĂNG LỰC BẠN CÓ QUYỀN SỬ DỤNG ===
    {capabilities_string}

    === BÀI TOÁN TƯ DUY & ĐIỀU PHỐI (ORCHESTRATION TASK) ===
    Dựa vào lịch sử cuộc trò chuyện, các tác vụ đang treo trong bộ nhớ làm việc (Working Memory) và yêu cầu mới nhất từ người dùng, bạn bắt buộc phải thực hiện phân tích theo logic 3 bước sau:

    1. **Quản lý Ngữ cảnh (Task Stack):** - Nếu người dùng đột ngột giao một việc mới không liên quan đến việc đang làm dở -> Hãy chọn `task_action`: "push" để tạm treo việc cũ lại.
    - Nếu việc xen ngang hiện tại đã được giải quyết triệt để và bạn cần quay lại xử lý nốt việc cũ nằm bên dưới ngăn xếp -> Hãy chọn `task_action`: "pop".
    - Nếu người dùng vẫn đang nói về chủ đề cũ hoặc chỉ đang trò chuyện thông thường -> Hãy chọn `task_action`: "none".

    2. **Lựa chọn Hành động (Action Selection):**
    - Xác định xem bạn có cần dữ liệu từ thế giới thực (web, file, DB) để xử lý yêu cầu không. Nếu có, chọn chính xác tên một công cụ (Tool) phù hợp trong danh sách được cấp.
    - Nếu bạn đã có đủ dữ liệu từ các bước trước hoặc đây là câu hỏi kiến thức phổ thông, hãy chọn hành động là "reply" để chuẩn bị xuất câu trả lời cuối cùng.

    3. **Trích xuất Tham số (Arguments Extraction):**
    - Đóng gói chính xác các tham số cần thiết cho công cụ đã chọn dưới dạng Key-Value dựa theo Schema cấu trúc của công cụ đó.

    === QUY TẮC ĐẦU RA (BẮT BUỘC) ===
    Bạn PHẢI trả lời duy nhất theo định dạng JSON dưới đây. Không được phép kèm theo bất kỳ lời thoại, lời giải thích hay ký tự thừa thãi nào ngoài khối JSON. Sự chính xác của định dạng JSON quyết định sự sống còn của hệ thống!

    {{
        "thought": "Suy nghĩ chi tiết bằng tiếng Việt về hướng giải quyết...",
        "task_action": "push" | "pop" | "none", 
        "task_description": "Mô tả ngắn gọn tác vụ nếu là push, để trống nếu là pop/none",
        "tool_action": "tên_công_cụ_chính_xác" hoặc "reply",
        "arguments": {{
            "tên_tham_số": "giá_trị"
        }}
    }}

    *Lưu ý nghiêm ngặt về các trường:*
    - `task_action`: Chỉ nhận một trong ba giá trị chuỗi cố định: "push", "pop", "none". Không tự chế từ khóa khác.
    - `task_description`: Tóm tắt nhiệm vụ mới bằng một câu ngắn (ví dụ: "Tính toán báo cáo tài chính tháng 5"). Nếu `task_action` là "none" hoặc "pop", trường này BẮT BUỘC phải để chuỗi rỗng "".
    - `tool_action`: Phải trùng khớp 100% về mặt ký tự (case-sensitive) với tên công cụ trong danh sách [TOOLS], hoặc ghi "reply" nếu muốn dừng vòng lặp tư duy để xuất phản hồi cho người dùng.
    - `arguments`: Nếu `tool_action` là "reply", hãy để object rỗng {{}}.
    """
            return action_prompt