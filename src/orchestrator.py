import json
import re
from src.config_loader import ConfigLoader
from src.router import SmartRouter
from src.tools.registry import ToolRegistry
from src.llm.ollama_client import OllamaClient
# từ src.llm.openai_client import OpenAIClient (Nếu dùng thêm Cloud)
from src.context_engine.memory_manager import ContextEngine


class AgentOrchestrator:
    def __init__(self):
        # 1. Khởi tạo cấu hình và định tuyến
        self.cfg = ConfigLoader()
        self.router = SmartRouter(self.cfg.config)
        
        # 2. Khởi tạo các kết nối LLM (Ví dụ với Local)
        self.local_llm = OllamaClient(self.cfg.get_provider_config("local"))
        
        # 3. Khởi tạo Bộ quản lý Công cụ (Tools)
        self.tool_registry = ToolRegistry()
        
        # 4. Khởi tạo Hệ thống Bộ nhớ (Memory)
        self.context_engine = ContextEngine(self.cfg.config)
        self.short_memory = ShortTermMemory(
            max_history=self.cfg.config["memory_settings"]["short_term"]["max_history"]
        )
        self.long_memory = LongTermMemory(
            storage_path=self.cfg.config["memory_settings"]["long_term"]["storage_path"]
        )

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

    def run(self, session_id: str, user_request: str) -> str:
        print(f"\n⚡ [BẮT ĐẦU] Nhận yêu cầu từ Session '{session_id}': {user_request}")

        # =====================================================================
        # BƯỚC 1: TRUY XUẤT KÝ ỨC (MEMORY RETRIEVAL)
        # =====================================================================
        # 1.1 Lấy lịch sử chat ngắn hạn của phiên này
        chat_history = self.short_memory.get_context(session_id)
        
        # 1.2 Tìm kiếm thông tin/sự kiện liên quan trong quá khứ (Long-term)
        past_knowledge = self.long_memory.search_relevant_facts(user_request)
        
        # 1.3 Xây dựng System Prompt động (Gộp Soul, Rules và Ký ức dài hạn)
        base_system_prompt = self.cfg.get_system_prompt()
        dynamic_system_prompt = f"""
{base_system_prompt}

[KÝ ỨC DÀI HẠN LIÊN QUAN TỪ QUÁ KHỨ]:
{past_knowledge}
"""

        # =====================================================================
        # BƯỚC 2: ĐỊNH TUYẾN & ĐƯA RA QUYẾT ĐỊNH HÀNH ĐỘNG (THINKING & FUNCTION CALLING)
        # =====================================================================
        target_provider = self.router.route_request(user_request)
        print(f"-> [Định tuyến]: Chọn hạ tầng '{target_provider}' để xử lý tư duy.")

        tool_name = "reply"
        tool_args = {}
        
        if target_provider == "local":
            # Lấy danh sách mô tả công cụ để ép vào prompt cho Local LLM
            tools_list_string = self.tool_registry.get_local_tools_prompt()
            
            # Thiết lập Prompt yêu cầu AI suy nghĩ xem có cần dùng Tool dựa trên cả Lịch sử chat
            action_prompt = f"""
[Lịch sử cuộc trò chuyện ngắn hạn]:
{json.dumps(chat_history, ensure_ascii=False, indent=2)}

[Yêu cầu hiện tại của người dùng]:
{user_request}

[Danh sách công cụ bạn có quyền sử dụng]:
{tools_list_string}

BÀI TOÁN: Dựa vào lịch sử chat và yêu cầu hiện tại, hãy quyết định xem có cần dùng công cụ nào để lấy dữ liệu không.
Bạn PHẢI trả lời duy nhất theo định dạng JSON sau, không kèm lời thoại nào khác:
{{
    "thought": "Suy nghĩ của bạn (ví dụ: Người dùng hỏi về X, lịch sử chưa có thông tin, tôi cần dùng công cụ Y...)",
    "action": "tên_công_cụ_chính_xác" hoặc "reply" nếu tự trả lời được luôn,
    "arguments": {{ "tên_tham_số": "giá_trị" }}
}}
"""
            llm_output = self.local_llm.generate(dynamic_system_prompt, action_prompt)
            decision = self._parse_llm_json(llm_output)
            
            tool_name = decision.get("action", "reply")
            tool_args = decision.get("arguments", {})
            print(f"-> [AI Suy nghĩ]: {decision.get('thought')}")

        else:
            # Xử lý luồng Cloud (OpenAI Native Function Calling) tương tự tại đây
            pass

        # =====================================================================
        # BƯỚC 3: THỰC THI HÀNH ĐỘNG (ACTING / TOOL EXECUTION)
        # =====================================================================
        collected_data = "Không có dữ liệu ngoài."
        
        if tool_name != "reply":
            print(f"-> [AI Quyết định]: Kích hoạt công cụ '{tool_name}'")
            # Chạy công cụ và lấy kết quả thô về (Ví dụ: Cào web hoặc truy vấn DB)
            collected_data = self.tool_registry.execute_tool(tool_name, tool_args)
        else:
            print("-> [AI Quyết định]: Không cần công cụ, tự trả lời dựa trên tri thức sẵn có.")

        # =====================================================================
        # BƯỚC 4: TỔNG HỢP, PHÂN TÍCH VÀ TẠO PHẢN HỒI (SYNTHESIS)
        # =====================================================================
        print("-> [Tổng hợp]: Đang phân tích dữ liệu và soạn thảo tin nhắn thông báo...")
        
        synthesis_prompt = f"""
[Lịch sử cuộc trò chuyện ngắn hạn]:
{json.dumps(chat_history, ensure_ascii=False, indent=2)}

[Yêu cầu hiện tại của người dùng]: {user_request}
[Dữ liệu thu thập được từ công cụ]: 
{collected_data}

NHIỆM VỤ: Hãy dựa vào dữ liệu thu thập được và ngữ cảnh cuộc trò chuyện để viết một phản hồi hoàn chỉnh, tóm tắt thông tin mạch lạc, định dạng tin nhắn dạng Markdown đẹp mắt để gửi cho người dùng.
"""
        # Gọi LLM lần cuối để tạo câu trả lời dạng văn bản tự nhiên (tuân thủ Soul/Rules trong dynamic_system_prompt)
        final_response = self.local_llm.generate(dynamic_system_prompt, synthesis_prompt)

        # =====================================================================
        # BƯỚC 5: CẬP NHẬT KÝ ỨC (MEMORY UPDATE)
        # =====================================================================
        # 5.1 Cập nhật Bộ nhớ ngắn hạn (Lưu lại lượt tương tác này vào lịch sử chat)
        self.short_memory.add_message(session_id, "user", user_request)
        self.short_memory.add_message(session_id, "assistant", final_response)
        
        # 5.2 (Tùy chọn) Học hỏi/Lưu fact vào Bộ nhớ dài hạn nếu phát hiện thông tin quan trọng
        # Ví dụ: Nếu người dùng nói "Tôi tên là Nam", hệ thống có thể bóc tách để lưu lại:
        # self.long_memory.learn_new_fact("Tên người dùng", "Nam")
        
        print("✅ [HOÀN THÀNH] Đã gửi tin nhắn thông báo và lưu lại ký ức cuộc gọi.")
        return final_response