# Metadata
- **Last Scan:** 2026-07-26
- **Source Files:** ~25+
- **Hash:** N/A
- **Depends On:** `fastapi`, `starlette`, `pydantic`, `sqlalchemy`, `structlog`, `opentelemetry`, `prometheus-client`, `authlib`, `passlib`, `python-jose`, `pyyaml`, `redis`
- **Scanned Files:** `circuit_breaker.py`, `authentication/*`, `router/*`, `middleware/*`, `limiter/*`, `fillter/*`

# 📂 Thư Mục: `gateway`

## 1. Architecture Decisions & Design Patterns
Module `gateway` là trái tim của ứng dụng, hoạt động như một API Gateway thông minh và có khả năng phục hồi lỗi cao, được xây dựng trên FastAPI. Nó xử lý tất cả các request đến, áp dụng một pipeline các bước xử lý, và điều phối các lệnh gọi đến các dịch vụ bên ngoài (LLM Providers).

- **Kiến trúc tổng thể (Architectural Style):**
  - **Microservices Gateway:** Đóng vai trò là cổng vào duy nhất cho tất cả các client, trừu tượng hóa các dịch vụ nội bộ và bên ngoài.
  - **Pipeline Processing / Chain of Responsibility:** Mỗi request đi qua một chuỗi các middleware và các bước xử lý một cách có thứ tự.
  - **Configuration-Driven:** Hầu hết các hành vi (rate limiting, circuit breaking, guardrails, auth providers) đều được điều khiển bởi cấu hình bên ngoài, giúp hệ thống rất linh hoạt.

- **Design Patterns chính:**
  - **Strategy:** Được sử dụng rộng rãi để chọn thuật toán (Rate Limiting), phương thức xác thực (`AuthenticationManager`), và nhà cung cấp LLM (`ProviderRouter`).
  - **Circuit Breaker:** Tích hợp sâu để bảo vệ hệ thống khỏi các lỗi từ dịch vụ bên ngoài (Redis, LLM providers), tăng cường khả năng phục hồi.
  - **Middleware:** Tận dụng hệ thống middleware của FastAPI/Starlette để áp dụng các logic xuyên suốt (cross-cutting concerns) như logging, metrics, và authentication.
  - **Dependency Injection:** Tận dụng triệt để hệ thống DI của FastAPI để cung cấp các service, manager, và dependency cho các endpoint một cách sạch sẽ.
  - **Facade:** `AuthenticationFacade` trong `router/auth.py` che giấu sự phức tạp của các service đăng ký, đăng nhập, OAuth...
  - **Factory:** `RateLimiterFactory`, `CircuitBreakerManager` hoạt động như các Factory để tạo và quản lý vòng đời của các đối tượng phức tạp.
  - **State:** `CircuitBreaker` là một state machine điển hình (Closed, Open, Half-Open).

## 2. Dependency & Ownership Graph
- **`main.py` (cấp ứng dụng)** sở hữu và khởi tạo các "Manager" chính: `EventingManager`, `StorageEngine`, `CircuitBreakerManager`, `RateLimiterManager`, etc. và đưa chúng vào `app.state`.
- **Module `router`** là người tiêu dùng chính, lấy các manager/service từ `app.state` thông qua DI để thực thi logic nghiệp vụ.
- **Module `middleware`** cũng truy cập `app.state` để lấy các manager cần thiết (ví dụ: `AuthenticationManager`).
- **`RateLimiterManager`** và `ProviderRouter` là client của `CircuitBreakerManager`.

