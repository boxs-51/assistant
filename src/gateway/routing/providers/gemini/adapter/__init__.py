import base64
import json
import time
from typing import Dict, Any, List, AsyncGenerator
import uuid
import httpx
from pathlib import Path
from .....schemas import (
    GatewayResponse, GatewayChoice, GatewayMessage, GatewayUsage,
    GatewayStreamChunk, GatewayStreamChoice, GatewayStreamDelta, 
    GatewayAttachment, FileMetadata,
    MessageContentPart, ImageContent, AudioContent, VideoContent
)
from ...base import BaseAdapter
from ....exceptions import ResponseValidationError
from .attachment import (
    MediaContentHandler, FlatFileHandler, 
    UrlContextHandler, OpenAiVisionFallbackHandler, 
    BaseAttachmentHandler, 
    FileHelper
)
from ..utils import _parse_iso_to_timestamp
import structlog
import re, os

logger = structlog.get_logger(__name__)

MAX_TEXT_LENGTH = 100_000       # Giới hạn số ký tự text thuần cho mỗi tin nhắn (~100k ký tự)
MAX_LOCAL_FILE_SIZE_MB = 15     # Giới hạn file local tối đa 15MB để tránh phình Payload Base64

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
                print(f"⚠️ [WARNING] Lỗi phân tách chuỗi Base64 thô: {str(e)}")
                # Tiếp tục luồng, coi như text bình thường

        # 2. Kiểm tra nếu là một đường dẫn Local File thô hợp lệ
        # Sử dụng try-except để phòng trường hợp chuỗi chứa ký tự đặc biệt gây lỗi Path
        try:
            potential_path = Path(stripped_text)
            if potential_path.is_file():
                # Kiểm tra dung lượng file
                file_size_mb = os.path.getsize(potential_path) / (1024 * 1024)
                if file_size_mb > MAX_LOCAL_FILE_SIZE_MB:
                    print(f"⚠️ [WARNING] File local '{stripped_text}' vượt quá giới hạn ({file_size_mb:.2f}MB > {MAX_LOCAL_FILE_SIZE_MB}MB). Bỏ qua chuyển đổi Base64.")
                    return [{"text": f"[Local File Path - Too Large]: {stripped_text}"}]
                
                # Tiến hành chuyển sang Base64
                mime_type = FileHelper.detect_mime_type(potential_path) if 'FileHelper' in globals() else "application/octet-stream"
                with open(potential_path, "rb") as f:
                    base64_data = base64.b64encode(f.read()).decode('utf-8')
                
                return [{"inlineData": {"mimeType": mime_type, "data": base64_data}}]
        except Exception as e:
            # Không phải path hợp lệ hoặc lỗi đọc file -> Tiếp tục luồng xuống dưới
            pass

        # 3. Kiểm tra nếu là một URL Web công khai thô
        if re.match(r'^https?://[^\s]+$', stripped_text):
            return [{"text": f"[Link tham khảo]: {stripped_text}"}]

        # 4. Nếu là Text bình thường -> Kiểm tra và cắt độ dài chống lỗi Payload dữ liệu
        if len(text_content) > MAX_TEXT_LENGTH:
            print(f"⚠️ [WARNING] Text content quá dài ({len(text_content)} ký tự). Hệ thống tự động cắt ngắn về {MAX_TEXT_LENGTH} ký tự.")
            text_content = text_content[:MAX_TEXT_LENGTH] + "\n...[Nội dung bị cắt bớt do quá dài]..."

        parts.append({"text": text_content})
        return parts
    
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
                            print(f"⚠️ [WARNING] Part text quá dài. Tiến hành cắt ngắn.")
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
                            print(f"⚠️ [ERROR] Lỗi khi Sub-Adapter {handler.__class__.__name__} xử lý: {str(e)}")
                    else:
                        print(f"⚠️ [WARNING] Không tìm thấy Sub-Adapter xử lý cho type: {part_type}")

            if gemini_parts:
                gemini_contents.append({
                    "role": gemini_role,
                    "parts": gemini_parts
                })

        # --- Đóng gói cấu trúc Gemini Body ---
        gemini_body = {"contents": gemini_contents}
        if "tools" in request:
            gemini_body["tools"] = request["tools"]

        if "model" in request:
            gemini_body["model"] = request["model"]

        # Giới hạn độ dài cho cả System Instruction tổng
        if system_instruction_text.strip():
            sys_text = system_instruction_text.strip()
            if len(sys_text) > MAX_TEXT_LENGTH:
                print(f"⚠️ [WARNING] System instruction quá dài. Tiến hành cắt ngắn.")
                sys_text = sys_text[:MAX_TEXT_LENGTH]
            gemini_body["systemInstruction"] = {
                "parts": [{"text": sys_text}]
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

    def _parse_gemini_parts_to_content(self, parts: List[Dict[str, Any]]) -> List[MessageContentPart]:
        """
        Chuyển đổi danh sách các 'parts' thô từ Gemini API 
        thành danh sách MessageContentPart chuẩn hóa của Gateway.
        """
        content_parts: List[MessageContentPart] = []

        for part in parts:
            # Kịch bản 1: Văn bản thô hoặc Bảng biểu (Markdown/HTML)
            if "text" in part:
                content_parts.append(MessageContentPart(
                    type="text", # Giả định MessageContentType.TEXT
                    text=part["text"]
                ))

            # Kịch bản 2: File/Hình ảnh sinh ra từ Code Execution (inlineData)
            elif "inlineData" in part:
                inline_data = part["inlineData"]
                mime_type = inline_data.get("mimeType", "application/octet-stream")
                base64_data = inline_data.get("data", "")

                # Xây dựng cấu trúc FileMetadata bọc bên trong
                file_metadata = FileMetadata(
                    created_at=int(time.time())
                )

                # Đóng gói GatewayAttachment
                attachment = GatewayAttachment(
                    id=f"att-{uuid.uuid4()}",
                    filename=f"ai_generated_{int(time.time())}",
                    mime_type=mime_type,
                    base64_data=base64_data,
                    metadata=file_metadata
                )

                # Phân loại dựa trên mimeType để map đúng trường DTO
                if mime_type.startswith("image/"):
                    content_parts.append(MessageContentPart(
                        type="image",
                        image=ImageContent(attachment=attachment, detail="auto")
                    ))
                elif mime_type.startswith("audio/"):
                    content_parts.append(MessageContentPart(
                        type="audio",
                        audio=AudioContent(attachment=attachment)
                    ))
                elif mime_type.startswith("video/"):
                    content_parts.append(MessageContentPart(
                        type="video",
                        video=VideoContent(attachment=attachment)
                    ))
                else:
                    content_parts.append(MessageContentPart(
                        type="file",
                        file=attachment
                    ))

            # Kịch bản 3: Mã nguồn Python mà AI tự viết để chạy
            elif "executableCode" in part:
                code_text = part["executableCode"].get("code", "")
                content_parts.append(MessageContentPart(
                    type="text",
                    text=f"\n\n```python\n# [AI Executed Code]\n{code_text}\n```"
                ))

            # Kịch bản 4: Kết quả log in ra màn hình của đoạn mã đó (stdout)
            elif "codeExecutionResult" in part:
                output_log = part["codeExecutionResult"].get("output", "")
                content_parts.append(MessageContentPart(
                    type="text",
                    text=f"\n\n```text\n# [Execution Output]\n{output_log}\n```"
                ))

        return content_parts

    async def adapt_chat_response(self, response: httpx.Response) -> GatewayResponse:
        """Chuyển đổi response JSON từ Gemini về GatewayResponse kèm Multimodal Content Parts."""
        try:
            response_data = response.json()
            
            choices = []
            for idx, candidate in enumerate(response_data.get("candidates", [])):
                gemini_content = candidate.get("content", {})
                parts = gemini_content.get("parts", [])
                
                # Khai phá mảng parts đa phương tiện thành danh sách ContentParts DTO
                parsed_content = self._parse_gemini_parts_to_content(parts)
                
                # Chuẩn hóa finish_reason
                gemini_finish_reason = candidate.get("finishReason", "STOP").upper()
                reason_mapping = {
                    "STOP": "stop",
                    "MAX_TOKENS": "length",
                    "SAFETY": "content_filter",
                    "RECITATION": "content_filter",
                }
                finish_reason = reason_mapping.get(gemini_finish_reason, "stop")

                choices.append(GatewayChoice(
                    index=idx,
                    message=GatewayMessage(
                        role="assistant",
                        content=parsed_content  # Ép kiểu danh sách List[MessageContentPart] chuẩn
                    ),
                    finish_reason=finish_reason
                ))

            # Khôi phục Token Usage
            usage_data = response_data.get("usageMetadata", {})
            usage = GatewayUsage(
                prompt_tokens=usage_data.get("promptTokenCount", 0),
                completion_tokens=usage_data.get("candidatesTokenCount", 0),
                total_tokens=usage_data.get("totalTokenCount", 0)
            )

            return GatewayResponse(
                id=f"chatcmpl-{uuid.uuid4()}",
                model=response_data.get("modelVersion", "gemini-model"),
                choices=choices,
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
        Chuẩn hóa stream của Gemini, xử lý bóc tách Text, Code Log 
        và các cấu trúc đa phương tiện thực thi.
        """
        stream_id = f"chatcmpl-{uuid.uuid4()}"
        buffer = ""
        
        async for chunk in response.aiter_text():
            buffer += chunk
            while True:
                buffer = buffer.lstrip()
                if buffer.startswith('['):
                    buffer = buffer[1:].lstrip()
                if buffer.startswith(','):
                    buffer = buffer[1:].lstrip()
                if buffer.startswith(']'):
                    buffer = buffer[1:].lstrip()
                    break
                
                if not buffer:
                    break

                try:
                    obj, idx = json.JSONDecoder().raw_decode(buffer)
                    buffer = buffer[idx:].lstrip()
                    
                    text_delta = ""
                    chunk_metadata = {}
                    finish_reason = None
                    
                    if "candidates" in obj:
                        candidate = obj["candidates"][0]
                        
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
                            parts = candidate["content"]["parts"]
                            
                            # Chạy hàm bóc tách để phân lập text / file
                            parsed_parts = self._parse_gemini_parts_to_content(parts)
                            
                            extracted_attachments = []
                            for part in parsed_parts:
                                if part.type == "text":
                                    text_delta += part.text
                                elif part.type == "image" and part.image:
                                    extracted_attachments.append(part.image.attachment.model_dump())
                                    text_delta += f"\n\n*[Hệ thống: Biểu đồ hình ảnh đã được tạo]*\n"
                                elif part.type == "file" and part.file:
                                    extracted_attachments.append(part.file.model_dump())
                            
                            if extracted_attachments:
                                chunk_metadata["stream_attachments"] = extracted_attachments

                    gateway_usage = None
                    if "usageMetadata" in obj:
                        usage_data = obj["usageMetadata"]
                        gateway_usage = GatewayUsage(
                            prompt_tokens=usage_data.get("promptTokenCount", 0),
                            completion_tokens=usage_data.get("candidatesTokenCount", 0),
                            total_tokens=usage_data.get("totalTokenCount", 0)
                        )
                    
                    yield GatewayStreamChunk(
                        id=stream_id,
                        model=obj.get("modelVersion", "gemini-model"),
                        choices=[GatewayStreamChoice(
                            index=0,
                            delta=GatewayStreamDelta(
                                content=text_delta if text_delta else None, 
                                role="assistant"
                            ),
                            finish_reason=finish_reason
                        )],
                        provider="gemini",
                        created=int(time.time()),
                        usage=gateway_usage,
                        metadata=chunk_metadata if chunk_metadata else {}
                    )
                    
                except json.JSONDecodeError:
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
    
    async def adapt_file_upload_response(self, response: Any) -> GatewayAttachment:
        """
        Chuyển đổi response thành công từ bước PUT (Resumable Upload) của Gemini File API 
        sang cấu trúc chuẩn hóa GatewayAttachment DTO.
        """
        try:
            # 1. Kiểm tra mã trạng thái HTTP (Gemini Resumable PUT thường trả về 200 OK hoặc 201 Created)
            status_code = getattr(response, "status_code", None) or getattr(response, "status", 200)
            if status_code not in (200, 201):
                raw_text = ""
                try:
                    raw_text = response.text if hasattr(response, "text") else str(await response.text())
                except Exception:
                    pass
                logger.error("Gemini File API returned failure status code", status_code=status_code, response=raw_text)
                raise ValueError(f"Gemini File API upload failed with status code {status_code}. Response: {raw_text}")

            # Trích xuất dữ liệu JSON từ Response
            # LƯU Ý: Nếu dùng aiohttp, hãy đổi thành: raw_data = await response.json()
            raw_data = response.json()
            if not raw_data:
                logger.error("Gemini File API returned an empty response body")
                raise ValueError("Empty response received from Gemini File API")

            # Gemini bọc dữ liệu trong trường "file"
            file_data: Dict[str, Any] = raw_data.get("file", raw_data)

            # 2. Bóc tách và chuẩn hóa thông tin cơ bản
            raw_name = file_data.get("name", "")  # Cấu trúc trả về thường là: "files/abc123xyz"
            file_id = raw_name.replace("files/", "") if "files/" in raw_name else raw_name
            
            # Xử lý kích thước file (Gemini trả về dạng chuỗi sizeBytes)
            size_bytes = file_data.get("sizeBytes")
            try:
                final_size = int(size_bytes) if size_bytes is not None else None
            except (ValueError, TypeError):
                final_size = None

            # 3. Xử lý timestamps (Chuyển ISO 8601 string thành Unix timestamp int)
            created_timestamp = _parse_iso_to_timestamp(iso_str=file_data.get("createTime"))
            modified_timestamp = _parse_iso_to_timestamp(iso_str=file_data.get("updateTime"))

            # 4. Tạo FileMetadata DTO chi tiết
            metadata_dto = FileMetadata(
                checksum_sha256=file_data.get("sha256Hash"),
                created_at=created_timestamp,
                modified_at=modified_timestamp,
                page_count=None,
                language=None,
                encoding=None
            )

            # 5. Khởi tạo và trả về GatewayAttachment hoàn chỉnh
            # Trường 'uri' cực kỳ quan trọng, chính là link 'https://generativelanguage.googleapis.com/...' 
            # để nạp vào cấu trúc fileData sau này.
            file_uri = file_data.get("uri")
            if not file_uri:
                logger.warning("Field 'uri' is missing from Gemini file upload response", file_id=file_id)

            attachment = GatewayAttachment(
                id=file_id,
                filename=file_data.get("displayName"),
                mime_type=file_data.get("mimeType", "application/octet-stream"),
                size=final_size,
                uri=file_uri,
                base64_data=None,  # Đã chuyển lên File API thành công nên trường này luôn để None
                metadata=metadata_dto
            )

            logger.info(
                "Successfully adapted Gemini File API response to GatewayAttachment",
                file_id=file_id,
                file_uri=file_uri
            )
            return attachment

        except Exception as e:
            logger.error("Failed to adapt Gemini file upload response due to unexpected error", error=str(e))
            raise e
        
    async def adapt_file_list_response(self, response: Any) -> List[GatewayAttachment]:
        """Chuyển đổi danh sách response từ Gemini File API sang List[GatewayAttachment]."""
        try:
            # Parse JSON từ response

            raw_data = response.json()
            # Gemini API trả về key 'files' chứa danh sách các file
            gemini_files = raw_data.get("files", [])
            logger.info("Found raw files in Gemini response, starting mapping", count=len(gemini_files))

            final_attachments: List[GatewayAttachment] = []

            for f in gemini_files:
                try:
                    # Giả lập một response bọc độc lập để tái sử dụng hàm adapt_file_upload_response đã viết
                    # Hoặc bạn có thể bóc tách logic parse của hàm đó ra thành một hàm private riêng lẻ
                    mock_file_response = {"file": f}
                    attachment = await self.adapt_file_upload_response(mock_file_response)
                    final_attachments.append(attachment)
                except Exception as map_err:
                    logger.error(
                        "Error mapping raw Gemini file data to GatewayAttachment", 
                        file_name=f.get("name"), 
                        error=str(map_err)
                    )
                    continue

            logger.info(
                "Successfully mapped Gemini files list to Gateway DTOs", 
                raw_count=len(gemini_files), 
                mapped_count=len(final_attachments)
            )
            return final_attachments

        except Exception as e:
            logger.error("Failed to adapt Gemini file list response", error=str(e))
            raise e

