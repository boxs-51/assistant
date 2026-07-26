# Metadata
- **Last Scan:** 2026-07-26
- **Source Files:** ~20+
- **Hash:** N/A
- **Depends On:** `fastapi`, `httpx`, `pydantic`, `structlog`, `opentelemetry`, `pyyaml`
- **Scanned Files:** `__init__.py`, `executor.py`, `factory.py`, `discovery.py`, `registry.py`, `core/*`, `policies/*`, `openai/*`, `google/*`, `ollama/*`

# 📂 Thư Mục: `provider`

## 1. Architecture Decisions & Design Patterns
Module `provider` chịu trách nhiệm giao tiếp với các dịch vụ LLM bên ngoài (OpenAI, Google, Ollama, etc.). Đây là một module được thiết kế với độ trừu tượng cao, khả năng mở rộng tốt và khả năng phục hồi lỗi mạnh mẽ.

- **Kiến trúc tổng thể (Architectural Style):**
  - **Framework/Plugin Architecture:** Module `core` định nghĩa một "framework", và mỗi nhà cung cấp (provider) như `openai` hay `google` là một "plugin" tuân thủ theo framework đó.
  - **Policy-Based Design:** Các hành vi phức tạp như retry, routing, và circuit breaking được tách ra thành các "policy" riêng biệt, có thể được kết hợp và áp dụng một cách linh hoạt.
  - **Resilience-Oriented:** Kiến trúc được xây dựng xung quanh các pattern phục hồi lỗi như Retry, Fallback, và Circuit Breaker.

- **Design Patterns chính:**
  - **Composition over Inheritance:** `BaseProvider` được "ghép" lại từ các thành phần nhỏ hơn (`AuthStrategy`, `ApiTypeMapper`, `ModelMapper`) thay vì kế thừa một lớp cơ sở khổng lồ.
  - **Adapter:** Mỗi module provider (e.g., `openai/converters`) hoạt động như một Adapter, chuyển đổi cấu trúc request/response chung của Gateway sang định dạng riêng của từng provider và ngược lại.
  - **Strategy:** Được sử dụng ở nhiều cấp:
    - `AuthStrategy`: Chọn cách xác thực (Bearer, API Key).
    - `RoutingPolicy`: Chọn chuỗi provider để thực thi.
    - `RetryPolicy`: Quyết định khi nào và làm thế nào để thử lại một request.
    - `BaseLoadBalancer`: Chọn provider tiếp theo trong một chuỗi.
  - **Factory:** `ProviderFactory` là nơi duy nhất biết cách tạo ra các instance của provider cụ thể, giúp tách biệt phần còn lại của hệ thống khỏi các lớp triển khai.
  - **Registry:** `ProviderRegistry` lưu trữ các provider đã được khởi tạo để dễ dàng truy xuất.
  - **Circuit Breaker:** `ProviderExecutor` sử dụng `CircuitBreakerManager` để bọc các lệnh gọi mạng, ngăn ngừa các lỗi hàng loạt.

## 2. Dependency & Ownership Graph
- `ModelRouter` (`__init__.py`) là "nhạc trưởng" cấp cao nhất. Nó sở hữu `ProviderExecutor`, `RoutingPolicy`, và `ProviderRegistry`.
- `ProviderDiscovery` được `ModelRouter` sử dụng một lần khi khởi tạo để điền vào `ProviderRegistry`.
- `ProviderExecutor` sở hữu `RetryPolicy` và là client của `CircuitBreakerManager`.
- `RoutingPolicy` là client của `ProviderRegistry` (để lấy danh sách provider).
- Mỗi provider cụ thể (e.g., `OpenAIProvider`) sở hữu các thành phần Adapter và Mapper của riêng nó.