## 3. Thread Model & Event/Data Flow
- **Thread Model:** Hoàn toàn bất đồng bộ (`asyncio`), đơn luồng, xử lý trên event loop. Các thao tác I/O (gọi DB, gọi provider, Redis) đều không block.
- **Luồng xử lý Request (`/v1/chat/completions`):**
  1.  **Middleware Pipeline (`middleware`):**
      - `observability_middleware`: Bắt đầu trace, ghi log, gán `request_id`.
      - `AuthenticationMiddleware`: Xác thực token, gán `Identity` vào `request.state`.
      - `SessionMiddleware`, `CORSMiddleware`: Xử lý session OAuth và CORS.
  2.  **Router Layer (`router/chat.py`):**
      - Endpoint nhận request, lấy `Identity` qua DI.
      - **(Song song)** Chạy `InputFillter` (`fillter`) để kiểm tra prompt injection VÀ `RateLimiterManager` (`limiter`) để kiểm tra giới hạn request.
      - **(Tuần tự)** Kiểm tra Semantic Cache (nếu có).
      - **Provider Routing (`provider/router.py`):**
          - Chọn provider tốt nhất dựa trên health (lấy từ `CircuitBreaker`), routing rules, và fallback logic.
          - Gọi đến provider, được bao bọc trong `CircuitBreaker` của provider đó.
      - **Tool-Use Loop:** Nếu provider yêu cầu gọi tool, gateway sẽ thực thi tool đó và gửi kết quả lại cho provider, lặp lại cho đến khi có câu trả lời cuối cùng.
      - **Output Sanitization (`fillter`):** Kết quả cuối cùng (hoặc stream) được làm sạch để loại bỏ thông tin nhạy cảm.
  3.  **Response:** Dữ liệu được trả về cho client. Middleware `observability` ghi log kết thúc và tính toán latency.

## 4. Public APIs & Configuration
- **Public APIs:** Được định nghĩa toàn bộ trong `src/gateway/router`. Các endpoint chính bao gồm:
  - `/v1/chat/completions`: Proxy chính đến các LLM.
  - `/auth/*`: Đăng ký, đăng nhập, OAuth, quản lý API key.
  - `/v1/models`, `/v1/files`: Proxy cho các API quản lý của provider.
  - `/v1/events/ws`: WebSocket để nhận sự kiện real-time.
  - `/admin/*`: Các endpoint quản trị được bảo vệ.
  - `/health`, `/ready`, `/metrics`: Các endpoint giám sát.

## 5. Risk Matrix & Error-Prone Areas (Classified)
- **Complexity (Rủi ro cao):** Gateway là module phức tạp nhất, với nhiều logic lồng nhau (DI, middleware, circuit breaker, tool-use loop). Việc debug và bảo trì đòi hỏi hiểu biết sâu về toàn bộ hệ thống.
- **Configuration (Rủi ro trung bình):** Hệ thống phụ thuộc rất nhiều vào cấu hình. Lỗi cấu hình (ví dụ: sai `failure_threshold` trong circuit breaker, sai rule trong guardrail) có thể gây ra hành vi không mong muốn.
- **Performance (Rủi ro trung bình):** Mọi request đều đi qua pipeline này. Bất kỳ thành phần nào trong pipeline bị chậm (ví dụ: một middleware xử lý lâu) sẽ ảnh hưởng đến toàn bộ hệ thống. Tuy nhiên, việc sử dụng `asyncio` và các kỹ thuật tối ưu (Lua script cho rate limiting) đã giảm thiểu rủi ro này.
- **Security (Rủi ro trung bình):** Là lớp ngoài cùng, đây là mục tiêu chính của các cuộc tấn công. Sự an toàn phụ thuộc vào việc triển khai chính xác của `authentication`, `limiter`, và `fillter`.

## 6. Technical Debt (TODO / FIXME / HACK)
- **Hardcoded values:** Một số giá trị như `PUBLIC_PATHS` trong `middleware/factory.py` hay `SESSION_SECRET_KEY` đang bị hardcode. Chúng nên được chuyển vào file cấu hình.
- **Session Management (Commented Out):** Logic quản lý session trong `router/chat.py` đang bị vô hiệu hóa. Khi được kích hoạt, cần đảm bảo nó hoạt động hiệu quả và không trở thành một điểm nghẽn.
- **Modular Routers:** File `router/auth.py` và `router/chat.py` đang rất lớn. Có thể xem xét chia nhỏ chúng thành các file con để dễ quản lý hơn (ví dụ: `auth/oauth.py`, `auth/api_keys.py`).
- **DI cho `auth.py`:** Cách lấy dependency trong `router/auth.py` (ví dụ: `get_auth_facade`) hơi cồng kềnh, phải khởi tạo lại các service con. Có thể tối ưu hóa bằng một DI container tập trung hơn.
