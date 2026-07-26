# Architecture Index

Đây là chỉ mục tra cứu nhanh các module chính trong dự án. Tài liệu chi tiết hơn có trong `PROJECT_STRUCTURE.md` và các file `FOLDER_INFO.md` tại từng module.

| Module | Trách nhiệm chính | Thành phần & Pattern chính | Phụ thuộc chính |
| :--- | :--- | :--- | :--- |
| **`gateway`** | API Gateway thông minh, là cổng vào cho mọi request. | Middleware Pipeline, Authentication, Rate Limiting, Routing, Circuit Breaker | `fastapi`, `provider`, `storage`, `event_bus` |
| **`provider`** | Giao tiếp với các dịch vụ LLM bên ngoài. | Adapter, Strategy, Factory, Resilience Policies (Retry, Fallback) | `httpx`, `pydantic` |
| **`storage`** | Lớp lưu trữ và truy cập dữ liệu (persistence layer). | Repository, Unit of Work, Driver-based Abstraction | `sqlalchemy`, `redis` |
| **`event_bus`** | Giao tiếp bất đồng bộ, đáng tin cậy giữa các service. | Pub/Sub, Mediator, Dependency Injection, Idempotency, DLQ | `asyncio` |
| **`context`** | Tải và lắp ráp ngữ cảnh (lịch sử, file...) cho một session. | Unit of Work, Repository, DTOs | `storage`, `schemas` |
| **`runtime`** | Quản lý các session agent có trạng thái, chạy dài hạn. | Actor Model, Event Sourcing, Distributed Lock, Consistent Hashing | `redis` |
| **`tool`** | Framework để định nghĩa và thực thi "công cụ" (function calling). | Strategy, Facade, Registry, Interpreter (for workflows) | `mcp` |
| **`schemas`** | Định nghĩa các "hợp đồng dữ liệu" (DTOs) cho toàn bộ ứng dụng. | Canonical Data Model, DTOs | `pydantic` |
| **`config`** | Quản lý cấu hình ứng dụng theo nhiều lớp. | Layered Config (YAML, .env, env vars), Proxy, Pydantic Schema | `pydantic`, `pyyaml` |
| **`agent`** | Quản lý việc định nghĩa và đăng ký các agent. | Registry | `schemas` |