## 3. Thread Model & Event/Data Flow
- **Thread Model:** Hoàn toàn bất đồng bộ (`asyncio`).
- **Luồng xử lý chính (`execute_with_fallback`):**
  1.  **Routing:** `ModelRouter` nhận request, hỏi `RoutingPolicy` để lấy chuỗi provider cần thử (e.g., `[openai, gemini]`).
  2.  **Health Check:** `ModelRouter` lọc chuỗi này, loại bỏ các provider đang bị "ngắt mạch" (Circuit Breaker is OPEN).
  3.  **Capability Check:** `ModelRouter` tiếp tục lọc chuỗi, loại bỏ các provider không hỗ trợ tính năng được yêu cầu (e.g., streaming).
  4.  **Execution Loop:** `ModelRouter` lặp qua chuỗi provider cuối cùng.
      a. Với mỗi provider, nó gọi `ProviderExecutor.execute()`.
      b. **Resilience Pipeline (`ProviderExecutor`):**
          i.   Kiểm tra Circuit Breaker (`before_request`).
          ii.  Thực thi request, được bọc trong `RetryPolicy`.
          iii. `RetryPolicy` sẽ tự động thử lại nếu gặp lỗi có thể retry (lỗi mạng, rate limit).
          iv.  Cập nhật trạng thái Circuit Breaker (`on_success` / `on_failure`).
      c. Nếu `ProviderExecutor` thành công, `ModelRouter` trả về kết quả ngay lập tức.
      d. Nếu `ProviderExecutor` thất bại (do lỗi không thể retry hoặc hết số lần retry), `ModelRouter` chuyển sang provider tiếp theo trong chuỗi.
  5.  **Failure:** Nếu tất cả các provider trong chuỗi đều thất bại, `ModelRouter` ném ra `NoAvailableProviderError`.

## 4. Public APIs & Configuration
- **Public API:** Giao diện chính mà `gateway` sử dụng là `ModelRouter`, cụ thể là các phương thức `execute_with_fallback` và `stream_with_fallback`.
- **Configuration:** Toàn bộ module được điều khiển mạnh mẽ bởi `settings.provider`, `settings.openai`, `settings.gemini`, etc. và file `routing_rules.yaml`.

## 5. Risk Matrix & Error-Prone Areas (Classified)
- **Configuration Complexity (Rủi ro cao):** Sự kết hợp giữa `settings.toml` và `routing_rules.yaml` tạo ra một hệ thống rất mạnh mẽ nhưng cũng dễ cấu hình sai. Một rule sai trong YAML có thể dẫn đến việc định tuyến sai hoặc không có provider nào được chọn.
- **Latency (Rủi ro trung bình):** Logic fallback và retry có thể làm tăng độ trễ của request nếu các provider đầu chuỗi liên tục thất bại. Tuy nhiên, Circuit Breaker và Health-Aware Routing giúp giảm thiểu rủi ro này bằng cách nhanh chóng bỏ qua các provider không khỏe mạnh.
- **Inconsistent Provider Behavior (Rủi ro trung bình):** Mỗi provider có thể có các giới hạn, lỗi, và hành vi khác nhau. Lớp Adapter giúp chuẩn hóa điều này, nhưng nếu một provider thay đổi API của họ, Adapter tương ứng cần phải được cập nhật ngay lập tức.

## 6. Technical Debt (TODO / FIXME / HACK)
- **Load Balancer:** Hiện chỉ có `RoundRobinLoadBalancer` được triển khai và dường như chưa được tích hợp vào `ModelRouter`. `ModelRouter` chỉ thực hiện fallback tuần tự. Cần có cơ chế để thực sự cân bằng tải giữa các provider có cùng mức độ ưu tiên.
- **Hardcoded Logic in `ModelRouter`:** `ModelRouter` có một số logic (như ưu tiên provider theo yêu cầu client) có thể được tách ra thành một "policy" riêng để làm cho `ModelRouter` gọn gàng hơn.
- **Error Propagation:** Cần đảm bảo các thông tin lỗi chi tiết từ các provider được lan truyền một cách nhất quán qua các lớp exception để `RetryPolicy` và các tầng trên có thể ra quyết định chính xác.
