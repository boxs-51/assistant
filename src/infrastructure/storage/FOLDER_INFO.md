# Metadata
- **Last Scan:** 2026-07-26
- **Source Files:** ~25+
- **Hash:** N/A
- **Depends On:** `sqlalchemy`, `redis`, `alembic`, `chromadb-client` (implied)
- **Scanned Files:** All files in `src/storage`

# 📂 Thư Mục: `storage`

## 1. Architecture Decisions & Design Patterns
Module `storage` là một framework truy cập dữ liệu đa năng, được thiết kế theo các nguyên tắc của Clean Architecture và Domain-Driven Design. Nó cung cấp một lớp trừu tượng mạnh mẽ giữa logic nghiệp vụ của ứng dụng và các công nghệ lưu trữ cụ thể (SQL, NoSQL, Vector DB).

- **Kiến trúc tổng thể (Architectural Style):**
  - **Layered Architecture (Kiến trúc phân lớp):** Module được chia thành các lớp có trách nhiệm rõ ràng: Interfaces (Hợp đồng), Drivers (Triển khai), Core (Điều phối), và Repositories (Truy cập dữ liệu).
  - **Dependency Inversion Principle:** Các lớp cấp cao (ví dụ: `UnitOfWork`) phụ thuộc vào các `interface` trừu tượng (`DatabaseDriver`), không phải các `driver` triển khai cụ thể (`SQLiteDriver`). Điều này giúp hệ thống linh hoạt và dễ thay thế công nghệ.

- **Design Patterns chính:**
  - **Repository:** Mỗi repository (`UserRepository`, `ProjectRepository`) đóng gói logic truy cập dữ liệu cho một thực thể nghiệp vụ (Aggregate Root), che giấu các chi tiết về SQL hoặc ORM.
  - **Unit of Work (`unit_of_work.py`):** Đảm bảo tính toàn vẹn dữ liệu cho các thao tác trên CSDL quan hệ. `SqlAlchemyUnitOfWork` quản lý một `AsyncSession` duy nhất và chia sẻ nó cho tất cả các repository được tạo trong context của nó, đảm bảo tất cả các thay đổi được commit hoặc rollback cùng nhau như một giao dịch nguyên tử.
  - **Strategy/Bridge:** Các `driver` (`SQLiteDriver`, `RedisDriver`) là các "chiến lược" triển khai cụ thể cho một `interface`. `StorageEngine` có thể sử dụng bất kỳ driver nào tuân thủ interface.
  - **Facade:** `StorageEngine` (`manager.py`) hoạt động như một Facade, là điểm vào duy nhất để khởi tạo, kết nối và ngắt kết nối toàn bộ lớp storage.
  - **Event-Driven Integration (`events.py`):** `StorageEventFactory` tự động tạo ra các sự kiện (`storage.user.created`) từ các thay đổi trong Unit of Work. Điều này cho phép các hệ thống khác (ví dụ: `event_bus`) phản ứng với các thay đổi dữ liệu một cách tách biệt.

## 2. Dependency & Ownership Graph
- `StorageEngine` sở hữu `DriverRegistry` và `RepositoryRegistry`, quản lý vòng đời của tất cả các driver và các repository "phi giao dịch" (non-transactional).
- `SqlAlchemyUnitOfWork` khi được tạo ra sẽ sở hữu một `AsyncSession` và các instance của các repository "giao dịch" (transactional) trong suốt vòng đời của nó.
- Các `Repository` phụ thuộc vào `AsyncSession` (đối với SQL) hoặc một `CacheDriver` (đối với Redis), tuân thủ nguyên tắc Dependency Inversion.

## 3. Thread Model & Event/Data Flow
- **Thread Model:** Hoàn toàn bất đồng bộ (`asyncio`).
- **Luồng dữ liệu (Sử dụng Unit of Work):**
  1.  Một service hoặc use case cần thực hiện một giao dịch nghiệp vụ (ví dụ: tạo user và organization của họ).
  2.  Nó tạo một context `async with uow_factory() as uow:`.
  3.  Bên trong `with`, nó sử dụng các repository từ `uow` (ví dụ: `uow.users.create(...)`, `uow.organizations.create(...)`). Cả hai repository này đều dùng chung một `AsyncSession`.
  4.  Nó gọi `uow.commit()`.
  5.  Bên trong `commit()`:
      a. `StorageEventFactory` quét session để tìm các thay đổi (đối tượng mới, đã sửa đổi, đã xóa).
      b. `session.commit()` được gọi để lưu các thay đổi vào CSDL.
      c. Nếu commit thành công, các sự kiện đã tạo sẽ được publish ra `event_bus`.
  6.  Nếu có lỗi xảy ra trong `with` block, `uow.__aexit__` sẽ tự động gọi `rollback()`.

## 4. Public APIs & Configuration
- **Public API:**
  - `StorageEngine`: Dùng khi khởi động và tắt ứng dụng.
  - `uow_factory`: Một factory function/callable để tạo ra các `SqlAlchemyUnitOfWork` instance. Đây là API chính được sử dụng bởi các lớp service.
- **Configuration:** Được điều khiển bởi `settings.storage`, nơi định nghĩa các driver nào được bật và các thông tin kết nối của chúng.

## 5. Risk Matrix & Error-Prone Areas (Classified)
- **Complexity (Rủi ro trung bình):** Các pattern như Unit of Work và Dependency Inversion rất mạnh mẽ nhưng có thể khó hiểu đối với các nhà phát triển mới.
- **ORM Mismatch (Rủi ro thấp):** Có sự tách biệt giữa "entities" (dường như là Pydantic model) và "models" (SQLAlchemy model). Việc giữ chúng đồng bộ có thể cần sự chú ý.
- **Transaction Management (Rủi ro thấp):** Unit of Work đã xử lý rất tốt việc quản lý transaction, nhưng lập trình viên phải nhớ sử dụng nó cho tất cả các thao tác ghi/sửa/xóa CSDL quan hệ.

## 6. Technical Debt (TODO / FIXME / HACK)
- Một số file trong `core` (`dependency.py`, `exceptions.py`, `transaction.py`) đang trống, cho thấy framework có thể vẫn đang trong quá trình phát triển.
- Logic khởi tạo repository trong `StorageEngine._initialize_repositories` hiện chỉ đăng ký các repo non-SQL. Cần có một cơ chế rõ ràng hơn để các service biết chúng nên lấy repository từ đâu (từ `StorageEngine` hay từ `UnitOfWork`).
- Việc xử lý lỗi kết nối trong `RedisDriver` (comment `raise`) là một quyết định "fail-soft", cần được ghi lại tài liệu rõ ràng để tránh hành vi không mong muốn.
