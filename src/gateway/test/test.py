import asyncio
import os
import time
import base64
import io
import uuid
import json
import re
import threading
from typing import Any, Dict, List, Tuple, Optional, Literal, Union, AsyncGenerator

import httpx
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import messagebox, scrolledtext

from ..schemas import (
    GatewayAttachment, ImageContent, MessageContentPart, GatewayMessage,
    GatewayChoice, GatewayResponse, TextContent, MessageContentType, GatewayChatRequest,
    GatewayToolDefinition, ToolType, RequestConfig
)
from ..routing.providers.google.converters.chats.request import RequestChats
from ..routing.providers.google.converters.chats.response import ResponseChats

class GeminiGatewayGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Gemini Gateway Client (Chuẩn hóa DTO & Giao diện nâng cao)")
        self.root.geometry("1400x900")
        self.root.configure(bg="#f4f6f9")
        self.rendered_image = None
        
        # Biến trạng thái để quản lý tag khi STREAMING code block
        self.current_stream_tag = "plain_text"

        # --- KHUNG ĐIỀU KHIỂN PHÍA TRÊN ---
        top_frame = tk.Frame(root, bg="#ffffff", bd=1, relief=tk.SOLID)
        top_frame.pack(fill=tk.X, padx=15, pady=10)

        tk.Label(top_frame, text="Prompt:", font=("Helvetica", 11, "bold"), bg="#ffffff").grid(row=0, column=0, padx=10, pady=10, sticky="w")
        
        self.prompt_entry = tk.Entry(top_frame, font=("Helvetica", 11), width=50)
        self.prompt_entry.insert(0, "Tạo bảng doanh thu 3 tháng đầu năm và vẽ biểu đồ hình cột.")
        self.prompt_entry.grid(row=0, column=1, padx=5, pady=10, sticky="we")

        self.is_stream_var = tk.BooleanVar(value=True)  
        self.stream_check = tk.Checkbutton(top_frame, text="Bật Streaming", font=("Helvetica", 10, "bold"), variable=self.is_stream_var, bg="#ffffff", activebackground="#ffffff")
        self.stream_check.grid(row=0, column=2, padx=10, pady=10)

        self.submit_btn = tk.Button(top_frame, text="Gửi Yêu Cầu AI", font=("Helvetica", 11, "bold"), bg="#007bff", fg="white", command=self.start_request_thread, width=15)
        self.submit_btn.grid(row=0, column=3, padx=10, pady=10)
        top_frame.columnconfigure(1, weight=1)

        # --- KHUNG CHỨA NỘI DUNG CHÍNH ---
        main_frame = tk.Frame(root, bg="#f4f6f9")
        main_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=(0, 10))
        main_frame.rowconfigure(0, weight=2) 
        main_frame.rowconfigure(1, weight=1) 
        main_frame.columnconfigure(0, weight=1)

        # --- KHUNG OUTPUT (VĂN BẢN VÀ HÌNH ẢNH) ---
        output_frame = tk.Frame(main_frame, bg="#f4f6f9")
        output_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        output_frame.columnconfigure(0, weight=1)
        output_frame.columnconfigure(1, weight=1)
        output_frame.rowconfigure(0, weight=1)

        left_frame = tk.LabelFrame(output_frame, text=" 📝 Nội dung trả về (Văn bản, Markdown, Code) ", font=("Helvetica", 10, "bold"), bg="#ffffff")
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))

        self.text_area = scrolledtext.ScrolledText(left_frame, wrap=tk.WORD, font=("Consolas", 11))
        self.text_area.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # --- Cấu hình các tag định dạng cho text_area ---
        self.text_area.tag_configure("bold", font=("Helvetica", 11, "bold"))
        self.text_area.tag_configure("code_block", font=("Consolas", 10), background="#272822", foreground="#f8f8f2", lmargin1=15, lmargin2=15, rmargin=15, spacing1=2, spacing3=2, wrap=tk.WORD)
        self.text_area.tag_configure("output_block", font=("Consolas", 10), background="#f0f0f0", foreground="#333333", lmargin1=15, lmargin2=15, rmargin=15, spacing1=2, spacing3=2, wrap=tk.WORD)
        self.text_area.tag_configure("plain_text", font=("Helvetica", 11), spacing3=5)
        self.text_area.tag_configure("table_block", font=("Consolas", 11), background="#eef4ff", lmargin1=15, lmargin2=15, rmargin=15, spacing3=5)

        right_frame = tk.LabelFrame(output_frame, text=" 🖼️ Hình ảnh đính kèm (Attachment) ", font=("Helvetica", 10, "bold"), bg="#ffffff")
        right_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))

        self.image_label = tk.Label(right_frame, text="[Chưa có hình ảnh]", font=("Helvetica", 12, "italic"), bg="#eaedf2", fg="#6c757d")
        self.image_label.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # --- KHUNG LOGGING ---
        log_container = tk.LabelFrame(main_frame, text=" 🔎 Dữ liệu chi tiết (Raw vs DTO) ", font=("Helvetica", 10, "bold"), bg="#ffffff")
        log_container.grid(row=1, column=0, sticky="nsew")
        log_container.columnconfigure(0, weight=1)
        log_container.columnconfigure(1, weight=1)
        log_container.rowconfigure(1, weight=1)

        tk.Label(log_container, text="Dữ liệu thô từ API (Raw)", font=("Helvetica", 9, "bold"), bg="#ffffff").grid(row=0, column=0, padx=5, pady=2, sticky="w")
        self.raw_log_area = scrolledtext.ScrolledText(log_container, wrap=tk.WORD, font=("Consolas", 9), height=10, bg="#2b2b2b", fg="#a9b7c6")
        self.raw_log_area.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)

        tk.Label(log_container, text="Dữ liệu chuẩn hóa (DTO)", font=("Helvetica", 9, "bold"), bg="#ffffff").grid(row=0, column=1, padx=5, pady=2, sticky="w")
        self.dto_log_area = scrolledtext.ScrolledText(log_container, wrap=tk.WORD, font=("Consolas", 9), height=10, bg="#e8e8e8")
        self.dto_log_area.grid(row=1, column=1, sticky="nsew", padx=5, pady=5)

    def _is_markdown_table(self, text: str) -> bool:
        """Kiểm tra xem một đoạn văn bản có phải là bảng Markdown hay không."""
        lines = text.strip().split('\n')
        if len(lines) < 2:
            return False
        # Dòng tiêu đề và dòng phân cách phải có ký tự '|'
        header = lines[0].strip()
        separator = lines[1].strip()
        return header.startswith('|') and header.endswith('|') and separator.startswith('|') and separator.endswith('|') and '-' in separator

    def _clean_technical_text(self, text: str) -> str:
        """Làm sạch các nhãn kỹ thuật khỏi văn bản trước khi hiển thị."""
        text = text.replace("```python", "").replace("```text", "").replace("```", "")
        text = text.replace("# [AI Executed Code]", "").replace("# [Execution Output]", "")
        return text.strip()

    def append_text_by_format(self, text: str, text_format: str, language: Optional[str] = None):
        """
        Hiển thị văn bản cho cả STREAM và NON-STREAM.
        Dựa vào 'format' từ DTO để chọn tag giao diện phù hợp.
        """
        tag = "plain_text" # Mặc định

        if text_format == "code":
            # Phân biệt code thực thi và output log dựa trên ngôn ngữ
            tag = "output_block" if language == "text" else "code_block"
        elif text_format == "structured":
            if self._is_markdown_table(text):
                tag = "table_block"
            else:
                tag = "plain_text"

        cleaned_text = self._clean_technical_text(text)
        if cleaned_text:
            self.text_area.insert(tk.END, cleaned_text + "\n\n", tag)
            self.text_area.see(tk.END)

    def parse_and_display_content(self, content_parts: List[MessageContentPart]):
        """
        Hàm chuẩn hóa cuối cùng phía Client.
        Duyệt qua danh sách các content_part đã được server bóc tách,
        và hiển thị từng phần với định dạng chính xác.
        Áp dụng cho cả non-stream và stream.
        """
        attachments_to_render = []

        for part in content_parts:
            if isinstance(part.data, TextContent):
                # Lấy thông tin từ DTO và gọi hàm hiển thị thống nhất
                self.append_text_by_format(
                    text=part.data.data,
                    text_format=part.data.format,
                    language=part.data.language
                )
            elif isinstance(part.data, ImageContent) and part.data.attachment:
                attachments_to_render.append(part.data.attachment)

        # Hiển thị các hình ảnh đính kèm (nếu có)
        for att in attachments_to_render:
            if att.base64_data:
                self.root.after(0, lambda b=att.base64_data: self.render_base64_image(b))

    def log_data(self, raw_data: Any, dto_data: Any):
        """Hiển thị dữ liệu thô và DTO đã chuẩn hóa ra vùng log."""
        if raw_data:
            self.raw_log_area.insert(tk.END, self._format_log_json(raw_data) + "\n\n")
            self.raw_log_area.see(tk.END)
        if dto_data:
            self.dto_log_area.insert(tk.END, self._format_log_json(dto_data) + "\n\n")
            self.dto_log_area.see(tk.END)

    def _truncate_long_strings(self, data: Any, parent_key: str = "") -> Any:
        if isinstance(data, dict):
            return {k: self._truncate_long_strings(v, k) for k, v in data.items()}
        if isinstance(data, list):
            return [self._truncate_long_strings(v, parent_key) for v in data]
        if parent_key == 'base64_data' and isinstance(data, str) and len(data) > 100:
            return f"{data[:20]}... (truncated) ...{data[-20:]}"
        return data

    def _format_log_json(self, data: Any) -> str:
        return json.dumps(self._truncate_long_strings(data), indent=2, ensure_ascii=False)

    def render_base64_image(self, base64_str: str):
        try:
            image_bytes = base64.b64decode(base64_str)
            pil_img = Image.open(io.BytesIO(image_bytes))
            pil_img.thumbnail((500, 500))
            self.rendered_image = ImageTk.PhotoImage(pil_img)
            self.image_label.config(image=self.rendered_image, text="")
        except Exception as e:
            self.image_label.config(text=f"❌ Lỗi hiển thị ảnh: {str(e)}")

    def start_request_thread(self):
        prompt = self.prompt_entry.get().strip()
        if not prompt: return
        self.submit_btn.config(state=tk.DISABLED, text="Đang chạy...")
        self.text_area.delete("1.0", tk.END)
        self.raw_log_area.delete("1.0", tk.END)
        self.dto_log_area.delete("1.0", tk.END)
        self.image_label.config(image="", text="[Đang xử lý kết nối...]")
        
        self.current_stream_tag = "plain_text" # Reset tag pointer
        use_stream = self.is_stream_var.get()
        threading.Thread(target=lambda: asyncio.run(self.execute_api_flow(prompt, use_stream)), daemon=True).start()

    async def execute_api_flow(self, prompt: str, use_stream: bool):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            self.root.after(0, lambda: messagebox.showerror("Lỗi", "Vui lòng cấu hình GEMINI_API_KEY"))
            self.root.after(0, lambda: self.submit_btn.config(state=tk.NORMAL, text="Gửi Yêu Cầu AI"))
            return

        model = "gemini-1.5-flash" # Đổi sang model mới hơn để có kết quả tốt hơn

        # FIX 1: Đưa tools về định dạng LIST đúng cấu trúc quy định của GatewayChatRequest
        gateway_request = GatewayChatRequest(
            model=model,
            messages=[
                GatewayMessage(role="system", content="Bạn là trợ lý phân tích dữ liệu. Luôn dùng bảng Markdown."),
                GatewayMessage(role="user", content=prompt)
            ],
            tools=[
                GatewayToolDefinition(
                    name="code_execution",  
                    description="Kích hoạt trình thông dịch code Python của Gemini",
                    tool_type=ToolType.NATIVE  
                ),
                GatewayToolDefinition(
                    name="google_search",  
                    description="Tìm kiếm với Google",
                    tool_type=ToolType.NATIVE  
                )
            ],
            config=RequestConfig(
                stream=use_stream
            )
            
        )
        request = RequestChats()
        payload = request.adapt_chat(gateway_request.model_dump())

        method = "streamGenerateContent" if use_stream else "generateContent"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:{method}?key={api_key}"
        
        async with httpx.AsyncClient() as client:
            try:
                response_chat=ResponseChats()
                if use_stream:
                    # KỊCH BẢN 1: CHẠY STREAMING
                    async with client.stream("POST", url, json=payload, timeout=60.0) as response:
                        response.raise_for_status()
                        
                        async for chunk_dto in response_chat.adapt_chat_stream(response):
                            # Log cấu trúc DTO nhận được từ stream 
                            self.root.after(0, lambda d=chunk_dto: self.log_data(
                                raw_data=None, 
                                dto_data=d.model_dump(exclude_none=True)
                            ))

                            # Tận dụng trực tiếp "content_parts" đã được bóc tách từ server
                            if "content_parts" in chunk_dto.metadata:
                                content_parts = [MessageContentPart.model_validate(p) for p in chunk_dto.metadata["content_parts"]]
                                self.root.after(0, lambda parts=content_parts: self.parse_and_display_content(parts))

                else:
                    # KỊCH BẢN 2: CHẠY NON-STREAM
                    response = await client.post(url, json=payload, timeout=60.0)
                    response.raise_for_status()
                    raw_json = response.json()
                    output_dto = await response_chat.adapt_chat(response)

                    self.root.after(0, lambda r=raw_json, d=output_dto: self.log_data(
                        raw_data=r, dto_data=d.model_dump(exclude_none=True)
                    ))
                    
                    # Lấy danh sách content parts từ DTO và đưa vào hàm hiển thị
                    if output_dto.choices:
                        self.root.after(0, lambda parts=output_dto.choices[0].message.content: self.parse_and_display_content(parts))
                            
            except Exception as e:
                self.root.after(0, lambda err=str(e): self.text_area.insert(tk.END, f"\n❌ Lỗi hệ thống: {err}\n"))
            finally:
                self.root.after(0, lambda: self.submit_btn.config(state=tk.NORMAL, text="Gửi Yêu Cầu AI"))

if __name__ == "__main__":
    root_window = tk.Tk()
    app = GeminiGatewayGUI(root_window)
    root_window.mainloop()
