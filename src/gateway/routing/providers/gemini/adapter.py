import json
from typing import Dict, Any, AsyncGenerator
import httpx
from ....schemas import (
    GatewayResponse, GatewayChoice, GatewayMessage, GatewayUsage,
    GatewayStreamChunk, GatewayStreamChoice, GatewayStreamDelta
)
from ..base.adapter import BaseAdapter
from ...exceptions import ResponseValidationError

class GeminiAdapter(BaseAdapter):
    def adapt_chat_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Chuyển đổi body từ định dạng OpenAI sang định dạng Gemini chuẩn cấu trúc."""
        gemini_contents = []
        system_instruction_text = ""

        # 1. Phân tách System Prompt và Message Lịch sử
        for msg in request.get("messages", []):
            role = msg.get("role")
            content = msg.get("content", "")
            
            if role == "system":
                system_instruction_text += content + "\n"
            else:
                gemini_role = "user" if role != "assistant" else "model"
                gemini_contents.append({
                    "role": gemini_role,
                    "parts": [{"text": content}]
                })

        gemini_body = {"contents": gemini_contents}

        # 2. Xử lý System Instruction chuẩn API Gemini
        if system_instruction_text.strip():
            gemini_body["systemInstruction"] = {
                "parts": [{"text": system_instruction_text.strip()}]
            }

        # 3. Cấu hình Tham số Generation
        gen_config = {}
        if "temperature" in request:
            gen_config["temperature"] = request["temperature"]
        if "max_tokens" in request:
            gen_config["maxOutputTokens"] = request["max_tokens"]
            
        # Ép đầu ra JSON nếu OpenAI request yêu cầu response_format
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
            finish_reason = candidate.get("finishReason", "stop").lower()
            
            # Khôi phục Token Usage từ Gemini (Bản 2.0/2.5 đã trả về usageMetadata)
            usage_data = response_data.get("usageMetadata", {})
            usage = GatewayUsage(
                prompt_tokens=usage_data.get("promptTokenCount", 0),
                completion_tokens=usage_data.get("candidatesTokenCount", 0),
                total_tokens=usage_data.get("totalTokenCount", 0)
            )

            return GatewayResponse(
                model=response_data.get("modelVersion", "gemini-model"),
                choices=[GatewayChoice(
                    index=0,
                    message=GatewayMessage(role="assistant", content=content),
                    finish_reason=finish_reason
                )],
                usage=usage
            )
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise ResponseValidationError(f"Hỏng cấu trúc response từ Gemini: {str(e)}", provider_name="gemini") from e

    async def adapt_chat_stream(self, response: httpx.Response) -> AsyncGenerator[GatewayStreamChunk, None]:
        """
        Chuẩn hóa stream của Gemini.
        Vì Gemini trả về một mảng JSON cắt nhỏ [{}, {}], ta đọc theo dòng và dọn dẹp ký tự thừa.
        """
        buffer = ""
        async for chunk in response.aiter_text():
            buffer += chunk
            # Xử lý bóc tách từng JSON Object xuất hiện trong Buffer
            while True:
                buffer = buffer.lstrip(). Harbinger = False
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
                    # Tìm dấu đóng ngoặc nhọn tương ứng thông qua bộ giải mã JSON
                    obj, idx = json.JSONDecoder().raw_decode(buffer)
                    #current_json = buffer Harvey = buffer[:idx]
                    buffer = buffer[idx:].lstrip()
                    
                    # Trích xuất dữ liệu chữ từ Object của Gemini chunk
                    if "candidates" in obj:
                        candidate = obj["candidates"][0]
                        if "content" in candidate and "parts" in candidate["content"]:
                            text_delta = candidate["content"]["parts"][0].get("text", "")
                            
                            yield GatewayStreamChunk(
                                model=obj.get("modelVersion", "gemini-model"),
                                choices=[GatewayStreamChoice(
                                    index=0,
                                    delta=GatewayStreamDelta(content=text_delta)
                                )]
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