# Metadata
- **Last Scan:** 2026-07-26
- **Source Files:** 1
- **Hash:** N/A
- **Depends On:** `sqlalchemy`, `structlog`, `..schemas`, `..storage`
- **Scanned Files:** `manager.py`

# 📂 Thư Mục: `context`

## 1. Architecture Decisions & Design Patterns
- **Patterns:**
  - **Unit of Work:** Sử dụng `uow_factory` để đảm bảo các thao tác với database trong một business transaction là nguyên tử (atomic).
  - **Repository:** Sử dụng các lớp Repository (`ProjectRepository`, `SessionRepository`) để trừu tượng hóa và tách biệt logic truy cập dữ liệu khỏi `ContextEngine`.
  - **Data Transfer Object (DTO):** Các Pydantic schema (`ContextObject`, `Session`) đóng vai trò là DTO, cung cấp một cấu trúc dữ liệu rõ ràng, đã được xác thực để trao đổi giữa lớp lưu trữ và lớp ứng dụng. `ContextObject` là DTO đầu ra chính của module này.
- **Decisions:**
  - **Separation of Concerns:** `ContextEngine` chỉ chịu trách nhiệm tải và lắp ráp ngữ cảnh. Nó không chứa business logic mà chỉ cung cấp dữ liệu cho các thành phần khác (như agent) hoạt động.
  - **Authorization-First Design:** Các kiểm tra quyền hạn (`identity.user_id`) được tích hợp chặt chẽ vào quá trình tải dữ liệu, đảm bảo an toàn.
  - **Performance Optimization:** Chủ động sử dụng `selectinload` để tối ưu hóa truy vấn database, tránh được vấn đề N+1 query.
  - **Asynchronous by Design:** Toàn bộ engine được xây dựng với `async`/`await`, phù hợp với môi trường có độ tương tranh cao.

## 2. Dependency & Ownership Graph
### Dependency
- `ContextEngine` phụ thuộc vào `StorageEngine`, `SqlAlchemyUnitOfWork`, và các lớp Repository.
- `ContextEngine` phụ thuộc nhiều vào các schema từ `..schemas`.

### Ownership & Lifetime
- `ContextEngine` là một service có trạng thái, được khởi tạo một lần khi ứng dụng bắt đầu và giữ tham chiếu đến `StorageEngine`.
- Trong mỗi phương thức, nó tạo ra và sở hữu các đối tượng có vòng đời ngắn như Unit of Work và Repository.
- Nó tạo ra `ContextObject` và chuyển quyền sở hữu cho bên gọi.

## 3. Thread Model & Event/Data Flow
- **Thread Model:** Hoàn toàn bất đồng bộ (`async`/`await`), các thao tác không bị chặn và phù hợp với event loop.
- **Data Flow (cho `load_context`):**
  1. `ContextEngine` nhận `session_id` và `identity`.
  2. Engine sử dụng Unit of Work và Repository để tải các model từ DB.
  3. Các model từ DB được xác thực và chuyển đổi thành Pydantic schema.
  4. Các schema được tập hợp lại thành một `ContextObject` duy nhất.
  5. `ContextObject` được trả về cho bên gọi.

## 4. Public APIs & Configuration
- **Public APIs:**
  - `ContextEngine.load_context(...)`
  - `ContextEngine.create_new_session(...)`
  - `ContextEngine.summarize_session(...)`
- **Configuration:** Engine được cấu hình một cách gián tiếp thông qua các đối tượng (`StorageEngine`, `uow_factory`) được tiêm vào khi khởi tạo (Dependency Injection).

## 5. Risk Matrix & Error-Prone Areas (Classified)
- **Memory:** Rủi ro thấp. Chỉ tải dữ liệu cho một session mỗi lần. Phương thức `summarize_session` có giới hạn 100 tin nhắn để tránh tải quá nhiều dữ liệu.
- **Thread:** Rủi ro thấp. Là một thành phần bất đồng bộ, nó dựa vào event loop để quản lý concurrency.
- **Exception:** Rủi ro thấp. Ném ra `ValueError` rõ ràng khi không tìm thấy dữ liệu hoặc bị từ chối quyền truy cập.
- **Performance:** Rủi ro trung bình. `summarize_session` thực hiện một cuộc gọi mạng bên ngoài đến LLM, có thể chậm hoặc thất bại. Điều này cần được xử lý ở tầng gọi (ví dụ: chạy dưới dạng background task).
- **Security:** Rủi ro thấp. Việc kiểm tra quyền hạn là bắt buộc và được thực hiện một cách nhất quán.

## 6. Technical Debt (TODO / FIXME / HACK)
- **TODO:** Trong `summarize_session`, tên model (`gpt-4o-mini`) đang bị hardcode. Giá trị này nên được lấy từ cấu hình để tăng tính linh hoạt.
- **TODO:** Prompt tóm tắt đang được viết bằng Tiếng Việt. Điều này nên được quản lý một cách mềm dẻo hơn (ví dụ: qua file template hoặc thư viện i18n) để hỗ trợ đa ngôn ngữ trong tương lai.
- Code trong module này khá sạch sẽ và tuân thủ các thông lệ tốt, không có nợ kỹ thuật nào khác đáng kể.
