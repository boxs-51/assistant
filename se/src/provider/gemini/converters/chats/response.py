import re
import uuid
import time
import json
import codecs
import httpx
from typing import List, Any, Dict, AsyncGenerator, Tuple, Optional

from .....domain.schemas import (
    MessageContentPart,
    GatewayAttachment,
    FileMetadata,
    ImageContent,
    AudioContent,
    VideoContent,
    DocumentContent,
    UrlContent,
    GatewayResponse,
    ResponseMetaData,
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
from ....core.tool_contract import ProviderToolNameMap

import structlog
logger = structlog.get_logger(__name__)


class ResponseChats:

    def _extract_citations(self, candidate: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Trích xuất và chuẩn hóa Grounding / Citation Metadata từ Gemini candidate
        về cấu trúc dữ liệu citations tiêu chuẩn.
        """
        citations_data = []
        grounding_meta = candidate.get("groundingMetadata") or candidate.get("grounding_metadata") or {}
        citation_meta = candidate.get("citationMetadata") or candidate.get("citation_metadata") or {}

        grounding_chunks = grounding_meta.get("groundingChunks") or grounding_meta.get("grounding_chunks") or []
        grounding_supports = grounding_meta.get("groundingSupports") or grounding_meta.get("grounding_supports") or []

        cit_index = 1

        if grounding_supports:
            for support in grounding_supports:
                segment = support.get("segment") or {}
                start_idx = segment.get("startIndex", 0)
                end_idx = segment.get("endIndex", 0)
                text_seg = segment.get("text", "")

                chunk_indices = support.get("groundingChunkIndices") or support.get("grounding_chunk_indices") or []
                for chunk_idx in chunk_indices:
                    if chunk_idx < len(grounding_chunks):
                        g_chunk = grounding_chunks[chunk_idx]
                        web_data = g_chunk.get("web") or {}
                        file_data = g_chunk.get("file") or {}

                        source_type = "web" if web_data else ("file" if file_data else "web")
                        url = web_data.get("uri") or file_data.get("uri") or ""
                        title = web_data.get("title") or file_data.get("title") or f"Source {cit_index}"
                        snippet = g_chunk.get("snippet") or text_seg or ""
                        file_id = file_data.get("fileId") or f"file-{uuid.uuid4().hex[:8]}"

                        citations_data.append({
                            "id": f"cit_{cit_index:03d}",
                            "index": cit_index,
                            "file_id": file_id,
                            "source_type": source_type,
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "start_index": start_idx,
                            "end_index": end_idx,
                            "text_segment": text_seg
                        })
                        cit_index += 1
        elif grounding_chunks:
            for g_chunk in grounding_chunks:
                web_data = g_chunk.get("web") or {}
                file_data = g_chunk.get("file") or {}

                source_type = "web" if web_data else ("file" if file_data else "web")
                url = web_data.get("uri") or file_data.get("uri") or ""
                title = web_data.get("title") or file_data.get("title") or f"Source {cit_index}"
                snippet = g_chunk.get("snippet") or ""
                file_id = file_data.get("fileId") or f"file-{uuid.uuid4().hex[:8]}"

                citations_data.append({
                    "id": f"cit_{cit_index:03d}",
                    "index": cit_index,
                    "file_id": file_id,
                    "source_type": source_type,
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                    "start_index": 0,
                    "end_index": 0,
                    "text_segment": ""
                })
                cit_index += 1

        # Fallback xử lý legacy citationMetadata / citationSources
        if not citations_data and citation_meta:
            citation_sources = citation_meta.get("citationSources") or citation_meta.get("citation_sources") or []
            for src in citation_sources:
                url = src.get("uri", "")
                start_idx = src.get("startIndex", 0)
                end_idx = src.get("endIndex", 0)
                citations_data.append({
                    "id": f"cit_{cit_index:03d}",
                    "index": cit_index,
                    "file_id": f"file-{uuid.uuid4().hex[:8]}",
                    "source_type": "web",
                    "title": url or f"Source {cit_index}",
                    "url": url,
                    "snippet": "",
                    "start_index": start_idx,
                    "end_index": end_idx,
                    "text_segment": ""
                })
                cit_index += 1

        return citations_data

    def _parse_and_split_text_content(self, text: str) -> List[MessageContentPart]:
        """
        Phân tích văn bản thô từ Gemini, tách các khối mã (code blocks) ra khỏi
        văn bản thường và chuyển đổi thành danh sách các MessageContentPart.
        """
        if not text:
            return []

        code_block_pattern = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
        parts: List[MessageContentPart] = []
        last_end = 0

        for match in code_block_pattern.finditer(text):
            start, end = match.span()
            if start > last_end:
                plain_text = text[last_end:start].strip()
                if plain_text:
                    parts.append(
                        MessageContentPart(type="text", text=plain_text)
                    )

            # Keep the original fenced Markdown as flat text so presentation
            # remains a client concern after TextContent removal.
            parts.append(
                MessageContentPart(type="text", text=match.group(0))
            )
            last_end = end

        if last_end < len(text):
            remaining_text = text[last_end:].strip()
            if remaining_text:
                parts.append(MessageContentPart(type="text", text=remaining_text))

        if not parts:
            parts.append(MessageContentPart(type="text", text=text))

        return parts

    def _parse_gemini_parts_to_content(
        self, 
        parts: List[Dict[str, Any]], 
        citations: Optional[List[Dict[str, Any]]] = None,
        tool_names: ProviderToolNameMap | None = None,
    ) -> Tuple[List[MessageContentPart], List[GatewayToolCall], str]:
        """
        Chuyển đổi danh sách các 'parts' thô từ Gemini thành MessageContentPart chuẩn, 
        trích xuất GatewayToolCall và bóc tách luồng suy nghĩ (reasoning_content).
        """
        content_parts: List[MessageContentPart] = []
        tool_calls: List[GatewayToolCall] = []
        reasoning_delta = ""

        for part in parts:
            # 0. Xử lý yêu cầu gọi Tool từ Gemini (Function Calling)
            if "functionCall" in part:
                fc = part["functionCall"]
                tool_name = fc.get("name", "")
                if tool_names is not None and tool_name:
                    tool_name = tool_names.logical_name(tool_name)
                tool_args = fc.get("args", {})

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

            # 1. Xử lý khối suy nghĩ (Thinking / Reasoning)
            is_thought = part.get("thought") is True or ("thought" in part and bool(part["thought"]))
            if is_thought:
                if isinstance(part.get("thought"), str):
                    thought_text = part["thought"]
                else:
                    thought_text = part.get("text", "")

                if thought_text:
                    reasoning_delta += thought_text
                    content_parts.append(
                        MessageContentPart(
                            type="thinking",
                            text=thought_text,
                            metadata={"citations": citations or []}
                        )
                    )
                continue

            # 2. Xử lý khối văn bản thường
            if "text" in part:
                content_parts.extend(self._parse_and_split_text_content(part["text"]))

            # 3. File/Hình ảnh/Tài liệu nhị phân (inlineData)
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

            # 4. Xử lý URL Content
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

            # 5. Code Python mô hình tự tạo để chạy
            elif "executableCode" in part:
                exec_code = part["executableCode"]
                code_text = exec_code.get("code", "")
                language = exec_code.get("language", "python").lower()

                content_parts.append(MessageContentPart(
                    type="text",
                    text=f"\n\n```{language}\n# [AI Executed Code]\n{code_text}\n```"
                ))

            # 6. Đầu ra stdout của mã nguồn vừa chạy
            elif "codeExecutionResult" in part:
                exec_result = part["codeExecutionResult"]
                output_log = exec_result.get("output", "")

                content_parts.append(MessageContentPart(
                    type="text",
                    text=f"\n\n```text\n# [Execution Output]\n{output_log}\n```"
                ))

        return content_parts, tool_calls, reasoning_delta

    async def adapt_chat(
        self,
        response: httpx.Response,
        *,
        tool_names: ProviderToolNameMap | None = None,
    ) -> GatewayResponse:
        """Chuyển đổi response JSON từ Gemini về GatewayResponse kèm Thinking Parts, Citations và Tool Calls."""
        try:
            response_data = response.json()
            
            choices = []
            for idx, candidate in enumerate(response_data.get("candidates", [])):
                gemini_content = candidate.get("content", {})
                parts = gemini_content.get("parts", [])
                
                # Trích xuất danh sách citations
                citations_data = self._extract_citations(candidate)

                # Khai phá mảng parts thành ContentParts DTO, GatewayToolCall và suy nghĩ
                parsed_content, tool_calls, _ = self._parse_gemini_parts_to_content(
                    parts,
                    citations=citations_data,
                    tool_names=tool_names,
                )
                
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
            metadata= ResponseMetaData(
                provider="gemini",
                raw_response=response_data
            )

            return GatewayResponse(
                id=f"chatcmpl-{uuid.uuid4()}",
                model=response_data.get("modelVersion", "gemini-model"),
                choices=choices,
                usage=usage,
                metadata=metadata,
            )
        except ResponseValidationError:
            raise
        except (KeyError, IndexError, AttributeError, TypeError, ValueError, json.JSONDecodeError) as e:
            logger.error("Hỏng cấu trúc response từ Gemini:", error=str(e), response=response.text)
            raise ResponseValidationError(
                f"Hỏng cấu trúc response từ Gemini: {str(e)}", 
                provider_name="google"
            ) from e

    async def adapt_chat_stream(
        self,
        response: httpx.Response,
        *,
        tool_names: ProviderToolNameMap | None = None,
    ) -> AsyncGenerator[GatewayStreamChunk, None]:
        """
        Chuẩn hóa Gemini streaming response.

        Framing strategy:
        - Đọc raw bytes để bảo đảm UTF-8 không bị cắt giữa codepoint.
        - Không tự đếm `{}` vì JSON string có thể chứa `{}`.
        - Dùng JSONDecoder.raw_decode() để xác định chính xác ranh giới object.
        - Hỗ trợ nhiều JSON object liên tiếp trong cùng network chunk.
        """
        stream_id = f"chatcmpl-{uuid.uuid4()}"
        buffer = ""

        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        json_decoder = json.JSONDecoder()

        async def process_object(obj: Dict[str, Any]):
            finish_reason = None
            parsed_parts = []
            tool_calls = []
            reasoning_delta = ""
            citations_data = []

            if "candidates" in obj and obj["candidates"]:
                candidate = obj["candidates"][0]

                citations_data = self._extract_citations(candidate)

                if "finishReason" in candidate:
                    gemini_finish_reason = str(
                        candidate["finishReason"]
                    ).upper()

                    reason_mapping = {
                        "STOP": "stop",
                        "MAX_TOKENS": "length",
                        "SAFETY": "content_filter",
                        "RECITATION": "content_filter",
                    }

                    finish_reason = reason_mapping.get(
                        gemini_finish_reason,
                        "stop",
                    )

                content = candidate.get("content") or {}
                parts = content.get("parts") or []

                if parts:
                    (
                        parsed_parts,
                        tool_calls,
                        reasoning_delta,
                    ) = self._parse_gemini_parts_to_content(
                        parts,
                        citations=citations_data,
                        tool_names=tool_names,
                    )

                    if tool_calls and finish_reason == "stop":
                        finish_reason = "tool_calls"

            text_delta = ""

            for part in parsed_parts:
                if (
                    part.type == "text"
                    and isinstance(part.text, str)
                ):
                    text_delta += part.text

            gateway_usage = None

            usage_data = obj.get("usageMetadata")

            if isinstance(usage_data, dict):
                gateway_usage = GatewayUsage(
                    prompt_tokens=usage_data.get(
                        "promptTokenCount",
                        0,
                    ),
                    completion_tokens=usage_data.get(
                        "candidatesTokenCount",
                        0,
                    ),
                    total_tokens=usage_data.get(
                        "totalTokenCount",
                        0,
                    ),
                )

            if (
                not text_delta
                and not reasoning_delta
                and not tool_calls
                and not finish_reason
                and not gateway_usage
                and not citations_data
            ):
                return None

            model_obj = obj.get(
                "modelVersion",
                "gemini-model",
            )
            delta_obj = GatewayStreamDelta(
                content=(text_delta if text_delta else None),
                reasoning_content=(reasoning_delta if reasoning_delta else None),
                role="assistant",
                tool_calls=(tool_calls if tool_calls else None),
            )
            choices_obj = GatewayStreamChoice(
                index=0,
                delta=delta_obj,
                finish_reason=finish_reason,
            )
            metadata = ResponseMetaData(
                provider="gemini",
                citations=citations_data if citations_data else None,
                content_parts=[
                    p.model_dump(exclude_none=True)
                    for p in parsed_parts
                ] if parsed_parts else None,
            )
            return GatewayStreamChunk(
                id=stream_id,
                model=model_obj,
                choices=[choices_obj],
                usage=gateway_usage,
                metadata=metadata,
            )

        async for byte_chunk in response.aiter_bytes():
            if not byte_chunk:
                continue

            buffer += decoder.decode(
                byte_chunk,
                final=False,
            )

            while True:
                # Bỏ whitespace / delimiter ở đầu.
                buffer = buffer.lstrip()

                while buffer.startswith((",", "[", "]")):
                    buffer = buffer[1:].lstrip()

                if not buffer:
                    break

                # Một số gateway/provider có prefix kiểu:
                # payload:
                # data:
                #
                # Tìm JSON object đầu tiên.
                if not buffer.startswith("{"):
                    start_index = buffer.find("{")

                    if start_index == -1:
                        # Chưa đủ dữ liệu để tìm JSON object.
                        break

                    buffer = buffer[start_index:]

                try:
                    obj, end_index = json_decoder.raw_decode(buffer)
                except json.JSONDecodeError:
                    # JSON chưa hoàn chỉnh.
                    # Giữ nguyên buffer và chờ network chunk tiếp theo.
                    break

                # Xóa chính xác object vừa decode.
                buffer = buffer[end_index:]

                if not isinstance(obj, dict):
                    logger.warning(
                        "gemini_stream_unexpected_json_type",
                        json_type=type(obj).__name__,
                    )
                    continue

                chunk = await process_object(obj)

                if chunk is not None:
                    yield chunk

        # Flush UTF-8 decoder.
        final_str = decoder.decode(b"", final=True)

        if final_str:
            buffer += final_str

        # Process object cuối nếu stream kết thúc ngay sau một object.
        while True:
            buffer = buffer.lstrip()

            while buffer.startswith((",", "[", "]")):
                buffer = buffer[1:].lstrip()

            if not buffer:
                break

            if not buffer.startswith("{"):
                start_index = buffer.find("{")

                if start_index == -1:
                    logger.warning(
                        "gemini_stream_unparsed_tail",
                        buffer=buffer[:1000],
                    )
                    raise ResponseValidationError(
                        "Invalid trailing data in Gemini stream.",
                        provider_name="gemini",
                    )

                buffer = buffer[start_index:]

            try:
                obj, end_index = json_decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                logger.warning(
                    "gemini_stream_incomplete_tail",
                    buffer=buffer[:1000],
                )
                raise ResponseValidationError(
                    "Incomplete JSON object at end of Gemini stream.",
                    provider_name="gemini",
                )

            buffer = buffer[end_index:]

            if not isinstance(obj, dict):
                continue

            chunk = await process_object(obj)

            if chunk is not None:
                yield chunk