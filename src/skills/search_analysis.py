import json
import re
from skills.base_skill import BaseSkill
from ..tools.internet_tool import FetchWebTool  # Hàm cào web thô đã viết ở phần trước

class SearchAnalysisSkill(BaseSkill):
    def __init__(self):
        self.name = "search_and_analysis"
        self.description = "Dùng khi người dùng yêu cầu đọc nội dung, thu thập hoặc phân tích một trang web/đường link cụ thể."
        self.web_tool = FetchWebTool()

    def execute_skill(self, llm, context: dict, user_request: str, system_prompt: str) -> str:
        print(f"🛠️ [Skill: {self.name}] Đang phân tích yêu cầu gọi công cụ...")

        # --- BƯỚC 1: ÉP LLM TRẢ VỀ JSON ĐỂ GỌI TOOL ---
        tool_prompt = f"""
Ngữ cảnh quá khứ: {context['long_term_facts']}
Lịch sử chat: {json.dumps(context['chat_history'], ensure_ascii=False)}
Yêu cầu hiện tại: {user_request}

Bạn có công cụ: '{self.web_tool.name}'. Mô tả: {self.web_tool.description}. Tham số yêu cầu dạng JSON Schema: {self.web_tool.parameters}

Hãy trả về DUY NHẤT một chuỗi JSON theo định dạng sau để kích hoạt công cụ:
{{
    "action": "{self.web_tool.name}",
    "arguments": {{ "url": "đường_dẫn_url_trích_xuất_được" }}
}}
"""
        llm_json_output = llm.generate(system_prompt, tool_prompt)
        
        # Bóc tách JSON an toàn bằng Regex
        match = re.search(r"\{.*\}", llm_json_output, re.DOTALL)
        if not match:
            return "Lỗi: Agent không thể bóc tách cấu trúc lệnh thực thi công cụ."
        
        decision = json.loads(match.group(0))
        url_to_fetch = decision.get("arguments", {}).get("url")

        # --- BƯỚC 2: THỰC THI TOOL LẤY DỮ LIỆU THÔ ---
        print(f"📡 [Skill: {self.name}] Đang cào dữ liệu từ: {url_to_fetch}")
        raw_data = self.web_tool.execute(url=url_to_fetch)

        # --- BƯỚC 3: ĐẨY DỮ LIỆU VÀO LLM LẦN 2 ĐỂ TỔNG HỢP & PHÂN TÍCH SÂU ---
        print(f"📊 [Skill: {self.name}] Đang gửi dữ liệu thô về LLM để tổng hợp...")
        synthesis_prompt = f"""
Yêu cầu của khách hàng: {user_request}
Dữ liệu thô thu thập được từ hệ thống:
{raw_data}

Nhiệm vụ: Hãy phân tích kỹ dữ liệu trên và viết bài tóm tắt chất lượng cao theo đúng Soul và Rules của bạn.
"""
        final_report = llm.generate(system_prompt, synthesis_prompt)
        return final_report