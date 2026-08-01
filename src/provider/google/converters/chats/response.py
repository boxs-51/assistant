import re
import uuid
import time
import json
import codecs
import httpx
from typing import List, Any, Dict, AsyncGenerator, Tuple, Optional

from .....domain.schemas import (
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
    GatewayStreamDelta,
    GatewayToolCall,
    FunctionCall
)
from ...file_extension import FileHelper
from ....exceptions import ResponseValidationError

import structlog
logger = structlog.get_logger(__name__)


class ResponseChats:

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
                    parts.append(MessageContentPart(type="text", data=TextContent(data=plain_text, format="structured")))

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

    def _parse_gemini_parts_to_content(self, parts: List[Dict[str, Any]]) -> Tuple[List[MessageContentPart], List[GatewayToolCall]]:
        """
        Chuyển đổi danh sách các 'parts' thô từ Gemini thành MessageContentPart chuẩn 
        và trích xuất danh sách các GatewayToolCall (nếu có).
        """
        content_parts: List[MessageContentPart] = []
        tool_calls: List[GatewayToolCall] = []

        for part in parts:
            # 0. Xử lý yêu cầu gọi Tool từ Gemini (Function Calling)
            if "functionCall" in part:
                fc = part["functionCall"]
                tool_name = fc.get("name", "")
                tool_args = fc.get("args", {})

                # Chuyển đổi args thành chuỗi JSON String hợp lệ cho DTO GatewayToolCall
                if isinstance(tool_args, (dict, list)):
                    args_str = json.dumps(tool_args, ensure_ascii=False)
                elif isinstance(tool_args, str):
                    args_str = tool_args
                else:
                    args_str = json.dumps(tool_args)

                tool_calls.append(
                    GatewayToolCall(
                        id=fc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                        type="function",
                        function=FunctionCall(
                            name=tool_name,
                            arguments=args_str
                        )
                    )
                )
                continue

            # 1. Xử lý khối văn bản
            if "text" in part:
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

            # 3. Xử lý URL Content
            elif "url" in part or ("fileData" in part and part["fileData"].get("fileUri", "").startswith(("http://", "https://"))):
                url_str = part.get("url") or part.get("fileData", {}).get("fileUri")
                
                if url_str:
                    if not any(url_str.lower().endswith(ext) for ext in ['.jpg', '.png', '.mp4', '.mp3', '.pdf', '.docx', '.csv']):
                        content_parts.append(MessageContentPart(
                            type="url",
                            data=UrlContent(url=url_str, crawl=True)
                        ))
                    else:
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
                language = exec_code.get("language", "python").lower()
                
                content_parts.append(MessageContentPart(
                    type="text",
                    data=TextContent(
                        data=f"\n\n```{language}\n# [AI Executed Code]\n{code_text}\n```", 
                        format="code",
                        language=language
                    )
                ))

            # 5. Đầu ra stdout của mã nguồn vừa chạy
            elif "codeExecutionResult" in part:
                exec_result = part["codeExecutionResult"]
                output_log = exec_result.get("output", "")
                
                content_parts.append(MessageContentPart(
                    type="text",
                    data=TextContent(
                        data=f"\n\n```text\n# [Execution Output]\n{output_log}\n```", 
                        format="code"
                    )
                ))

        return content_parts, tool_calls

    async def adapt_chat(self, response: httpx.Response) -> GatewayResponse:
        """Chuyển đổi response JSON từ Gemini về GatewayResponse kèm Multimodal Content Parts và Tool Calls."""
        try:
            response_data = response.json()
            
            choices = []
            for idx, candidate in enumerate(response_data.get("candidates", [])):
                gemini_content = candidate.get("content", {})
                parts = gemini_content.get("parts", [])
                
                # Khai phá mảng parts thành ContentParts DTO và danh sách GatewayToolCall
                parsed_content, tool_calls = self._parse_gemini_parts_to_content(parts)
                
                # Chuẩn hóa finish_reason
                gemini_finish_reason = candidate.get("finishReason", "STOP").upper()
                reason_mapping = {
                    "STOP": "tool_calls" if tool_calls else "stop",
                    "MAX_TOKENS": "length",
                    "SAFETY": "content_filter",
                    "RECITATION": "content_filter",
                }
                finish_reason = reason_mapping.get(gemini_finish_reason, "stop")

                choices.append(GatewayChoice(
                    index=idx,
                    message=GatewayMessage(
                        role="assistant",
                        content=parsed_content,
                        tool_calls=tool_calls if tool_calls else None
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
            logger.error("Hỏng cấu trúc response từ Gemini:", error=str(e), response=response.text)
            raise ResponseValidationError(
                f"Hỏng cấu trúc response từ Gemini: {str(e)}", 
                provider_name="google"
            ) from e

    async def adapt_chat_stream(self, response: httpx.Response) -> AsyncGenerator[GatewayStreamChunk, None]:
        """
        Chuẩn hóa stream của Gemini, xử lý bóc tách Text, Code Log, 
        cấu trúc đa phương tiện và Streaming Tool Calls thời gian thực.
        """
        stream_id = f"chatcmpl-{uuid.uuid4()}"
        buffer = ""
        
        # Tạo Incremental Decoder để xử lý byte chunk UTF-8 không bị đứt đoạn
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')

        async for byte_chunk in response.aiter_bytes():
            if not byte_chunk:
                continue

            # Decode an toàn: Các byte lẻ của ký tự đa byte sẽ được giữ lại cho lần lặp sau
            buffer += decoder.decode(byte_chunk, final=False)
            
            # Xử lý toàn bộ các đối tượng JSON hoàn chỉnh trong buffer
            while True:
                if not buffer:
                    break

                # Tìm vị trí bắt đầu của JSON Object
                start_index = buffer.find('{')
                if start_index == -1:
                    # Nếu không chứa '{', loại bỏ khoảng trắng hoặc ký tự phân tách mảng JSON
                    if buffer.strip() in "[],":
                        buffer = ""
                    break
                
                # Bỏ qua các ký tự thừa trước ký tự '{'
                buffer = buffer[start_index:]
                nesting_level = 0
                end_index = -1

                for i, char in enumerate(buffer):
                    if char == '{':
                        nesting_level += 1
                    elif char == '}':
                        nesting_level -= 1
                        if nesting_level == 0:
                            end_index = i + 1
                            break
                
                # Khi tìm thấy một JSON Object hoàn chỉnh
                if end_index != -1:
                    json_str = buffer[:end_index]
                    buffer = buffer[end_index:]
                    
                    try:
                        obj = json.loads(json_str)

                        chunk_metadata = {}
                        finish_reason = None
                        parsed_parts = []
                        tool_calls = []

                        if "candidates" in obj and obj["candidates"]:
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
                                parsed_parts, tool_calls = self._parse_gemini_parts_to_content(parts)

                                if parsed_parts:
                                    chunk_metadata["content_parts"] = [
                                        p.model_dump(exclude_none=True) for p in parsed_parts
                                    ]

                                if tool_calls and finish_reason == "stop":
                                    finish_reason = "tool_calls"
                        
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
                        
                        if not text_delta and not tool_calls and not finish_reason and not gateway_usage:
                            continue

                        yield GatewayStreamChunk(
                            id=stream_id,
                            model=obj.get("modelVersion", "gemini-model"),
                            choices=[GatewayStreamChoice(
                                index=0,
                                delta=GatewayStreamDelta(
                                    content=text_delta if text_delta else None,
                                    role="assistant",
                                    tool_calls=tool_calls if tool_calls else None
                                ),
                                finish_reason=finish_reason
                            )],
                            provider="gemini",
                            created=int(time.time()),
                            usage=gateway_usage,
                            metadata=chunk_metadata if chunk_metadata else {}
                        )

                    except json.JSONDecodeError:
                        logger.warning("Failed to decode JSON object from stream", json_chunk=json_str)
                        continue
                else:
                    # Chưa nhận đủ byte để đóng ngoặc '}', dừng lại chờ chunk kế tiếp
                    break

        # Flush nốt byte còn dư nếu luồng kết thúc đột ngột
        final_str = decoder.decode(b'', final=True)
        if final_str:
            buffer += final_str