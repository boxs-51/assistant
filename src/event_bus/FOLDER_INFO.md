# Metadata
- **Last Scan:** 2026-07-26
- **Source Files:** 5
- **Hash:** N/A
- **Depends On:** `fastapi`, `structlog`, `sqlalchemy`, `..schemas`, `..context`, `..storage`
- **Scanned Files:** `bus.py`, `manager.py`, `registry.py`, `subscribers.py`, `ws_manager.py`

# 📂 Thư Mục: `event_bus`

## 1. Architecture Decisions & Design Patterns
- **Patterns:**
  - **Publish/Subscribe:** Là pattern kiến trúc cốt lõi của toàn bộ module.
  - **Mediator:** `EventBus` và `EventDispatcher` cùng nhau hoạt động như một Mediator, cho phép các thành phần giao tiếp mà không cần phụ thuộc trực tiếp vào nhau.
  - **Dependency Injection (DI):** `EventDispatcher` tự động "tiêm" các dependency (ví dụ: `SessionRepository`, `WebSocketManager`) vào các hàm xử lý sự kiện (handler) dựa trên type hint của chúng.
  - **Unit of Work (UoW):** Các handler có tương tác với database sẽ tự động được bao bọc trong một Unit of Work, đảm bảo tính toàn vẹn của giao dịch.
  - **Decorator:** Được sử dụng rộng rãi (`@registry.subscribe`) để đăng ký các handler một cách rõ ràng và khai báo.
- **Decisions:**
  - **Asynchronous First:** Toàn bộ hệ thống được xây dựng trên `asyncio`, giúp nó có khả năng mở rộng cao và không bị block.
  - **Reliability-Oriented:** Sự kết hợp của các cơ chế Retry, Dead Letter Queue (DLQ), Idempotency (chống lặp) và Session-level Locking làm cho hệ thống có độ tin cậy cao, phù hợp cho các tác vụ quan trọng.
  - **Decoupled & Extensible:** Các thành phần có thể "phát" sự kiện mà không cần biết ai đang lắng nghe. Chức năng mới có thể được thêm vào một cách dễ dàng bằng cách tạo một handler mới mà không cần sửa đổi code hiện có.
  - **Priority Queue:** Việc sử dụng `asyncio.PriorityQueue` cho phép các sự kiện quan trọng được ưu tiên xử lý trước.

## 2. Dependency & Ownership Graph
### Dependency
- `EventingManager` là "nhạc trưởng", khởi tạo và nắm giữ các instance của tất cả các thành phần khác.
- `EventDispatcher` phụ thuộc vào `EventRegistry`, hàng đợi của `EventBus`, cache driver và `uow_factory`.
- `subscribers.py` định nghĩa các hàm handler và đăng ký chúng vào một instance toàn cục của `EventRegistry`.

### Ownership & Lifetime
- `EventingManager` là một service có vòng đời dài.
- `EventDispatcher` chạy như một background task (`asyncio.Task`) trong suốt vòng đời của ứng dụng.
- Các sự kiện (`BaseEvent`) là các đối tượng có vòng đời ngắn, luân chuyển qua hệ thống.

## 3. Thread Model & Event/Data Flow
- **Thread Model:** Đơn luồng, bất đồng bộ (sử dụng event loop). Cơ chế khóa theo session (`session_lock`) được sử dụng để đảm bảo các sự kiện liên quan đến cùng một session được xử lý tuần tự, tránh race condition.
- **Data Flow:**
  1. Một thành phần gọi `bus.publish(event)`.
  2. Sự kiện được đưa vào `PriorityQueue`.
  3. Vòng lặp của `EventDispatcher` lấy sự kiện ra.
  4. Dispatcher kiểm tra idempotency (sự kiện đã được xử lý chưa) qua cache.
  5. Nó lấy một lock cho session (nếu cần).
  6. Nó tìm các handler tương ứng trong `EventRegistry`.
  7. Với mỗi handler, nó "tiêm" các dependency cần thiết và thực thi handler (bọc trong UoW nếu cần).
  8. Nếu thành công, nó đánh dấu sự kiện đã được xử lý. Nếu thất bại, nó thử lại, và cuối cùng đưa vào Dead Letter Queue.
  9. Nó giải phóng lock.

## 4. Public APIs & Configuration
- **Public API:** `event_bus.bus.publish(event)` là giao diện chính để các module khác sử dụng.
- **Configuration:** `EventingManager` được cấu hình khi khởi tạo với `storage_engine` và `context_engine`. `EventBus` được cấu hình với một `priority_map`.

## 5. Risk Matrix & Error-Prone Areas (Classified)
- **Memory:** Rủi ro thấp. Hàng đợi có thể phình to nếu các handler xử lý quá chậm, gây ra tồn đọng, nhưng đây là vấn đề về hiệu năng hơn là rò rỉ bộ nhớ.
- **Thread:** Rủi ro thấp. Cơ chế khóa theo session đã giải quyết tốt vấn đề race condition.
- **Complexity:** **Rủi ro cao.** Đây là một module rất mạnh mẽ nhưng cực kỳ phức tạp. Việc hiểu rõ sự tương tác giữa dispatcher, DI, UoW, và các cơ chế khóa/retry đòi hỏi kiến thức sâu về `asyncio` và các design pattern.
- **Performance:** Một dispatcher duy nhất xử lý các sự kiện một cách tuần tự. Nếu có quá nhiều sự kiện hoặc các handler chạy chậm, hàng đợi có thể bị tắc nghẽn. Với các hệ thống yêu cầu thông lượng cực lớn, có thể cần đến mô hình "worker pool" với nhiều dispatcher.

## 6. Technical Debt (TODO / FIXME / HACK)
- **TODO:** Trong `EventingManager`, driver database (`"sqlite"`) đang bị hardcode. Cần đưa vào cấu hình để có thể thay đổi dễ dàng.
- **TODO:** File `subscribers.py` có nguy cơ trở nên quá lớn. Một cơ chế tự động tìm kiếm các module subscriber trong các thư mục con sẽ giúp mã nguồn dễ quản lý hơn khi dự án phát triển.
- **TODO:** Cơ chế DI hiện tại khá đơn giản. Có thể cân nhắc sử dụng một framework DI hoàn chỉnh hơn nếu số lượng service cần tiêm vào tăng lên.
