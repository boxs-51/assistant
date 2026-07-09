import asyncio
import os
import time
import base64
import io
import json
import re
import threading
from typing import Any, Dict, List, Tuple, Optional, Union
import httpx
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import messagebox, scrolledtext

class GeminiGatewayGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Gemini Gateway Client (Bản độc lập - Hỗ trợ Context & Stream)")
        self.root.geometry("1400x900")
        self.root.configure(bg="#f4f6f9")
        self.rendered_image = None
        
        # 🧠 BỘ NHỚ NGỮ CẢNH (CONTEXT MEMORY) TRONG SESSION CHAT
        self.conversation_history: List[Dict[str, Any]] = [
            {
                "role": "system",
                "content": "Bạn là trợ lý phân tích dữ liệu chuyên nghiệp. Khi xuất dữ liệu so sánh, hãy luôn ưu tiên định dạng bảng Markdown."
            },
            {
                "role": "assistant",
                "parts": [{"text": "Tôi đã hiểu. Tôi sẽ luôn sử dụng bảng Markdown cho dữ liệu phân tích và so sánh."}]
            }
        ]

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
        
        self.clear_btn = tk.Button(top_frame, text="Xóa Context", font=("Helvetica", 10, "bold"), bg="#dc3545", fg="white", command=self.clear_context, width=12)
        self.clear_btn.grid(row=0, column=4, padx=5, pady=10)
        
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

        left_frame = tk.LabelFrame(output_frame, text=" 📝 Nội dung hội thoại (Markdown / Tables) ", font=("Helvetica", 10, "bold"), bg="#ffffff")
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))

        self.text_area = scrolledtext.ScrolledText(left_frame, wrap=tk.WORD, font=("Consolas", 11))
        self.text_area.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Cấu hình các tag định dạng hiển thị
        self.text_area.tag_configure("bold", font=("Helvetica", 11, "bold"))
        self.text_area.tag_configure("code_block", font=("Consolas", 10), background="#272822", foreground="#f8f8f2", lmargin1=15, lmargin2=15, rmargin=15, spacing1=2, spacing3=2, wrap=tk.WORD)
        self.text_area.tag_configure("user_tag", font=("Helvetica", 11, "bold"), foreground="#007bff")
        self.text_area.tag_configure("ai_tag", font=("Helvetica", 11, "bold"), foreground="#28a745")
        self.text_area.tag_configure("plain_text", font=("Helvetica", 11), spacing3=5)
        self.text_area.tag_configure("table_block", font=("Consolas", 11), background="#eef4ff", lmargin1=15, lmargin2=15, rmargin=15, spacing3=5)

        right_frame = tk.LabelFrame(output_frame, text=" 🖼️ Hình ảnh phân tích inline từ Gemini Interpreter ", font=("Helvetica", 10, "bold"), bg="#ffffff")
        right_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))

        self.image_label = tk.Label(right_frame, text="[Chưa có hình ảnh]", font=("Helvetica", 12, "italic"), bg="#eaedf2", fg="#6c757d")
        self.image_label.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # --- KHUNG LOGGING ---
        log_container = tk.LabelFrame(main_frame, text=" 🔎 Logs payload JSON truyền nhận ", font=("Helvetica", 10, "bold"), bg="#ffffff")
        log_container.grid(row=1, column=0, sticky="nsew")
        log_container.columnconfigure(0, weight=1)
        log_container.columnconfigure(1, weight=1)
        log_container.rowconfigure(1, weight=1)

        tk.Label(log_container, text="Dữ liệu gửi đi (Request Payload)", font=("Helvetica", 9, "bold"), bg="#ffffff").grid(row=0, column=0, padx=5, pady=2, sticky="w")
        self.raw_log_area = scrolledtext.ScrolledText(log_container, wrap=tk.WORD, font=("Consolas", 9), height=10, bg="#2b2b2b", fg="#a9b7c6")
        self.raw_log_area.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)

        tk.Label(log_container, text="Dữ liệu thô nhận về (API Response)", font=("Helvetica", 9, "bold"), bg="#ffffff").grid(row=0, column=1, padx=5, pady=2, sticky="w")
        self.dto_log_area = scrolledtext.ScrolledText(log_container, wrap=tk.WORD, font=("Consolas", 9), height=10, bg="#e8e8e8")
        self.dto_log_area.grid(row=1, column=1, sticky="nsew", padx=5, pady=5)

    def clear_context(self):
        """Xóa lịch sử hội thoại, reset bộ nhớ"""
        self.conversation_history = self.conversation_history[:2] # Giữ lại system-init nếu có
        self.text_area.delete("1.0", tk.END)
        self.image_label.config(image="", text="[Đã xóa ngữ cảnh dữ liệu]")
        messagebox.showinfo("Hệ thống", "Đã làm mới bộ nhớ Context thành công!")

    def _is_markdown_table(self, text: str) -> bool:
        lines = text.strip().split('\n')
        if len(lines) < 2: return False
        header = lines[0].strip()
        separator = lines[1].strip()
        return header.startswith('|') and header.endswith('|') and separator.startswith('|') and separator.endswith('|') and '-' in separator

    def append_text_smooth(self, text: str, tag: str = "plain_text", delay: float = 0.005):
        """
        In chữ chạy mượt từng cụm/từ mà không gây block Main Loop UI của Tkinter.
        """
        def _queue_char(chars_left):
            if chars_left and self.root.winfo_exists():
                # Lấy 3 kí tự in 1 lần để tăng hiệu suất nếu chuỗi dài
                chunk = chars_left[:3]
                self.text_area.insert(tk.END, chunk, tag)
                self.text_area.see(tk.END)
                self.root.after(int(delay * 1000), lambda: _queue_char(chars_left[3:]))

        _queue_char(text)

    def parse_and_display_chunk(self, text_chunk: str):
        """Phân tích nhanh chunk text stream để gán tag style thích hợp"""
        tag = "plain_text"
        if self._is_markdown_table(text_chunk):
            tag = "table_block"
        elif "```" in text_chunk or text_chunk.startswith("    "):
            tag = "code_block"
            
        # Làm sạch kí tự bọc markdown nếu cần
        clean_text = text_chunk.replace("```python", "").replace("```text", "").replace("```", "")
        if clean_text:
            self.append_text_smooth(clean_text, tag)

    def log_data(self, req_data: Any, res_data: Any):
        if req_data:
            self.raw_log_area.insert(tk.END, json.dumps(req_data, indent=2, ensure_ascii=False) + "\n\n")
            self.raw_log_area.see(tk.END)
        if res_data:
            # Rút gọn chuỗi base64 khi hiển thị ở khung log tránh lag giao diện
            log_res = json.loads(json.dumps(res_data))
            try:
                for parts in log_res.get("candidates", [{}])[0].get("content", {}).get("parts", []):
                    if "inlineData" in parts and "data" in parts["inlineData"]:
                        parts["inlineData"]["data"] = f"{parts['inlineData']['data'][:30]}... [Truncated Base64]"
            except Exception:
                pass
            self.dto_log_area.insert(tk.END, json.dumps(log_res, indent=2, ensure_ascii=False) + "\n\n")
            self.dto_log_area.see(tk.END)

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
        
        self.submit_btn.config(state=tk.DISABLED, text="Đang xử lý...")
        self.image_label.config(image="", text="[Chờ xử lý từ Gemini...]")
        
        # Thêm câu hỏi hiện tại vào text view chính
        self.text_area.insert(tk.END, "\n👤 Bạn: ", "user_tag")
        self.text_area.insert(tk.END, f"{prompt}\n", "plain_text")
        self.text_area.insert(tk.END, "🤖 AI: ", "ai_tag")
        
        use_stream = self.is_stream_var.get()
        threading.Thread(target=lambda: asyncio.run(self.execute_api_flow(prompt, use_stream)), daemon=True).start()

    async def execute_api_flow(self, prompt: str, use_stream: bool):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            self.root.after(0, lambda: messagebox.showerror("Lỗi", "Vui lòng cấu hình môi trường GEMINI_API_KEY"))
            self.root.after(0, lambda: self.submit_btn.config(state=tk.NORMAL, text="Gửi Yêu Cầu AI"))
            return

        model = "gemini-2.5-flash"
        
        # 🔗 CẬP NHẬT CONTEXT HỘI THOẠI
        self.conversation_history.append({
            "role": "user",
            "parts": [{"text": prompt}]
        })

        # 🛠️ TỰ XÂY DỰNG PAYLOAD KHÔNG DÙNG DTO NGOÀI
        payload = {
            "contents": self.conversation_history,
            "tools": [{
                "codeExecution": {}  # Bật trình thông dịch code Python nội tại
            }]
        }
        
        self.root.after(0, lambda: self.log_data(req_data=payload, res_data=None))

        method = "streamGenerateContent" if use_stream else "generateContent"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:{method}?key={api_key}"
        
        async with httpx.AsyncClient() as client:
            try:
                full_assistant_text = ""
                
                if use_stream:
                    # KỊCH BẢN 1: STREAM CHỮ CHẠY
                    async with client.stream("POST", url, json=payload, timeout=60.0) as response:
                        response.raise_for_status()
                        
                        buffer = ""
                        async for chunk in response.aiter_text():
                            buffer += chunk
                            # Cắt các cụm JSON hợp lệ trong chuỗi Stream của Google
                            while "{" in buffer and "}" in buffer:
                                try:
                                    # Tìm kiếm điểm kết thúc khớp nối JSON Object
                                    start_idx = buffer.find("{")
                                    # Giải pháp bóc tách thô một block JSON
                                    bracket_count = 0
                                    end_idx = -1
                                    for i in range(start_idx, len(buffer)):
                                        if buffer[i] == '{': bracket_count += 1
                                        elif buffer[i] == '}':
                                            bracket_count -= 1
                                            if bracket_count == 0:
                                                end_idx = i + 1
                                                break
                                    
                                    if end_idx == -1: break # Chưa đủ cụm đóng json
                                    
                                    raw_obj = buffer[start_idx:end_idx]
                                    buffer = buffer[end_idx:]
                                    
                                    obj_json = json.loads(raw_obj.strip(", \n\r"))
                                    self.root.after(0, lambda o=obj_json: self.log_data(req_data=None, res_data=o))
                                    
                                    # Trích xuất nội dung text part
                                    parts = obj_json.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                                    for part in parts:
                                        if "text" in part:
                                            txt = part["text"]
                                            full_assistant_text += txt
                                            self.root.after(0, lambda t=txt: self.parse_and_display_chunk(t))
                                        
                                        # Trích xuất nếu có hình ảnh sinh ra từ code thông dịch
                                        if "inlineData" in part:
                                            b64 = part["inlineData"].get("data")
                                            if b64:
                                                self.root.after(0, lambda b=b64: self.render_base64_image(b))
                                except Exception:
                                    break
                else:
                    # KỊCH BẢN 2: CHẠY KHÔNG STREAM
                    response = await client.post(url, json=payload, timeout=60.0)
                    response.raise_for_status()
                    res_json = response.json()
                    
                    self.root.after(0, lambda r=res_json: self.log_data(req_data=None, res_data=r))
                    
                    candidates = res_json.get("candidates", [{}])
                    parts = candidates[0].get("content", {}).get("parts", [])
                    
                    for part in parts:
                        if "text" in part:
                            full_assistant_text += part["text"]
                            self.root.after(0, lambda t=part["text"]: self.parse_and_display_chunk(t))
                        if "inlineData" in part:
                            b64 = part["inlineData"].get("data")
                            if b64:
                                self.root.after(0, lambda b=b64: self.render_base64_image(b))
                
                # 💾 LƯU PHẢN HỒI CỦA AI VÀO NGỮ CẢNH ĐỂ TIẾP TỤC ĐỐI THOẠI
                if full_assistant_text:
                    self.conversation_history.append({
                        "role": "model",
                        "parts": [{"text": full_assistant_text}]
                    })
                            
            except Exception as e:
                self.root.after(0, lambda err=str(e): self.text_area.insert(tk.END, f"\n❌ Lỗi API Gateway: {err}\n"))
            finally:
                self.root.after(0, lambda: self.submit_btn.config(state=tk.NORMAL, text="Gửi Yêu Cầu AI"))
                self.root.after(0, lambda: self.text_area.insert(tk.END, "\n" + "-"*40 + "\n"))

if __name__ == "__main__":
    # Cấu hình biến môi trường test trực tiếp tại đây nếu cần
    # os.environ["GEMINI_API_KEY"] = "AIzaSy..."
    
    root_window = tk.Tk()
    app = GeminiGatewayGUI(root_window)
    root_window.mainloop()