import asyncio
import os
import time
import base64
import io
import uuid
import json
import threading
from typing import Any, Dict, List, Tuple, Optional, Literal, Union, AsyncGenerator
from pydantic import BaseModel, Field

import httpx
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import messagebox, scrolledtext

# =========================================================================
# 1. ĐỊNH NGHĨA CÁC DTO (CẢ STREAM VÀ NON-STREAM ĐỒNG BỘ)
# =========================================================================

class GatewayUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

class GatewayStreamDelta(BaseModel):
    content: Optional[str] = None
    role: Optional[str] = None
    tool_calls: Optional[List[Any]] = None

class GatewayStreamChoice(BaseModel):
    index: int
    delta: GatewayStreamDelta
    finish_reason: Optional[str] = None

class GatewayStreamChunk(BaseModel):
    id: str = Field(default_factory=str)
    model: str
    choices: List[GatewayStreamChoice]
    object: str = "gateway_stream_chunk"
    created: int = Field(default_factory=lambda: int(time.time()))
    provider: str
    usage: Optional[GatewayUsage] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

# DTO phục vụ luồng Non-stream cũ để so sánh
class FileMetadata(BaseModel):
    created_at: Optional[int] = None

class GatewayAttachment(BaseModel):
    id: Optional[str] = None
    mime_type: str
    base64_data: Optional[str] = None
    metadata: FileMetadata = Field(default_factory=FileMetadata)

class ImageContent(BaseModel):
    attachment: GatewayAttachment

class MessageContentPart(BaseModel):
    type: Literal["text", "image", "audio", "video", "file", "url"]
    text: Optional[str] = None
    image: Optional[ImageContent] = None

class GatewayMessage(BaseModel):
    role: str
    content: List[MessageContentPart]

class GatewayChoice(BaseModel):
    index: int
    message: GatewayMessage
    finish_reason: Optional[str] = None

class GatewayResponse(BaseModel):
    id: str
    model: str
    choices: List[GatewayChoice]
    usage: GatewayUsage
    provider: str

# =========================================================================
# 2. BỘ ADAPTER XỬ LÝ SONG SONG CẢ HAI PHƯƠNG THỨC
# =========================================================================

