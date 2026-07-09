# Kế hoạch Xây dựng Storage Engine cho AI Gateway

**Ngày tạo:** 08/07/2024
**Người soạn:** Gemini Code Assist
**Mục tiêu:** Xây dựng một "Storage Engine" - nền tảng lưu trữ cấp cao, thống nhất, linh hoạt và có khả năng mở rộng để phục vụ cho tất cả các module của AI Gateway.

---

## 1. Tổng quan và Mục tiêu

### 1.1. Vấn đề hiện tại

Khi quy mô dự án lớn dần với các yêu cầu phức tạp như `Authentication`, `Billing`, `File Service`, `Agent`, và `Semantic Caching`, một kiến trúc lưu trữ đơn giản sẽ bộc lộ nhiều nhược điểm:

*   **Thiếu thống nhất:** Mỗi module có thể kết nối đến database/cache theo một cách khác nhau.
*   **Khó mở rộng:** Việc thêm một loại hình lưu trữ mới (ví dụ: từ SQLite sang PostgreSQL, hoặc thêm Vector DB) đòi hỏi phải sửa đổi code ở nhiều nơi.
*   **Lặp code:** Logic kết nối, xử lý lỗi, và truy vấn có thể bị lặp lại giữa các module.
*   **Phụ thuộc cứng:** Business logic (ví dụ: `Authentication`) bị phụ thuộc trực tiếp vào loại database cụ thể (ví dụ: Redis, PostgreSQL), làm giảm tính linh hoạt và khả năng testing.

Một AI Gateway hiện đại không chỉ cần "database" mà là một hệ sinh thái lưu trữ đa dạng:
*   **Relational/Document Storage:** PostgreSQL, SQLite, MongoDB (lưu thông tin user, key, billing...).
*   **Cache Storage:** Redis (lưu session, rate limiting, cache tạm thời).
*   **Object Storage:** MinIO, S3 (lưu file upload, tài liệu cho Agent).
*   **Vector Storage:** ChromaDB, Qdrant (phục vụ Semantic Cache, RAG).
*   **Event Storage:** Kafka, RabbitMQ (cho kiến trúc hướng sự kiện trong tương lai).

### 1.2. Mục tiêu của Storage Engine

Chúng ta sẽ xây dựng một **Storage Engine** thực thụ, không chỉ là một framework, với các mục tiêu sau:

1.  **Trừu tượng hóa Backend:** Che giấu chi tiết triển khai của các hệ thống lưu trữ khác nhau.
2.  **API Thống nhất:** Cung cấp một giao diện nhất quán (`Repository Pattern`) để các module business logic tương tác với dữ liệu.
3.  **Linh hoạt (Plug-and-Play):** Dễ dàng thêm, bớt hoặc thay đổi các `Driver` lưu trữ (PostgreSQL, Redis, MinIO...) thông qua cấu hình mà không cần thay đổi code business.
4.  **Tách biệt Trách nhiệm (SoC):** Phân tách rõ ràng giữa `Core` (điều phối), `Interfaces` (hợp đồng), `Drivers` (thực thi), và `Repositories` (truy cập dữ liệu).
5.  **Hỗ trợ Giao tác (Transaction):** Cung cấp cơ chế `Unit of Work` hoặc `Transaction Manager` để đảm bảo tính toàn vẹn dữ liệu cho các hoạt động phức tạp.
6.  **Domain-Centric:** `Entities` (mô hình miền) là trung tâm, được dùng chung bởi toàn bộ ứng dụng, không bị "sở hữu" bởi lớp lưu trữ.

## 2. Thiết kế Kiến trúc (Storage Engine)

### 2.1. Cấu trúc Thư mục

Kiến trúc được tổ chức theo chức năng, không theo loại lưu trữ, để tối đa hóa sự linh hoạt và giảm thiểu sự phụ thuộc chéo.

```
gateway/
│
├── core/                       # Core của toàn bộ Gateway
│   ├── entities/               # Domain models (Pydantic), dùng chung toàn ứng dụng
│   └── schemas/                # Schemas cho API request/response
│
└── storage/                    # Storage Engine
    │
    ├── core/                   # Phần lõi điều phối của Engine
    │   ├── manager.py          # StorageEngine: Điểm truy cập chính, điều phối
    │   ├── registry.py         # DriverRegistry, RepositoryRegistry
    │   ├── transaction.py      # Unit of Work / Transaction Manager
    │   ├── exceptions.py       # Các exception tùy chỉnh
    │   └── dependency.py       # FastAPI dependency injectors
    │
    ├── interfaces/             # Định nghĩa các "hợp đồng" (Abstract Base Classes)
    │   ├── database.py         # DatabaseDriver interface
    │   ├── cache.py            # CacheDriver interface
    │   ├── vector.py           # VectorStorageDriver interface
    │   ├── object.py           # ObjectStorageDriver interface
    │   └── repository.py       # BaseRepository interface
    │
    ├── drivers/                # Các implementation cụ thể cho từng loại storage
    │   ├── sqlite/
    │   ├── postgres/
    │   ├── redis/
    │   └── ...
    │
    ├── repositories/           # Lớp truy cập dữ liệu, sử dụng các Driver
    │   ├── users.py
    │   ├── api_keys.py
    │   └── ...
    │
    ├── migrations/             # Quản lý schema CSDL (Alembic)
    ├── models/                 # Các model của ORM (SQLAlchemy)
    └── utils/                  # Các hàm tiện ích
```

