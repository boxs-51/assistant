# Kế Hoạch Di Chuyển (Migration Plan) - Refactor AI Gateway

Tài liệu này theo dõi tiến độ tái cấu trúc (refactoring) hệ thống AI Gateway để tuân thủ các nguyên tắc SOLID, đặc biệt là OCP, và áp dụng Adapter Pattern.

Mục tiêu: Gateway chỉ làm việc với các schema đã được chuẩn hóa (`GatewayResponse`, `GatewayStreamChunk`), hoàn toàn không biết về định dạng response của từng provider cụ thể.

## Giai đoạn 1: Nền tảng và Chuẩn hóa (Foundation & Standardization)

- [ X ] **Bước 1: Tạo các Model Chuẩn Hóa.**
    - [ X ] Tạo file mới `src/gateway/schemas.py`.
    - [ X ] Định nghĩa các Pydantic model cho non-streaming: `GatewayMessage`, `GatewayChoice`, `GatewayUsage`, `GatewayResponse`.
    - [ X ] Định nghĩa các Pydantic model cho streaming: `GatewayStreamDelta`, `GatewayStreamChoice`, `GatewayStreamChunk`.

- [ X ] **Bước 2: Cập nhật `BaseProvider` Interface.**
    - [ X ] Mở file `src/gateway/routing/providers/base.py`.
    - [ X ] Thêm phương thức abstract `async def normalize_response(...) -> GatewayResponse`.
    - [ X ] Thêm phương thức abstract `async def normalize_stream(...) -> AsyncGenerator[GatewayStreamChunk, None]`.

## Giai đoạn 2: Triển khai các Adapter (Adapter Implementation)

*Thực hiện tuần tự cho từng provider để giảm thiểu rủi ro.*

- [ X ] **Bước 3: Refactor Từng Provider Một.**
    - [ X ] **OpenAIProvider:**
        - [ X ] Triển khai `normalize_response` bằng cách parse JSON và khởi tạo `GatewayResponse`.
        - [ X ] Triển khai `normalize_stream` (nếu cần).
    - [ X ] **OllamaProvider:**
        - [ X ] Triển khai `normalize_response` để chuyển đổi schema của Ollama thành `GatewayResponse`.
        - [ X ] Triển khai `normalize_stream` để chuyển đổi stream của Ollama thành `AsyncGenerator[GatewayStreamChunk, None]`.
    - [ X ] **GeminiProvider:**
        - [ X ] Di chuyển logic từ `_adapt_request_body` vào `normalize_response`.
        - [ X ] Triển khai `normalize_stream` (có thể `raise NotImplementedError` nếu chưa ưu tiên).
    - [ X ] **Các Provider khác:**
        - [ X ] Lặp lại quy trình tương tự cho các provider còn lại (Anthropic, Groq, Azure OpenAI...).

## Giai đoạn 3: Tích hợp vào Luồng Thực thi (Integration)

- [ X ] **Bước 4: Refactor `ProviderExecutor`.**
    - [ X ] Mở file `src/gateway/routing/executor.py`.
    - [ X ] Thay đổi phương thức `execute` để gọi `provider.normalize_response` sau khi nhận được `httpx.Response`.
    - [ X ] Thay đổi kiểu trả về của `execute` từ `httpx.Response` thành `GatewayResponse`.

- [ X ] **Bước 5: Refactor `ModelRouter`.**
    - [ X ] Mở file `src/gateway/router.py`.
    - [ X ] Cập nhật `execute_with_fallback` để nhận và trả về `GatewayResponse`.

- [ X ] **Bước 6: Refactor Endpoint `chat_completions_proxy`.**
    - [ X ] Mở file `src/gateway/base_gateway.py`.
    - [ X ] Thay đổi logic xử lý response để làm việc trực tiếp với đối tượng `GatewayResponse` đã được chuẩn hóa.
    - [ X ] Loại bỏ hoàn toàn việc truy cập `response.json()["choices"]` hoặc các key đặc thù của provider.

## Giai đoạn 4: Hoàn thiện Streaming và Dọn dẹp

- [ ] **Bước 7 (Nâng cao): Refactor Toàn diện Luồng Streaming.**
    - [ ] Tạo một luồng thực thi riêng cho streaming trong `ProviderExecutor` và `ModelRouter` (ví dụ: `stream_with_fallback`).
    - [ ] Luồng này sẽ gọi `provider.normalize_stream` và `yield from` các `GatewayStreamChunk` đã được chuẩn hóa.
    - [ ] Cập nhật logic `if is_streaming:` trong endpoint để gọi `stream_with_fallback` và trả về `StreamingResponse`.

- [ ] **Bước 8: Viết Unit Test.**
    - [ ] Viết unit test cho các phương thức `normalize_response` và `normalize_stream` của từng provider để đảm bảo chúng hoạt động đúng như một Adapter.
    - [ ] Cập nhật các unit test hiện có cho endpoint để chúng làm việc với `GatewayResponse` mock.

Chúc bạn thực hiện quá trình refactor thành công!