class DualGeminiAdapter:
    def __init__(self, provider_name: str = "gemini"):
        self.provider_name = provider_name

    def _parse_part_to_text_and_attachment(self, part: Dict[str, Any]) -> tuple[str, Optional[Dict[str, Any]]]:
        text_delta = ""
        attachment_data = None

        if "text" in part:
            text_delta = part["text"]
        elif "inlineData" in part:
            inline_data = part["inlineData"]
            mime_type = inline_data.get("mimeType", "application/octet-stream")
            attachment_data = {
                "id": f"att-{uuid.uuid4()}",
                "mime_type": mime_type,
                "base64_data": inline_data.get("data", ""),
                "metadata": {"created_at": int(time.time())}
            }
            text_delta = f"\n\n*[Hệ thống: Nhận tệp đính kèm {mime_type}]*\n"
        elif "executableCode" in part:
            text_delta = f"\n\n```python\n# [AI Executed Code]\n{part['executableCode'].get('code', '')}\n```"
        elif "codeExecutionResult" in part:
            text_delta = f"\n\n```text\n# [Execution Output]\n{part['codeExecutionResult'].get('output', '')}\n```"

        return text_delta, attachment_data

    async def adapt_chat_stream(self, response: httpx.Response, request_model: str) -> AsyncGenerator[GatewayStreamChunk, None]:
        stream_id = f"chatcmpl-{uuid.uuid4()}"
        buffer = ""
        
        async for chunk_text in response.aiter_text():
            buffer += chunk_text
            while True:
                buffer = buffer.lstrip()
                if buffer.startswith('['): buffer = buffer[1:].lstrip()
                if buffer.startswith(','): buffer = buffer[1:].lstrip()
                if buffer.startswith(']'): 
                    buffer = buffer[1:].lstrip()
                    break
                if not buffer: break

                try:
                    obj, idx = json.JSONDecoder().raw_decode(buffer)
                    buffer = buffer[idx:].lstrip()
                    
                    text_delta = ""
                    chunk_metadata = {}
                    finish_reason = None
                    
                    if "candidates" in obj:
                        candidate = obj["candidates"][0]
                        if "finishReason" in candidate:
                            finish_reason = candidate["finishReason"].lower()
                        
                        if "content" in candidate and "parts" in candidate["content"]:
                            parts = candidate["content"]["parts"]
                            extracted_attachments = []
                            for part in parts:
                                t_delta, att = self._parse_part_to_text_and_attachment(part)
                                if t_delta: text_delta += t_delta
                                if att: extracted_attachments.append(att)
                            
                            if extracted_attachments:
                                chunk_metadata["stream_attachments"] = extracted_attachments

                    gateway_usage = None
                    if "usageMetadata" in obj:
                        u = obj["usageMetadata"]
                        gateway_usage = GatewayUsage(prompt_tokens=u.get("promptTokenCount", 0), completion_tokens=u.get("candidatesTokenCount", 0), total_tokens=u.get("totalTokenCount", 0))

                    yield GatewayStreamChunk(
                        id=stream_id,
                        model=request_model,
                        choices=[GatewayStreamChoice(index=0, delta=GatewayStreamDelta(content=text_delta if text_delta else None, role="assistant"), finish_reason=finish_reason)],
                        provider=self.provider_name,
                        usage=gateway_usage,
                        metadata=chunk_metadata
                    )
                except json.JSONDecodeError:
                    break

    def adapt_chat_response(self, response_data: Dict[str, Any], request_model: str) -> GatewayResponse:
        choices = []
        for idx, candidate in enumerate(response_data.get("candidates", [])):
            parts = candidate.get("content", {}).get("parts", [])
            content_parts = []
            for part in parts:
                t_delta, att = self._parse_part_to_text_and_attachment(part)
                if t_delta:
                    content_parts.append(MessageContentPart(type="text", text=t_delta))
                if att:
                    attachment = GatewayAttachment(id=att["id"], mime_type=att["mime_type"], base64_data=att["base64_data"])
                    content_parts.append(MessageContentPart(type="image", image=ImageContent(attachment=attachment)))

            choices.append(GatewayChoice(index=idx, message=GatewayMessage(role="assistant", content=content_parts), finish_reason=candidate.get("finishReason", "stop").lower()))
        u = response_data.get("usageMetadata", {})
        return GatewayResponse(id=f"chatcmpl-{uuid.uuid4()}", model=request_model, choices=choices, usage=GatewayUsage(prompt_tokens=u.get("promptTokenCount", 0), completion_tokens=u.get("candidatesTokenCount", 0), total_tokens=u.get("totalTokenCount", 0)), provider=self.provider_name)

# =========================================================================
# 3. GIAO DIỆN ĐỒ HOẠ (HỖ TRỢ BIẾN STREAM SWITCH)
# =========================================================================

class GeminiGatewayGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Google Gemini Gateway Client - Smooth Typewriter")
        self.root.geometry("1150x750")
        self.root.configure(bg="#f4f6f9")
        self.rendered_image = None

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
        main_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)

        left_frame = tk.LabelFrame(main_frame, text=" 📝 Văn bản & Mã nguồn (Hiển thị mượt) ", font=("Helvetica", 10, "bold"), bg="#ffffff")
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        self.text_area = scrolledtext.ScrolledText(left_frame, wrap=tk.WORD, font=("Consolas", 10))
        self.text_area.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        right_frame = tk.LabelFrame(main_frame, text=" 📊 Biêu đồ / Hình ảnh (GatewayAttachment) ", font=("Helvetica", 10, "bold"), bg="#ffffff")
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self.image_label = tk.Label(right_frame, text="[Chưa có hình ảnh]", font=("Helvetica", 11, "italic"), bg="#eaedf2", fg="#6c757d")
        self.image_label.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

    def append_text_instantly(self, text: str):
        """Dùng cho STREAM: Chèn ngay lập tức chuỗi ký tự vừa nhận từ mạng vào UI."""
        self.text_area.insert(tk.END, text)
        self.text_area.see(tk.END)

    def append_text_smoothly(self, text: str, index: int = 0, delay_ms: int = 10):
        """
        Dùng cho NON-STREAM: Đệ quy an toàn bằng hệ thống sự kiện của Tkinter (.after)
        để in từng ký tự một với độ trễ tùy chỉnh (mặc định 10ms), tạo hiệu ứng typewriter siêu mượt.
        """
        if index < len(text):
            char = text[index]
            self.text_area.insert(tk.END, char)
            self.text_area.see(tk.END)
            # Lên lịch in ký tự tiếp theo sau `delay_ms` mili-giây
            self.root.after(delay_ms, lambda: self.append_text_smoothly(text, index + 1, delay_ms))

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
        self.image_label.config(image="", text="[Đang xử lý kết nối...]")
        
        use_stream = self.is_stream_var.get()
        threading.Thread(target=lambda: asyncio.run(self.execute_api_flow(prompt, use_stream)), daemon=True).start()

    async def execute_api_flow(self, prompt: str, use_stream: bool):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            self.root.after(0, lambda: messagebox.showerror("Lỗi", "Vui lòng cấu hình GEMINI_API_KEY"))
            self.root.after(0, lambda: self.submit_btn.config(state=tk.NORMAL, text="Gửi Yêu Cầu AI"))
            return

        model = "gemini-2.5-flash"
        adapter = DualGeminiAdapter()
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "tools": [{"codeExecution": {}}],
            "systemInstruction": {"parts": [{"text": "Bạn là trợ lý phân tích dữ liệu. Luôn dùng bảng Markdown."}]}
        }

        method = "streamGenerateContent" if use_stream else "generateContent"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:{method}?key={api_key}"

        async with httpx.AsyncClient() as client:
            try:
                if use_stream:
                    # KỊCH BẢN 1: CHẠY STREAMING
                    async with client.stream("POST", url, json=payload, timeout=60.0) as response:
                        response.raise_for_status()
                        async for chunk in adapter.adapt_chat_stream(response, model):
                            delta_text = chunk.choices[0].delta.content
                            if delta_text:
                                # Đổ text trực tiếp ra màn hình (mượt tự nhiên theo tốc độ mạng)
                                self.root.after(0, lambda dt=delta_text: self.append_text_instantly(dt))
                            
                            if "stream_attachments" in chunk.metadata:
                                for att in chunk.metadata["stream_attachments"]:
                                    if att["mime_type"].startswith("image/"):
                                        self.root.after(0, lambda b6=att["base64_data"]: self.render_base64_image(b6))
                else:
                    # KỊCH BẢN 2: CHẠY NON-STREAM (NHẬN CẢ CỤM TEXT LỚN)
                    response = await client.post(url, json=payload, timeout=60.0)
                    response.raise_for_status()
                    output = adapter.adapt_chat_response(response.json(), model)
                    
                    full_text = ""
                    base64_img = None
                    
                    # Thu thập toàn bộ dữ liệu trước khi chạy hiệu ứng chữ mượt
                    for part in output.choices[0].message.content:
                        if part.type == "text" and part.text:
                            full_text += part.text
                        elif part.type == "image" and part.image:
                            base64_img = part.image.attachment.base64_data
                    
                    # Gọi hàm in chữ mượt độc lập dạng máy đánh chữ cho toàn bộ khối văn bản
                    if full_text:
                        self.root.after(0, lambda t=full_text: self.append_text_smoothly(t, delay_ms=10))
                    if base64_img:
                        self.root.after(0, lambda b=base64_img: self.render_base64_image(b))
                            
            except Exception as e:
                self.root.after(0, lambda err=str(e): self.append_text_instantly(f"\n❌ Lỗi hệ thống: {err}"))
            finally:
                self.root.after(0, lambda: self.submit_btn.config(state=tk.NORMAL, text="Gửi Yêu Cầu AI"))

if __name__ == "__main__":
    root_window = tk.Tk()
    app = GeminiGatewayGUI(root_window)
    root_window.mainloop()