import base64
import json
import time
from typing import Dict, Any, AsyncGenerator
import uuid
import httpx
from pathlib import Path
from .....schemas import (
    GatewayResponse, GatewayChoice, GatewayMessage, GatewayUsage,
    GatewayStreamChunk, GatewayStreamChoice, GatewayStreamDelta
)
from ...base.adapter import BaseAdapter
from ....exceptions import ResponseValidationError
from .attachment import (
    MediaContentHandler, FlatFileHandler, 
    UrlContextHandler, OpenAiVisionFallbackHandler, 
    BaseAttachmentHandler,
    FileHelper
)

class GeminiAdapter(BaseAdapter):

    def __init__(self):
        # Đăng ký danh sách các Sub-Adapter phụ trách từng loại phần tử khác nhau
        media_handler = MediaContentHandler()
        
        self.handlers: Dict[str, BaseAttachmentHandler] = {
            "image": media_handler,
            "audio": media_handler,
            "video": media_handler,
            "file": FlatFileHandler(),
            "url": UrlContextHandler(),
            "image_url": OpenAiVisionFallbackHandler()
        }

    def adapt_chat_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Hàm chính điều phối: Gọi các Sub-Adapter tương ứng để xử lý tệp đính kèm.
        """
        gemini_contents = []
        system_instruction_text = ""

        # 1. Phân tách System Prompt và Message Lịch sử
        for msg in request.get("messages", []):
            role = msg.get("role")
            content = msg.get("content", "")
            
            if role == "system":
                if isinstance(content, str):
                    system_instruction_text += content + "\n"
                elif isinstance(content, list):
                    for part in content:
                        if part.get("type") == "text" or "text" in part:
                            system_instruction_text += part.get("text", "") + "\n"
                continue

            gemini_role = "user" if role != "assistant" else "model"
            gemini_parts = []

            # Kịch bản 1: Văn bản phẳng thuần túy
            if isinstance(content, str):
                gemini_parts.append({"text": content})

            # Kịch bản 2: Danh sách Multimodal Parts chuẩn mới
            elif isinstance(content, list):
                for part in content:
                    raw_type = part.get("type")
                    part_type = raw_type.value if hasattr(raw_type, "value") else str(raw_type)
                    
                    # Xử lý chữ thuần tại chỗ
                    if part_type == "text":
                        gemini_parts.append({"text": part.get("text", "")})
                        continue
                        
                    # ĐIỀU PHỐI THÔNG MINH: Tìm kiếm Sub-Adapter phụ trách loại tệp này
                    handler = self.handlers.get(part_type)
                    if handler:
                        gemini_part_result = handler.handle(part, part_type)
                        if gemini_part_result:
                            gemini_parts.append(gemini_part_result)
                    else:
                        # Log cảnh báo nếu hệ thống nhận được một loại tệp lạ chưa đăng ký sub-adapter
                        print(f"⚠️ [WARNING] Không tìm thấy Sub-Adapter xử lý cho type: {part_type}")

            if gemini_parts:
                gemini_contents.append({
                    "role": gemini_role,
                    "parts": gemini_parts
                })

        # --- Đóng gói cấu trúc Gemini Body ---
        gemini_body = {"contents": gemini_contents}

        if "model" in request:
            gemini_body["model"] = request["model"]

        if system_instruction_text.strip():
            gemini_body["systemInstruction"] = {
                "parts": [{"text": system_instruction_text.strip()}]
            }

        # Cấu hình tham số thế hệ (Generation Config)
        gen_config = {}
        if "temperature" in request:
            gen_config["temperature"] = request["temperature"]
        if "max_tokens" in request:
            gen_config["maxOutputTokens"] = request["max_tokens"]
            
        response_format = request.get("response_format", {})
        if response_format.get("type") == "json_object":
            gen_config["responseMimeType"] = "application/json"

        if gen_config:
            gemini_body["generationConfig"] = gen_config
        
        return gemini_body

    async def adapt_chat_response(self, response: httpx.Response) -> GatewayResponse:
        """Chuyển đổi response JSON từ Gemini về GatewayResponse kèm Usage (nếu có)."""
        try:
            response_data = response.json()
            candidate = response_data["candidates"][0]
            content = candidate["content"]["parts"][0]["text"]
            
            # Chuẩn hóa finish_reason từ Gemini sang định dạng OpenAI mong muốn
            gemini_finish_reason = candidate.get("finishReason", "STOP").upper()
            reason_mapping = {
                "STOP": "stop",
                "MAX_TOKENS": "length",
                "SAFETY": "content_filter",
                "RECITATION": "content_filter",
            }
            finish_reason = reason_mapping.get(gemini_finish_reason, "stop")
            
            # Khôi phục Token Usage từ Gemini (Bản 2.0/2.5 đã trả về usageMetadata)
            usage_data = response_data.get("usageMetadata", {})
            usage = GatewayUsage(
                prompt_tokens=usage_data.get("promptTokenCount", 0),
                completion_tokens=usage_data.get("candidatesTokenCount", 0),
                total_tokens=usage_data.get("totalTokenCount", 0)
            )

            return GatewayResponse(
                id=f"chatcmpl-{uuid.uuid4()}",
                model=response_data.get("modelVersion", "gemini-model"),
                choices=[GatewayChoice(
                    index=0,
                    message=GatewayMessage(role="assistant", content=content),
                    finish_reason=finish_reason
                )],
                usage=usage,
                provider="gemini",
                created=int(time.time()),
                raw_response=response_data
            )
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise ResponseValidationError(
                f"Hỏng cấu trúc response từ Gemini: {str(e)}", 
                provider_name="gemini"
            ) from e
        
    async def adapt_chat_stream(self, response: httpx.Response) -> AsyncGenerator[GatewayStreamChunk, None]:
        """
        Chuẩn hóa stream của Gemini.
        Vì Gemini trả về một mảng JSON cắt nhỏ [{}, {}], ta đọc theo dòng và dọn dẹp ký tự thừa.
        """
        stream_id = f"chatcmpl-{uuid.uuid4()}"
        buffer = ""
        
        async for chunk in response.aiter_text():
            buffer += chunk
            # Xử lý bóc tách từng JSON Object xuất hiện trong Buffer
            while True:
                buffer = buffer.lstrip()
                # Xóa bỏ các ký tự phân tách mảng của JSON stream: '[', ',', ']'
                if buffer.startswith('['):
                    buffer = buffer[1:].lstrip()
                if buffer.startswith(','):
                    buffer = buffer[1:].lstrip()
                if buffer.startswith(']'):
                    buffer = buffer[1:].lstrip()
                    break
                
                if not buffer:
                    break

                # Tìm ranh giới của một JSON object hợp lệ
                try:
                    obj, idx = json.JSONDecoder().raw_decode(buffer)
                    buffer = buffer[idx:].lstrip()
                    
                    text_delta = ""
                    finish_reason = None
                    
                    # Trích xuất dữ liệu chữ từ Object của Gemini chunk
                    if "candidates" in obj:
                        candidate = obj["candidates"][0]
                        
                        # Lấy lý do kết thúc nếu có
                        if "finishReason" in candidate:
                            gemini_finish_reason = candidate["finishReason"].upper()
                            reason_mapping = {
                                "STOP": "stop",
                                "MAX_TOKENS": "length",
                                "SAFETY": "content_filter",
                                "RECITATION": "content_filter",
                            }
                            finish_reason = reason_mapping.get(gemini_finish_reason, "stop")
                        
                        if "content" in candidate and "parts" in candidate["content"]:
                            text_delta = candidate["content"]["parts"][0].get("text", "")
                    
                    # Trích xuất Usage Metadata (Gemini thường trả về ở chunk cuối cùng)
                    gateway_usage = None
                    if "usageMetadata" in obj:
                        usage_data = obj["usageMetadata"]
                        gateway_usage = GatewayUsage(
                            prompt_tokens=usage_data.get("promptTokenCount", 0),
                            completion_tokens=usage_data.get("candidatesTokenCount", 0),
                            total_tokens=usage_data.get("totalTokenCount", 0)
                        )
                    
                    # Trả về GatewayStreamChunk đầy đủ thuộc tính
                    yield GatewayStreamChunk(
                        id=stream_id,
                        model=obj.get("modelVersion", "gemini-model"),
                        choices=[GatewayStreamChoice(
                            index=0,
                            delta=GatewayStreamDelta(content=text_delta, role="assistant"),
                            finish_reason=finish_reason
                        )],
                        provider="gemini",
                        created=int(time.time()),
                        usage=gateway_usage
                    )
                    
                except json.JSONDecodeError:
                    # Chưa nhận đủ dữ liệu cho một Object hoàn chỉnh, đợi chunk tiếp theo
                    break

    def adapt_embeddings_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Sửa lỗi: Thêm bọc models/ vào trước tên model cho đúng quy định Gemini."""
        input_text = request.get("input")
        model_name = request.get("model", "embedding-001")
        # Đảm bảo có prefix models/
        full_model_path = model_name if model_name.startswith("models/") else f"models/{model_name}"

        if isinstance(input_text, str):
            return {"model": full_model_path, "content": {"parts": [{"text": input_text}]}}
        elif isinstance(input_text, list):
            return {
                "requests": [
                    {"model": full_model_path, "content": {"parts": [{"text": text}]}}
                    for text in input_text
                ]
            }
        return {}