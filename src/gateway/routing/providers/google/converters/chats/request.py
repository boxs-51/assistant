from typing import List, Dict, Any
from pathlib import Path
import base64
import os
import re

from ...file_extension import FileHelper
from ......schemas import GatewayToolDefinition
from .acttachment import (
    MediaContentHandler,
    BaseAttachmentHandler,
    FlatFileHandler,
    UrlContextHandler,
    OpenAiVisionFallbackHandler
)
import structlog
logger = structlog.get_logger(__name__)
MAX_TEXT_LENGTH = 100_000       # Giới hạn số ký tự text thuần cho mỗi tin nhắn (~100k ký tự)
MAX_LOCAL_FILE_SIZE_MB = 15     # Giới hạn file local tối đa 15MB để tránh phình Payload Base64

class RequestChats():
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
                header, base64_data = stripped_text.split(";base64,", 1)
                mime_type = header.replace("data:", "")
                return [{"inlineData": {"mimeType": mime_type, "data": base64_data}}]
            except Exception as e:
                logger.warning(f"Lỗi phân tách chuỗi Base64 thô",error=str(e))
                # Tiếp tục luồng, coi như text bình thường

        # 2. Kiểm tra nếu là một đường dẫn Local File thô hợp lệ
        # Sử dụng try-except để phòng trường hợp chuỗi chứa ký tự đặc biệt gây lỗi Path
        try:
            potential_path = Path(stripped_text)
            if potential_path.is_file():
                # Kiểm tra dung lượng file
                file_size_mb = os.path.getsize(potential_path) / (1024 * 1024)
                if file_size_mb > MAX_LOCAL_FILE_SIZE_MB:
                    logger.warning(f"File local '{stripped_text}' vượt quá giới hạn ({file_size_mb:.2f}MB > {MAX_LOCAL_FILE_SIZE_MB}MB). Bỏ qua chuyển đổi Base64.")
                    return [{"text": f"[Local File Path - Too Large]: {stripped_text}"}]
                
                # Tiến hành chuyển sang Base64
                mime_type = FileHelper.detect_mime_type(potential_path) if 'FileHelper' in globals() else "application/octet-stream"
                with open(potential_path, "rb") as f:
                    base64_data = base64.b64encode(f.read()).decode('utf-8')
                
                return [{"inlineData": {"mimeType": mime_type, "data": base64_data}}]
        except Exception as e:
            # Không phải path hợp lệ hoặc lỗi đọc file -> Tiếp tục luồng xuống dưới
            logger.warning(f"Path không hợp lệ hoặc lỗi đọc file",error =str(e),file_id=potential_path)
            pass

        # 3. Kiểm tra nếu là một URL Web công khai thô
        if re.match(r'^https?://[^\s]+$', stripped_text):
            return [{"text": f"[Link tham khảo]: {stripped_text}"}]

        # 4. Nếu là Text bình thường -> Kiểm tra và cắt độ dài chống lỗi Payload dữ liệu
        if len(text_content) > MAX_TEXT_LENGTH:
            logger.warning(f"Text content quá dài ({len(text_content)} ký tự). Hệ thống tự động cắt ngắn về {MAX_TEXT_LENGTH} ký tự.")
            text_content = text_content[:MAX_TEXT_LENGTH] + "\n...[Nội dung bị cắt bớt do quá dài]..."

        parts.append({"text": text_content})
        return parts
    
    def adapt_chat(self, request: Dict[str, Any]) -> Dict[str, Any]:
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

            # --- KỊCH BẢN 1: VĂN BẢN PHẲNG THUẦN TÚY (ĐÃ ĐƯỢC NÂNG CẤP) ---
            if isinstance(content, str):
                processed_parts = self._process_flat_text_content(content)
                gemini_parts.extend(processed_parts)

            # --- Kịch bản 2: Danh sách Multimodal Parts chuẩn mới ---
            elif isinstance(content, list):
                for part in content:
                    raw_type = part.get("type")
                    part_type = raw_type.value if hasattr(raw_type, "value") else str(raw_type)
                    
                    # Xử lý chữ thuần tại chỗ
                    if part_type == "text":
                        text_val = part.get("text", "")
                        # Kiểm tra độ dài cho từng part text nhỏ
                        if len(text_val) > MAX_TEXT_LENGTH:
                            logger.warning(f"Part text quá dài. Tiến hành cắt ngắn về {MAX_TEXT_LENGTH} ký tự.")
                            text_val = text_val[:MAX_TEXT_LENGTH] + "\n...[Cắt bớt]..."
                        
                        gemini_parts.append({"text": text_val})
                        continue
                        
                    # ĐIỀU PHỐI THÔNG MINH: Tìm kiếm Sub-Adapter phụ trách loại tệp này
                    handler = self.handlers.get(part_type)
                    if handler:
                        try:
                            gemini_part_result = handler.handle(part, part_type)
                            if gemini_part_result:
                                gemini_parts.append(gemini_part_result)
                        except Exception as e:
                            logger.warning(f"Lỗi khi Sub-Adapter {handler.__class__.__name__} xử lý",error= str(e))
                    else:
                        logger(f"Không tìm thấy Sub-Adapter xử lý cho type: {part_type}")

            if gemini_parts:
                gemini_contents.append({
                    "role": gemini_role,
                    "parts": gemini_parts
                })

        # --- Đóng gói cấu trúc Gemini Body ---
        gemini_body = {"contents": gemini_contents}

        tools_input = request.get("tools")
        if tools_input and isinstance(tools_input, list):
            function_declarations = []
            gemini_tools = []
            
            for tool_data in tools_input:
                # Ép kiểu nghiêm ngặt từ dữ liệu nhận được về DTO Pydantic
                if isinstance(tool_data, GatewayToolDefinition):
                    tool = tool_data
                else:
                    tool = GatewayToolDefinition(**tool_data)
                
                # CHÍNH SÁCH PHÂN LOẠI DỰA TRÊN DTO:
                # Nếu là tool hệ thống hoặc có tên đặc biệt -> Map thành công cụ chạy code của Gemini
                if tool.tool_type.value == "NATIVE":
                    # Đảm bảo không trùng lặp nếu client gửi nhiều lần
                    if {"code_execution": {}} not in gemini_tools:
                        gemini_tools.append({"code_execution": {}})
                else:
                    # Nếu là Custom Tool (MCP, API riêng...) -> Gom vào function_declarations
                    decl = {
                        "name": tool.name,
                        "description": tool.description
                    }
                    if tool.parameters:
                        param_schema = tool.parameters.copy()
                        param_schema.pop("$schema", None)
                        decl["parameters"] = param_schema
                        
                    function_declarations.append(decl)
            
            # Nếu có custom functions, đóng gói lại và nạp vào danh sách tools của Gemini
            if function_declarations:
                gemini_tools.append({"function_declarations": function_declarations})
                
            if gemini_tools:
                gemini_body["tools"] = gemini_tools

        if "model" in request:
            gemini_body["model"] = request["model"]

        # Giới hạn độ dài cho cả System Instruction tổng
        if system_instruction_text.strip():
            sys_text = system_instruction_text.strip()
            if len(sys_text) > MAX_TEXT_LENGTH:
                logger.warning(f"System instruction quá dài ({len(sys_text)} ký tự). Hệ thống tự động cắt ngắn về {MAX_TEXT_LENGTH} ký tự.")
                sys_text = sys_text[:MAX_TEXT_LENGTH]
            gemini_body["systemInstruction"] = {
                "parts": [{"text": sys_text}]
            }

        # Cấu hình tham số thế hệ (Generation Config)
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