import json
import random
import time
from types import SimpleNamespace
import structlog

from ..schemas.message import GatewayMessage, MessageContentPart
from ..schemas.tool import GatewayToolCall, FunctionCall
from ..schemas.enums import MessageContentType
from ..schemas.attachment import GatewayAttachment, ImageContent, UrlContent

logger = structlog.get_logger(__name__)


class MockLLMProvider:
    """Mô-đun sinh phản hồi giả lập (Mock Mode) nâng cao hỗ trợ:
    - Luồng suy nghĩ chuyên sâu & nhiều bước (Deep Multi-Step Reasoning)
    - Mô phỏng Tìm kiếm Web & Trích dẫn nguồn (Web Search & Citations/Grounding)
    - Đa phương tiện (Hình ảnh, Attachment, Link)
    - Phân tích lệnh đầu vào (Tạo ảnh, Search, Suy nghĩ lâu, Gọi tool)
    """

    def __init__(self, registry):
        self.registry = registry

    def _build_multi_step_reasoning(self, query: str) -> str:
        """Sinh chuỗi suy nghĩ nhiều bước (CoT) mô phỏng DeepSeek R1 / OpenAI o1."""
        query_snippet = query[:60] if query else "yêu cầu hệ thống"
        
        steps = [
            f"🧠 [Giai đoạn 1: Bóc tách Yêu cầu]\n"
            f"  • Nhận input: '{query_snippet}'\n"
            f"  • Xác định mục tiêu: Cần đưa ra câu trả lời chi tiết, chính xác, kèm các tình huống biên (edge cases).\n"
            f"  • Ràng buộc: Tuân thủ định dạng Gateway DTO và tối ưu hóa trải nghiệm Stream.",

            f"🔎 [Giai đoạn 2: Phân tích Khả thi & Đánh giá Rủi ro]\n"
            f"  • Phân tích thuật toán: Đánh giá độ phức tạp O(N) vs O(1) đối với luồng xử lý Data Chunk.\n"
            f"  • Kiểm tra dữ liệu đính kèm: Không phát hiện mã độc hoặc tham số bất thường.\n"
            f"  • Giả định kịch bản lỗi: Nếu mạng bị lag/ngắt kết nối SSE giữa chừng -> Gateway cần cơ chế Retry/Resync.",

            f"🔄 [Giai đoạn 3: Tự kiểm tra & Tự sửa lỗi (Self-Correction)]\n"
            f"  • Đang xem xét lại giải pháp sơ bộ...\n"
            f"  • [Phát hiện vấn đề]: Giải pháp ban đầu có thể gây nghẽn RAM nếu stream chuỗi reasoning quá dài.\n"
            f"  • [Tối ưu hóa]: Tách nhỏ token suy nghĩ và chèn các quãng nghỉ ngắn (Sleep buffers) để giải phóng Thread Event Loop.",

            f"⚡ [Giai đoạn 4: Lập Kế hoạch Thực thi Phản hồi]\n"
            f"  1. Trình bày tổng quan giải pháp theo cấu trúc Markdown rõ ràng.\n"
            f"  2. Cung cấp ví dụ code minh họa chi tiết.\n"
            f"  3. Đưa ra bảng so sánh đánh giá để người dùng dễ theo dõi.\n"
            f"  • Chuyển trạng thái: HOÀN TẤT SUY NGHĨ -> SẴN SÀNG SINH CONTENT CHÍNH."
        ]
        
        return "\n\n".join(steps)

    def generate_response(self, final_messages: list, step: int, enable_stream: bool):
        last_msg = final_messages[-1] if final_messages else None
        last_role = getattr(last_msg, "role", "")

        last_text = ""
        if last_msg and hasattr(last_msg, "content"):
            if isinstance(last_msg.content, list):
                last_text = " ".join([
                    part.text for part in last_msg.content 
                    if getattr(part, "type", "") in ("text", MessageContentType.TEXT) and getattr(part, "text", None)
                ])
            else:
                last_text = str(last_msg.content or "")

        last_text_lower = last_text.lower()

        # Khai báo các biến dữ liệu phản hồi
        thinking_text: str = ""
        text_response: str = ""
        tool_calls = None
        image_content: ImageContent = None
        citations_data: list = None  # Chứa danh sách trích dẫn (Grounding Metadata)
        citation_mode: int = None   # 1: Inline Chunk, 2: Final Chunk, 3: Multi-Citation Combo
        thinking_delay_mult: float = 1.0  

        # =================================================================
        # 1. XỬ LÝ THEO INTENT & NGỮ CẢNH ĐẦU VÀO
        # =================================================================

        # TRƯỜNG HỢP A: Nhận kết quả từ Tool (Role = "tool")
        if last_role == "tool":
            tool_name = getattr(last_msg, "name", "tool")
            content_snippet = last_text[:120] + "..." if len(last_text) > 120 else last_text
            
            thinking_text = (
                f"[Phân tích kết quả Tool Execution]\n"
                f"- Đã nhận payload từ tool `{tool_name}`.\n"
                f"- Kiểm tra tính hợp lệ của dữ liệu trả về...\n"
                f"- Tổng hợp thông tin để tạo báo cáo phản hồi cho người dùng."
            )
            text_response = (
                f"Đã xử lý thành công kết quả từ công cụ `{tool_name}`:\n\n"
                f"```json\n{{\n  \"tool\": \"{tool_name}\",\n  \"status\": \"success\",\n  \"output_preview\": \"{content_snippet}\"\n}}\n```\n\n"
                f"Bạn có thể xem chi tiết tài liệu tích hợp tại [Gateway Developer Docs](https://docs.gateway.ai/tools/{tool_name})."
            )

        # TRƯỜNG HỢP B: Yêu cầu Tìm kiếm Web / Phân tích dữ liệu / Tra cứu RAG (Citations Mock)
        elif any(kw in last_text_lower for kw in ["tìm kiếm", "web search", "search", "trích dẫn", "citation", "tra cứu", "phân tích dữ liệu", "rag"]):
            citation_mode = random.choice([1, 2, 3])

            if citation_mode == 1:
                # KỊCH BẢN 1: Gửi citations ngay trong Chunk chứa nội dung tương ứng
                thinking_text = (
                    f"🌐 [Search & RAG - Kịch bản 1: Inline Chunk Citations]\n"
                    f"  • Đã nhận kết quả tra cứu cho query: '{last_text[:40]}...'\n"
                    f"  • Phương thức Stream: Gửi metadata citation đính kèm trực tiếp trong chunk văn bản chứa [1], [2]."
                )
                text_response = (
                    f"Dựa trên dữ liệu tra cứu và phân tích mới nhất :[1]\n\n"
                    f"1. **Thị trường AI Gateway**: Các hệ thống Multi-LLM Orchestration đang tập trung chuẩn hóa DTO để tối ưu hóa Streaming SSE.[1]\n"
                    f"2. **Cấu trúc Trích dẫn (Citations)**: Trích dẫn được truyền tải thông qua trường `metadata.citations` trong DTO hoặc dạng `UrlContent` trong `content` .[2]"
                )
                citations_data = [
                    {
                        "id": "cit_001",
                        "index": 1,
                        "file_id": "file-report-2026-pdf",
                        "source_type": "file",
                        "title": "Báo cáo Thị trường & AI Orchestration 2026.pdf",
                        "url": "https://example.com/reports/ai-2026.pdf",
                        "snippet": "Các hệ thống Multi-LLM Orchestration đang phát triển mạnh mẽ...",
                        "start_index": 0,
                        "end_index": 165,
                        "text_segment": "Dựa trên dữ liệu tra cứu và phân tích mới nhất [1]..."
                    },
                    {
                        "id": "cit_002",
                        "index": 2,
                        "file_id": "file-dto-spec-v1",
                        "source_type": "file",
                        "title": "Tài liệu Kiến trúc Gateway DTO Specification.pdf",
                        "url": "https://docs.gateway.ai/spec/dto",
                        "snippet": "Trích dẫn DTO chuẩn hóa hỗ trợ cả Streaming Chunk...",
                        "start_index": 167,
                        "end_index": 312,
                        "text_segment": "2. Cấu trúc Trích dẫn (Citations) [2]..."
                    }
                ]

            elif citation_mode == 2:
                # KỊCH BẢN 2: Gom toàn bộ citations và gửi duy nhất ở Chunk cuối cùng
                thinking_text = (
                    f"🌐 [Search & RAG - Kịch bản 2: Final Chunk Citations]\n"
                    f"  • Đã nhận kết quả tra cứu cho query: '{last_text[:40]}...'\n"
                    f"  • Phương thức Stream: Giữ nguyên luồng text stream và đính kèm danh sách citations đầy đủ trong Chunk cuối cùng."
                )
                text_response = (
                    f"Dựa trên dữ liệu tra cứu và phân tích mới nhất :[1]\n\n"
                    f"1. **Thị trường AI Gateway**: Các hệ thống Multi-LLM Orchestration đang tập trung chuẩn hóa DTO để tối ưu hóa Streaming SSE .[1]\n"
                    f"2. **Cấu trúc Trích dẫn (Citations)**: Trích dẫn được truyền tải thông qua trường `metadata.citations` trong DTO .[2]"
                )
                citations_data = [
                    {
                        "id": "cit_001",
                        "index": 1,
                        "file_id": "file-report-2026-pdf",
                        "source_type": "file",
                        "title": "Báo cáo Thị trường & AI Orchestration 2026.pdf",
                        "url": "https://example.com/reports/ai-2026.pdf",
                        "snippet": "Các hệ thống Multi-LLM Orchestration đang phát triển...",
                        "start_index": 0,
                        "end_index": 165,
                        "text_segment": "..."
                    },
                    {
                        "id": "cit_002",
                        "index": 2,
                        "file_id": "file-dto-spec-v1",
                        "source_type": "file",
                        "title": "Tài liệu Kiến trúc Gateway DTO Specification.pdf",
                        "url": "https://docs.gateway.ai/spec/dto",
                        "snippet": "Trích dẫn DTO chuẩn hóa...",
                        "start_index": 167,
                        "end_index": 312,
                        "text_segment": "..."
                    }
                ]

            else:
                # KỊCH BẢN 3: Nội dung có nhiều nguồn trích dẫn phức tạp (Multi-Citations)
                thinking_text = (
                    f"🌐 [Search & RAG - Kịch bản 3: Multi-Citations Combo]\n"
                    f"  • Tìm thấy nhiều tài liệu tham khảo đồng thời [1], [2], [3], [4].\n"
                    f"  • Phương thức Stream: Gửi citation trực tiếp tại chunk chứa marker và đồng bộ tổng hợp ở chunk cuối."
                )
                text_response = (
                    f"Tổng hợp phân tích từ nhiều nguồn tra cứu độc lập :[1] [2]\n\n"
                    f"1. **Hiệu năng Orchestration**: Tối ưu độ trễ SSE stream thông qua thuật toán zero-copy buffer .[1] [3]\n"
                    f"2. **Tích hợp Citations**: Gắn kèm trực tiếp metadata vào chunk hoặc đẩy về cuối luồng stream .[2] [4]"
                )
                citations_data = [
                    {
                        "id": "cit_001", "index": 1, "file_id": "file-gartner-2026", "source_type": "web",
                        "title": "Gartner AI Infrastructure Trends 2026", "url": "https://gartner.com/ai-2026",
                        "snippet": "Phân tích xu hướng hạ tầng AI...", "start_index": 0, "end_index": 60, "text_segment": "..."
                    },
                    {
                        "id": "cit_002", "index": 2, "file_id": "file-openai-arch", "source_type": "web",
                        "title": "OpenAI Gateway Best Practices", "url": "https://openai.com/research/gateway",
                        "snippet": "Kiến trúc Gateway tiêu chuẩn...", "start_index": 61, "end_index": 120, "text_segment": "..."
                    },
                    {
                        "id": "cit_003", "index": 3, "file_id": "file-sse-bench", "source_type": "file",
                        "title": "High Performance SSE Benchmark.pdf", "url": "https://docs.gateway.ai/sse-bench",
                        "snippet": "Báo cáo thử nghiệm hiệu năng SSE...", "start_index": 121, "end_index": 180, "text_segment": "..."
                    },
                    {
                        "id": "cit_004", "index": 4, "file_id": "file-citation-proto", "source_type": "file",
                        "title": "Citation Binding Protocol v2.pdf", "url": "https://docs.gateway.ai/citations-v2",
                        "snippet": "Giao thức gắn citation vào Stream Chunk...", "start_index": 181, "end_index": 240, "text_segment": "..."
                    }
                ]

        # TRƯỜNG HỢP C: Yêu cầu Tạo Ảnh
        elif any(kw in last_text_lower for kw in ["tạo ảnh", "vẽ ảnh", "generate image", "draw", "tao anh"]):
            seed = random.randint(1000, 9999)
            image_url = f"https://picsum.photos/seed/{seed}/1024/768"
            
            thinking_text = (
                f"[Phân tích Prompt Tạo Ảnh]\n"
                f"1. Trích xuất từ khóa chính: '{last_text[:50]}'\n"
                f"2. Rendering completed với seed={seed}."
            )
            text_response = (
                f"Tôi đã tạo xong hình ảnh dựa trên mô tả của bạn:\n\n"
                f"![Generated Image]({image_url})\n\n"
                f"- **Seed**: `{seed}`\n"
                f"- **Link ảnh gốc**: [Download Full Resolution]({image_url})"
            )
            image_content = ImageContent(
                attachment=GatewayAttachment(
                    id=f"img_{seed}",
                    filename=f"generated_{seed}.jpg",
                    mime_type="image/jpeg",
                    uri=image_url,
                    source="url"
                ),
                detail="high"
            )

        # TRƯỜNG HỢP D: Suy nghĩ sâu
        elif any(kw in last_text_lower for kw in ["suy nghĩ lâu", "suy nghĩ sâu", "deep dive", "reasoning"]):
            thinking_text = self._build_multi_step_reasoning(last_text)
            thinking_delay_mult = 2.5
            text_response = (
                f"## 📊 Báo Cáo Phân Tích Kỹ Thuật Chuyên Sâu\n\n"
                f"Sau khi trải qua **4 giai đoạn suy nghĩ và tự kiểm chứng**, dưới đây là giải pháp tối ưu dành cho bạn:\n\n"
                f"### 1. Đánh Giá Tổng Quan\n"
                f"Hệ thống đã phân tích các rủi ro tiềm ẩn và đề xuất mô hình xử lý Async Gateway Stream đạt hiệu năng cao.\n\n"
                f"### 2. Các Bước Triển Khai Kỹ Thuật\n"
                f"- **Bước 1**: Tiếp nhận stream chunk từ Provider.\n"
                f"- **Bước 2**: Đóng gói `reasoning_content` vào SSE event.\n"
                f"- **Bước 3**: Render giao diện UI Client với hiệu ứng 'Model đang suy nghĩ...'.\n\n"
                f"### 3. Bảng Tham Số Tối Ưu\n"
                f"| Tiêu chí | Giá trị đề xuất | Ghi chú |\n"
                f"| :--- | :--- | :--- |\n"
                f"| **Chunk Timeout** | `30s` | Đảm bảo không bị timeout khi suy nghĩ sâu |\n"
                f"| **Buffer Size** | `1024 bytes` | Tối ưu hóa throughput |\n"
                f"| **Retry Logic** | `Exponential Backoff` | Xử lý sự cố mạng |\n\n"
                f"🔗 Tham khảo thêm: [Gateway Optimization Guide](https://docs.gateway.ai/performance)"
            )

        # TRƯỜNG HỢP E: Gọi Tool
        else:
            matched_tool = None
            if hasattr(self.registry, "tools") and self.registry.tools:
                for t_name in self.registry.tools.keys():
                    if t_name.lower() in last_text_lower:
                        matched_tool = t_name
                        break

            if matched_tool:
                thinking_text = f"[Kiểm tra Tool Registry]\n- Tìm thấy tool: `{matched_tool}`."
                text_response = f"Tôi sẽ thực thi công cụ `{matched_tool}` để xử lý yêu cầu của bạn."
                tool_calls = [
                    GatewayToolCall(
                        id=f"mock_call_{int(time.time())}_{step}",
                        type="function",
                        function=FunctionCall(
                            name=matched_tool,
                            arguments=json.dumps({"input": last_text[:30]}, ensure_ascii=False)
                        )
                    )
                ]
            else:
                thinking_text = (
                    f"[Phân tích yêu cầu chung]\n"
                    f"1. Tiếp nhận prompt: '{last_text[:40]}...'\n"
                    f"2. Khởi tạo câu trả lời mặc định cho Step {step}."
                )
                templates = [
                    (
                        f"Dưới đây là thông tin phản hồi cho yêu cầu của bạn:\n\n"
                        f"1. **Trạng thái**: Chế độ Offline Mock đang hoạt động tốt.\n"
                        f"2. **Dữ liệu tiếp nhận**: `{last_text[:60]}`\n"
                        f"3. **Tài liệu**: [Xem API Gateway Docs](https://docs.gateway.ai/v1)\n\n"
                        f"Cảm ơn bạn đã thử nghiệm!"
                    ),
                    (
                        f"Tôi đã ghi nhận yêu cầu tại bước `{step}`.\n\n"
                        f"```json\n"
                        f"{{\n"
                        f'  "status": "success",\n'
                        f'  "step": {step},\n'
                        f'  "mode": "offline_mock"\n'
                        f"}}\n"
                        f"```"
                    )
                ]
                text_response = templates[(step - 1) % len(templates)]

        # =================================================================
        # 2. ĐÓNG GÓI RESPONSE (STREAMING VS NON-STREAMING)
        # =================================================================

        if enable_stream:
            def _stream_generator():
                # 2.1 Stream Thinking Content
                if thinking_text:
                    lines = thinking_text.split("\n")
                    for line in lines:
                        words = line.split(" ")
                        for word_idx, word in enumerate(words):
                            chunk_text = word + (" " if word_idx < len(words) - 1 else "")
                            yield SimpleNamespace(
                                choices=[
                                    SimpleNamespace(
                                        delta=SimpleNamespace(
                                            content="",
                                            reasoning_content=chunk_text,
                                            tool_calls=None
                                        )
                                    )
                                ]
                            )
                            time.sleep(0.015 * thinking_delay_mult)
                        
                        yield SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    delta=SimpleNamespace(
                                        content="",
                                        reasoning_content="\n",
                                        tool_calls=None
                                    )
                                )
                            ]
                        )

                time.sleep(0.1)

                # 2.2 Stream Text Response
                if text_response:
                    content_words = text_response.split(" ")
                    for i, word in enumerate(content_words):
                        chunk_text = word + (" " if i < len(content_words) - 1 else "")
                        
                        # Đính kèm metadata.citations trực tiếp trong chunk văn bản chứa marker [1], [2]... (Kịch bản 1 & 3)
                        chunk_kwargs = {}
                        if citation_mode in (1, 3) and citations_data:
                            matched_cits = [
                                cit for cit in citations_data 
                                if f"[{cit['index']}]" in word
                            ]
                            if matched_cits:
                                chunk_kwargs["metadata"] = {"citations": matched_cits}

                        yield SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    delta=SimpleNamespace(
                                        content=chunk_text,
                                        reasoning_content="",
                                        tool_calls=None
                                    )
                                )
                            ],
                            **chunk_kwargs
                        )
                        time.sleep(0.01)

                # 2.3 Stream Tool Calls (nếu có)
                if tool_calls:
                    tool_call_deltas = [
                        SimpleNamespace(
                            index=idx,
                            id=tc.id,
                            function=SimpleNamespace(
                                name=tc.function.name,
                                arguments=tc.function.arguments
                            )
                        ) for idx, tc in enumerate(tool_calls)
                    ]
                    yield SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(
                                    content="",
                                    reasoning_content="",
                                    tool_calls=tool_call_deltas
                                )
                            )
                        ]
                    )

                # 2.4 Stream Final Chunk chứa Metadata Citations đầy đủ (Kịch bản 2 & 3)
                if citations_data and citation_mode in (2, 3):
                    yield SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(content="", reasoning_content="", tool_calls=None),
                                finish_reason="stop"
                            )
                        ],
                        metadata={"citations": citations_data}
                    )

            return _stream_generator()

        else:
            # Dạng Non-Streaming: Đóng gói Message Content Parts & Metadata
            content_parts = []
            metadata = {}

            # Gắn Citations vào metadata của ContentPart
            if citations_data:
                metadata["citations"] = citations_data
            
            if thinking_text:
                content_parts.append(
                    MessageContentPart(
                        type=getattr(MessageContentType, "THINKING", "thinking"),
                        text=thinking_text
                    )
                )

            if text_response:
                content_parts.append(
                    MessageContentPart(
                        type=getattr(MessageContentType, "TEXT", "text"),
                        text=text_response,
                        metadata=metadata
                    )
                )

            if image_content:
                content_parts.append(
                    MessageContentPart(
                        type=getattr(MessageContentType, "IMAGE", "image"),
                        data=image_content
                    )
                )

            final_content = content_parts if len(content_parts) > 1 else text_response

            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=GatewayMessage(
                            role="assistant",
                            content=final_content,
                            tool_calls=tool_calls,
                        )
                    )
                ]
            )