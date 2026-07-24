from typing import List, Dict, Any
from pathlib import Path
import base64
import os
import re
import json

from ...file_extension import FileHelper
from ......schemas import GatewayToolDefinition
from .acttachment import (
    MediaContentHandler,
    BaseAttachmentHandler,
    FlatFileHandler,
    UrlContextHandler,
    OpenAiVisionFallbackHandler
)
from ......schemas import GatewayToolResult
import structlog
logger = structlog.get_logger(__name__)

MAX_TEXT_LENGTH = 100_000       # Giới hạn số ký tự text thuần cho mỗi tin nhắn (~100k ký tự)
MAX_LOCAL_FILE_SIZE_MB = 15     # Giới hạn file local tối đa 15MB để tránh phình Payload Base64


class RequestChats:
    def __init__(self):
        media_handler = MediaContentHandler()
        
        self.handlers: Dict[str, BaseAttachmentHandler] = {
            "image": media_handler,
            "audio": media_handler,
            "video": media_handler,
            "file": FlatFileHandler(),
            "url": UrlContextHandler(),
            "image_url": OpenAiVisionFallbackHandler()
        }

    def _process_flat_text_content(self, text_content: str) -> List[Dict[str, Any]]:
        """
        Hàm xử lý thông minh cho văn bản phẳng thuần túy.
        Tự động bóc tách Base64 thô, File Local thô, hoặc URL nằm trong chuỗi.
        """
        parts = []
        stripped_text = text_content.strip()

        # 1. Kiểm tra nếu bản thân text_content là một chuỗi Data URL Base64 thô
        if stripped_text.startswith("data:") and ";base64," in stripped_text:
            try:
                header, base64_data = stripped_text.split(";base64/", 1)
                mime_type = header.replace("data:", "")
                return [{"inlineData": {"mimeType": mime_type, "data": base64_data}}]
            except Exception as e:
                logger.warning("Lỗi phân tách chuỗi Base64 thô", error=str(e))

        # 2. Kiểm tra nếu là một URL Web công khai thô
        if re.match(r'^https?://[^\s]+$', stripped_text):
            return [{"text": f"[Link]: {stripped_text}"}]

        # 3. Nếu là Text bình thường -> Kiểm tra và cắt độ dài chống lỗi Payload dữ liệu
        if len(text_content) > MAX_TEXT_LENGTH:
            logger.warning(f"Text content quá dài ({len(text_content)} ký tự). Hệ thống tự động cắt ngắn về {MAX_TEXT_LENGTH} ký tự.")
            text_content = text_content[:MAX_TEXT_LENGTH] + "\n...[Nội dung bị cắt bớt do quá dài]..."

        parts.append({"text": text_content})
        return parts

    def _format_function_response(self, name: str, content: Any) -> Dict[str, Any]:
        """
        Chuẩn hóa kết quả trả về của Tool/Function thành Gemini functionResponse format.
        Gemini bắt buộc khối 'response' phải là một JSON Object (Dict).
        """
        response_obj = {}
        if isinstance(content, dict):
            response_obj = content
        elif isinstance(content, str):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    response_obj = parsed
                else:
                    response_obj = {"response": parsed}
            except Exception:
                response_obj = {"response": content}
        else:
            response_obj = {"response": content}

        return {
            "functionResponse": {
                "name": name,
                "response": response_obj
            }
        }

    def adapt_chat(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Hàm chính điều phối: Chuyển đổi chuẩn Request OpenAI/Gateway sang REST Payload của Gemini.
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

            gemini_parts = []

            # --- XỬ LÝ THEO TỪNG PHÂN HỆ VAI TRÒ (ROLE) ---
            
            # KỊCH BẢN A: TRỢ LÝ AI (ASSISTANT / MODEL)
            if role == "assistant":
                gemini_role = "model"
                
                # 1. Trích xuất Tool Calls từ hệ thống OpenAI chuyển dịch sang functionCall của Gemini
                tool_calls = msg.get("tool_calls") or msg.get("function_call")
                if tool_calls:
                    if isinstance(tool_calls, list):
                        for tc in tool_calls:
                            func = tc.get("function", {})
                            args = func.get("arguments", {})
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args)
                                except Exception:
                                    args = {"raw_arguments": args}
                            gemini_parts.append({
                                "functionCall": {
                                    "name": func.get("name"),
                                    "args": args
                                }
                            })
                    elif isinstance(tool_calls, dict):
                        args = tool_calls.get("arguments", {})
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except Exception:
                                args = {"raw_arguments": args}
                        gemini_parts.append({
                            "functionCall": {
                                "name": tool_calls.get("name"),
                                "args": args
                            }
                        })

                # 2. Xử lý phần nội dung văn bản kèm theo của Assistant
                if content:
                    if isinstance(content, str):
                        gemini_parts.extend(self._process_flat_text_content(content))
                    elif isinstance(content, list):
                        for part in content:
                            if part.get("type") == "text" or "text" in part:
                                gemini_parts.append({"text": part.get("text", "")})

            # KỊCH BẢN B: KẾT QUẢ TRẢ VỀ TỪ CÔNG CỤ (TOOL / FUNCTION RESPONSE / TOOL_RESULT)
            elif role in ["tool", "tool_result", "function"]:
                # Quy tắc REST của Gemini: functionResponse bắt buộc phải nằm dưới turn của role 'user'
                gemini_role = "user"
                
                # Bóc tách tên tool (Ưu tiên lấy tên từ GatewayToolResult nếu content là instance)
                tool_name = msg.get("name") or msg.get("tool_name")
                if isinstance(content, GatewayToolResult):
                    tool_name = content.name or tool_name
                elif isinstance(content, dict) and "name" in content:
                    tool_name = content.get("name") or tool_name

                if not tool_name:
                    tool_name = "unnamed_tool"

                gemini_parts.append(self._format_function_response(tool_name, content))

            # KỊCH BẢN C: NGƯỜI DÙNG (USER) HOẶC CÁC ROLE KHÁC
            else:
                gemini_role = "user"

                # 1. Xử lý trường hợp đặc biệt: user message chứa tool_result / function_response
                if "tool_result" in msg or "function_response" in msg:
                    tool_data = msg.get("tool_result") or msg.get("function_response")
                    tool_name = tool_data.get("name") or tool_data.get("tool_name") or "unnamed_tool"
                    tool_content = tool_data.get("content") or tool_data.get("response") or tool_data
                    gemini_parts.append(self._format_function_response(tool_name, tool_content))

                # 2. Xử lý content chính của User
                if isinstance(content, str):
                    processed_parts = self._process_flat_text_content(content)
                    gemini_parts.extend(processed_parts)
                elif isinstance(content, list):
                    for part in content:
                        raw_type = part.get("type")
                        part_type = raw_type.value if hasattr(raw_type, "value") else str(raw_type)
                        
                        # Bóc tách nếu part chứa tool_result riêng lẻ
                        if part_type in ["tool_result", "function_response"]:
                            tool_name = part.get("name") or part.get("tool_name") or "unnamed_tool"
                            tool_content = part.get("content") or part.get("output") or ""
                            gemini_parts.append(self._format_function_response(tool_name, tool_content))
                            continue

                        if part_type == "text":
                            text_val = part.get("text", "")
                            if len(text_val) > MAX_TEXT_LENGTH:
                                logger.warning(f"Part text quá dài. Tiến hành cắt ngắn về {MAX_TEXT_LENGTH} ký tự.")
                                text_val = text_val[:MAX_TEXT_LENGTH] + "\n...[Cắt bớt]..."
                            gemini_parts.append({"text": text_val})
                            continue
                            
                        handler = self.handlers.get(part_type)
                        if handler:
                            try:
                                gemini_part_result = handler.handle(part, part_type)
                                if gemini_part_result:
                                    gemini_parts.append(gemini_part_result)
                            except Exception as e:
                                logger.warning(f"Lỗi khi Sub-Adapter {handler.__class__.__name__} xử lý", error=str(e))
                        else:
                            logger.warning(f"Không tìm thấy Sub-Adapter xử lý cho type: {part_type}")

            if gemini_parts:
                gemini_contents.append({
                    "role": gemini_role,
                    "parts": gemini_parts
                })

        # --- Đóng gói cấu trúc Gemini Body ---
        gemini_body = {"contents": gemini_contents}

        # Xử lý Khai báo Tools (Function Declarations & Native Code Execution)
        tools_input = request.get("tools")
        if tools_input and isinstance(tools_input, list):
            function_declarations = []
            gemini_tools = []
            
            for tool_data in tools_input:
                if isinstance(tool_data, GatewayToolDefinition):
                    tool = tool_data
                elif isinstance(tool_data, dict):
                    tool = GatewayToolDefinition(**tool_data)
                else:
                    continue
                
                # Bổ sung ép kiểu Enum / String an toàn
                tool_type_str = tool.tool_type.value if hasattr(tool.tool_type, "value") else str(tool.tool_type)

                if tool_type_str == "NATIVE":
                    if {"code_execution": {}} not in gemini_tools:
                        gemini_tools.append({"code_execution": {}})
                else:
                    decl = {
                        "name": tool.name,
                        "description": tool.description
                    }
                    if tool.parameters:
                        param_schema = tool.parameters.copy() if isinstance(tool.parameters, dict) else tool.parameters
                        param_schema.pop("$schema", None)

                        # --- ĐIỂM SỬA QUAN TRỌNG CHO GEMINI ---
                        # Đảm bảo type luôn là OBJECT (In hoa) để Gemini tiếp nhận
                        if "type" in param_schema and isinstance(param_schema["type"], str):
                            param_schema["type"] = param_schema["type"].upper()
                        else:
                            param_schema["type"] = "OBJECT"

                        # Đảm bảo properties luôn tồn tại (kể cả khi không có param nào)
                        if "properties" not in param_schema:
                            param_schema["properties"] = {}

                        decl["parameters"] = param_schema
                        
                    function_declarations.append(decl)
            
            if function_declarations:
                gemini_tools.append({"function_declarations": function_declarations})
                
            if gemini_tools:
                gemini_body["tools"] = gemini_tools

        # Giới hạn độ dài cho System Instruction tổng
        if system_instruction_text.strip():
            sys_text = system_instruction_text.strip()
            if len(sys_text) > MAX_TEXT_LENGTH:
                logger.warning(f"System instruction quá dài ({len(sys_text)} ký tự). Hệ thống tự động cắt ngắn về {MAX_TEXT_LENGTH} ký tự.")
                sys_text = sys_text[:MAX_TEXT_LENGTH]
            gemini_body["systemInstruction"] = {
                "parts": [{"text": sys_text}]
            }

        # Cấu hình tham số sinh (Generation Config)
        gen_config = {}
        config_data = request.get("config", {})

        if "temperature" in config_data and config_data["temperature"] is not None:
            gen_config["temperature"] = config_data["temperature"]
            
        if "top_p" in config_data and config_data["top_p"] is not None:
            gen_config["topP"] = config_data["top_p"]
            
        if "max_tokens" in config_data and config_data["max_tokens"] is not None:
            gen_config["maxOutputTokens"] = config_data["max_tokens"]
            
        if "presence_penalty" in config_data and config_data["presence_penalty"] is not None:
            gen_config["presencePenalty"] = config_data["presence_penalty"]
            
        if "frequency_penalty" in config_data and config_data["frequency_penalty"] is not None:
            gen_config["frequencyPenalty"] = config_data["frequency_penalty"]

        if "response_format" in config_data and config_data["response_format"] is not None:
            gen_config["responseMimeType"] = config_data["response_format"]

        if gen_config:
            gemini_body["generationConfig"] = gen_config
            
        return gemini_body