### 2.2. Luồng hoạt động

1.  **Khởi động (Startup):**
    *   `base_gateway.py` khởi tạo `StorageEngine` với cấu hình từ `settings`.
    *   `StorageEngine` khởi tạo `DriverRegistry`.
    *   `DriverRegistry` đọc config, tìm và đăng ký các `Driver` cần thiết (ví dụ: `SQLiteDriver`, `RedisDriver`).
    *   `StorageEngine` gọi `connect()` trên `DriverRegistry`, lần lượt kết nối tất cả các driver đã đăng ký.
    *   `StorageEngine` khởi tạo `RepositoryRegistry`, inject các `Driver` cần thiết vào từng `Repository` và đăng ký chúng.
    *   Lưu instance của `StorageEngine` vào `app.state.storage`.

2.  **Trong quá trình xử lý Request:**
    *   Một service (ví dụ: `AuthenticationService`) nhận các `Repository` cần thiết thông qua dependency injection.
    *   **Ví dụ:** `def handle_login(user_repo: UserRepository = Depends(get_user_repository))`
    *   Service gọi các phương thức của repository (ví dụ: `await user_repo.get_by_email(...)`).
    *   Repository sử dụng các `Driver` đã được inject để thực thi logic truy vấn. Một repository có thể dùng nhiều driver (ví dụ: lấy dữ liệu chính từ SQL, kiểm tra cache trên Redis).

3.  **Tắt (Shutdown):**
    *   `base_gateway.py` gọi `await app.state.storage.disconnect()`.
    *   `StorageEngine` gọi `disconnect()` trên `DriverRegistry` để đóng tất cả các kết nối một cách an toàn.

## 3. Lộ trình Phát triển (Từng bước)

**Chapter 1: Nền tảng Storage Engine (Tuần 1)**
1.  **Tái cấu trúc thư mục:** Dựng lại toàn bộ cây thư mục theo kiến trúc `Storage Engine`.
2.  **Di chuyển và định nghĩa Entities:** Chuyển `User`, `Organization`, `APIKey` vào `gateway/core/entities/`.
3.  **Định nghĩa Interfaces:** Tạo các file `database.py`, `cache.py`... trong `gateway/storage/interfaces/` với các lớp trừu tượng (ABC).
4.  **Xây dựng Core Engine:**
    *   Triển khai `StorageEngine`, `DriverRegistry`, `RepositoryRegistry` trong `gateway/storage/core/`.
    *   Tích hợp `StorageEngine` vào `startup_event` và `shutdown_event` của `base_gateway.py`.
5.  **Triển khai Driver đầu tiên:**
    *   Tạo `SQLiteDriver` và `RedisDriver` tuân thủ theo `DatabaseDriver` và `CacheDriver` interface.
    *   Cấu hình SQLAlchemy ORM và Alembic trong `storage/models` và `storage/migrations`.

**Chapter 2: Repositories và Tích hợp Module đầu tiên (Tuần 2)**
1.  **Xây dựng `UserRepository` và `APIKeyRepository`:**
    *   Triển khai các phương thức CRUD cơ bản (create, get, update, delete).
    *   Sử dụng `DatabaseDriver` và `CacheDriver` đã được inject.
2.  **Refactor `Authentication`:**
    *   Xây dựng `AuthenticationService` sử dụng `UserRepository` và `APIKeyRepository` thông qua dependency injection.
    *   Loại bỏ hoàn toàn logic truy cập storage trực tiếp khỏi các endpoint.
3.  **Xây dựng `SessionRepository`:**
    *   Sử dụng `RedisDriver` để quản lý session.
    *   Tích hợp vào `AuthenticationService`.

**Chapter 3: Hoàn thiện và Mở rộng (Tuần 3-4)**
1.  **Thêm `PostgresDriver`:** Chứng minh tính linh hoạt bằng cách chuyển đổi từ SQLite sang PostgreSQL chỉ bằng cách thay đổi config.
2.  **Refactor các Module hiện có:**
    *   `RateLimiterManager` sẽ sử dụng `CacheDriver` từ `StorageEngine`.
    *   `SemanticCache` sẽ sử dụng `VectorStorageDriver` và `CacheDriver`.
3.  **Triển khai các Driver mới:**
    *   `MinIO` (Object Storage), `ChromaDB` (Vector Storage).
4.  **Xây dựng các Repository mới:**
    *   `FileRepository` (sử dụng `DatabaseDriver` và `ObjectStorageDriver`).
    *   `MetricsRepository`, `BillingRepository`.

**Chapter 4: Các Tính năng Nâng cao (Tuần 5+)**
1.  **Triển khai `UnitOfWork`:** Xây dựng `TransactionManager` để xử lý các giao tác phức tạp trên nhiều repository.
2.  **Tích hợp `Agent`:** Các thành phần của Agent (`Memory`, `MessageStore`) sẽ là các client lý tưởng, chỉ tương tác với các `Repository` (`MessageRepository`, `FileRepository`...) mà `StorageEngine` cung cấp.
3.  **Hoàn thiện tài liệu:** Viết tài liệu hướng dẫn cách thêm một `Driver` mới, một `Repository` mới và cách sử dụng `StorageEngine` trong một module mới.

---
