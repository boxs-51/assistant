import re
import uuid
import time
import json
import httpx
from typing import List, Any, Dict, AsyncGenerator

from ......schemas import (
    TextContent,
    MessageContentPart,
    GatewayAttachment,
    FileMetadata,
    ImageContent,
    AudioContent,
    VideoContent,
    DocumentContent,
    UrlContent,
    GatewayResponse,
    GatewayChoice,
    GatewayMessage,
    GatewayUsage,
    GatewayStreamChunk,
    GatewayStreamChoice,
    GatewayStreamDelta
)
from ...file_extension import FileHelper
from .....exceptions import ResponseValidationError

import structlog
logger=structlog.get_logger(__name__)

class ResponseChats():

    def _parse_and_split_text_content(self, text: str) -> List[MessageContentPart]:
        """
        Phân tích văn bản thô từ Gemini, tách các khối mã (code blocks) ra khỏi
        văn bản thường và chuyển đổi thành danh sách các MessageContentPart.
        """
        if not text:
            return []

        # Regex để tìm các khối mã ```...```
        code_block_pattern = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
        parts: List[MessageContentPart] = []
        last_end = 0

        for match in code_block_pattern.finditer(text):
            start, end = match.span()
            # 1. Lấy phần văn bản thường đứng trước khối mã
            if start > last_end:
                plain_text = text[last_end:start].strip()
                if plain_text:
                    parts.append(MessageContentPart(type="text", data=TextContent(data=plain_text, format="structured"))) # Mặc định là structured để client render markdown

            # 2. Lấy khối mã
            language = match.group(1).lower() or "text"
            code_text = match.group(2)
            parts.append(MessageContentPart(type="text", data=TextContent(data=code_text, format="code", language=language)))
            last_end = end

        # 3. Lấy phần văn bản còn lại sau khối mã cuối cùng
        if last_end < len(text):
            remaining_text = text[last_end:].strip()
            if remaining_text:
                parts.append(MessageContentPart(type="text", data=TextContent(data=remaining_text, format="structured")))
        
        # Nếu không tìm thấy khối mã nào, toàn bộ là một phần văn bản
        if not parts:
            parts.append(MessageContentPart(type="text", data=TextContent(data=text, format="structured")))

        return parts
    
    def _parse_gemini_parts_to_content(self, parts: List[Dict[str, Any]]) -> List[MessageContentPart]:
        """Chuyển đổi danh sách các 'parts' thô từ Gemini thành MessageContentPart chuẩn."""
        content_parts: List[MessageContentPart] = []

        for part in parts:
            # 1. Xử lý khối văn bản (Tất cả dồn về TextContent với format động)
            if "text" in part:
                # Phân tách văn bản thành các phần nhỏ (text thường và code)
                content_parts.extend(self._parse_and_split_text_content(part["text"]))

            # 2. File/Hình ảnh/Tài liệu nhị phân (inlineData)
            elif "inlineData" in part:
                inline_data = part["inlineData"]
                mime_type = inline_data.get("mimeType", "application/octet-stream")
                base64_data = inline_data.get("data", "")

                attachment = GatewayAttachment(
                    id=f"att-{uuid.uuid4()}",
                    filename=f"ai_generated_{int(time.time())}",
                    mime_type=mime_type,
                    base64_data=base64_data,
                    source="base64",
                    metadata=FileMetadata(created_at=int(time.time()))
                )

                # Phân loại Multimedia & Document
                if mime_type.startswith("image/"):
                    content_parts.append(MessageContentPart(
                        type="image",
                        data=ImageContent(attachment=attachment, detail="auto")
                    ))
                elif mime_type.startswith("audio/"):
                    content_parts.append(MessageContentPart(
                        type="audio",
                        data=AudioContent(attachment=attachment)
                    ))
                elif mime_type.startswith("video/"):
                    content_parts.append(MessageContentPart(
                        type="video",
                        data=VideoContent(attachment=attachment)
                    ))
                # Xử lý DocumentContent (PDF, Word, Excel, CSV, v.v.)
                elif (
                    mime_type == "application/pdf" or 
                    mime_type.startswith("application/msword") or 
                    mime_type.startswith("application/vnd.openxmlformats-officedocument") or
                    mime_type in ["text/csv", "application/epub+zip"]
                ):
                    content_parts.append(MessageContentPart(
                        type="document",
                        data=DocumentContent(attachment=attachment)
                    ))
                else:
                    content_parts.append(MessageContentPart(
                        type="file",
                        data=attachment
                    ))

            # 3. Xử lý URL Content (Dành cho ngữ cảnh URL / Google Search tool)
            elif "url" in part or ("fileData" in part and part["fileData"].get("fileUri", "").startswith(("http://", "https://"))):
                url_str = part.get("url") or part.get("fileData", {}).get("fileUri")
                
                if url_str:
                    # Kiểm tra xem đây là link bài viết web hay link dẫn thẳng tới file tĩnh
                    if not any(url_str.lower().endswith(ext) for ext in ['.jpg', '.png', '.mp4', '.mp3', '.pdf', '.docx', '.csv']):
                        content_parts.append(MessageContentPart(
                            type="url",
                            data=UrlContent(url=url_str, crawl=True)
                        ))
                    else:
                        # Nếu là link file tĩnh, wrap thành GatewayAttachment với source="url"
                        mime_type = FileHelper.detect_mime_type(url_str)
                        attachment = GatewayAttachment(
                            id=f"att-{uuid.uuid4()}",
                            uri=url_str,
                            mime_type=mime_type or "application/octet-stream",
                            source="url"
                        )
                        content_parts.append(MessageContentPart(type="file", data=attachment))

            # 4. Code Python mô hình tự tạo để chạy
            elif "executableCode" in part:
                exec_code = part["executableCode"]
                code_text = exec_code.get("code", "")
                # Lấy ngôn ngữ từ Gemini trả về, mặc định là python nếu không có
                language = exec_code.get("language", "python").lower()
                
                content_parts.append(MessageContentPart(
                    type="text",
                    data=TextContent(
                        data=f"\n\n```{language}\n# [AI Executed Code]\n{code_text}\n```", 
                        format="code",
                        language=language  # Gán luôn vào thuộc tính language của TextContent
                    )
                ))

            # 5. Đầu ra stdout của mã nguồn vừa chạy
            elif "codeExecutionResult" in part:
                exec_result = part["codeExecutionResult"]
                output_log = exec_result.get("output", "")
                
                # Bạn có thể giữ format="code" với text thuần, hoặc dùng "plain" tùy sở thích UI
                content_parts.append(MessageContentPart(
                    type="text",
                    data=TextContent(
                        data=f"\n\n```text\n# [Execution Output]\n{output_log}\n```", 
                        format="code"
                    )
                ))

        return content_parts

    async def adapt_chat(self, response: httpx.Response) -> GatewayResponse:
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
            logger.error(f"Hỏng cấu trúc response từ Gemini:",error=str(e),response=response.text)
            raise ResponseValidationError(
                f"Hỏng cấu trúc response từ Gemini: {str(e)}", 
                provider_name="google"
            ) from e
        
    async def adapt_chat_stream(self, response: httpx.Response) -> AsyncGenerator[GatewayStreamChunk, None]:
        """
        Chuẩn hóa stream của Gemini, xử lý bóc tách Text, Code Log 
        và các cấu trúc đa phương tiện thực thi.
        """
        stream_id = f"chatcmpl-{uuid.uuid4()}"
        buffer = ""
        # last_content_parts để xử lý delta lũy kế (nếu cần trong tương lai)
        
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
                            
                            # Chạy hàm bóc tách để phân lập text / code / file
                            parsed_parts = self._parse_gemini_parts_to_content(parts)
                            
                            # Đóng gói toàn bộ các part đã phân tích vào metadata
                            # để client có thể tái cấu trúc đầy đủ nội dung.
                            if parsed_parts:
                                chunk_metadata["content_parts"] = [p.model_dump(exclude_none=True) for p in parsed_parts]

                            # Trích xuất text_delta để tương thích ngược với các client đơn giản
                            # chỉ xử lý text. Logic này sẽ cộng dồn text từ các part.
                            text_delta = ""
                            for part in parsed_parts:
                                if part.type == "text" and isinstance(part.data, TextContent):
                                    text_delta += part.data.data

                    gateway_usage = None
                    if "usageMetadata" in obj:
                        usage_data = obj["usageMetadata"]
                        gateway_usage = GatewayUsage(
                            prompt_tokens=usage_data.get("promptTokenCount", 0),
                            completion_tokens=usage_data.get("candidatesTokenCount", 0),
                            total_tokens=usage_data.get("totalTokenCount", 0)
                        )
                    
                    # Nếu không có delta text và không có lý do kết thúc, có thể bỏ qua chunk rỗng
                    if not text_delta and not finish_reason and not gateway_usage:
                        continue
                    
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
