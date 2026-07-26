# Metadata
- **Last Scan:** 2026-07-26
- **Source Files:** 6
- **Hash:** N/A
- **Depends On:** `redis`, `sqlalchemy` (implied), `structlog`
- **Scanned Files:** `bus.py`, `ingress.py`, `kernel.py`, `lock.py`, `router.py`, `store.py`

# 📂 Thư Mục: `runtime`

## 1. Architecture Decisions & Design Patterns
Module `runtime` định nghĩa một nền tảng thực thi cho các session có trạng thái, chạy dài hạn và có khả năng mở rộng. Kiến trúc này khác biệt cơ bản so với mô hình request-response không trạng thái của `gateway` và hướng tới việc xây dựng các agent phức tạp.

- **Kiến trúc tổng thể (Architectural Style):**
  - **Actor Model:** Nền tảng của module, mỗi `SessionActor` là một "actor" độc lập, có trạng thái và hành vi riêng, giao tiếp qua message bất đồng bộ.
  - **Event Sourcing:** Tất cả các thay đổi trạng thái đều được ghi lại dưới dạng một chuỗi các sự kiện (`RuntimeEvent`) không thể thay đổi vào `EventStore`. Đây là nguồn chân lý (source of truth) của hệ thống.
  - **Distributed Systems:** Các thành phần như `DistributedSessionLock` và `SessionRouter` được thiết kế để hệ thống có thể chạy trên nhiều server (multi-instance) một cách an toàn.

- **Design Patterns chính:**
  - **Actor Model (`kernel.py`):** `SessionActor` đóng gói trạng thái và xử lý tuần tự các command trong một hàng đợi riêng, đảm bảo không có race condition trong nội bộ một session.
  - **Event Sourcing (`store.py`):** `EventStore` cung cấp một kho lưu trữ append-only cho các sự kiện, cho phép tái tạo trạng thái và gỡ lỗi.
  - **Distributed Lock (`lock.py`):** Sử dụng Redis `SETNX` để đảm bảo chỉ một instance của `SessionActor` được kích hoạt cho một session tại một thời điểm, chống lại hiện tượng "split-brain".
  - **Consistent Hashing (`router.py`):** `SessionRouter` sử dụng hashing nhất quán để ánh xạ một `session_id` tới một server instance cụ thể. Điều này giúp tối ưu việc định tuyến và duy trì `session affinity` khi scale hệ thống.
  - **Command/Event:** Hệ thống phân biệt rõ ràng giữa `RuntimeCommand` (chỉ thị một hành động) và `RuntimeEvent` (ghi nhận một điều đã xảy ra).

## 2. Dependency & Ownership Graph
- Một "Runtime Manager" (không được định nghĩa ở đây, nhưng có thể suy ra) sẽ chịu trách nhiệm quản lý vòng đời của các `SessionActor`.
- `SessionActor` sở hữu trạng thái, hàng đợi command, và một `DistributedSessionLock`.
- Các `SessionActor` là client của `InternalEventBus` (để publish event) và `EventStore` (được gọi bởi một subscriber của bus).
- `IngressRuntime` là cổng vào, nhận request và tạo ra `RuntimeCommand`.

## 3. Thread Model & Event/Data Flow
- **Thread Model:** Hoàn toàn bất đồng bộ (`asyncio`).
- **Luồng dữ liệu (dự kiến):**
  1.  Một request (từ WebSocket, webhook...) đến gateway.
  2.  `SessionRouter` xác định server instance nào chịu trách nhiệm cho `session_id` này. Request được chuyển đến instance đó.
  3.  `IngressRuntime` của instance đó xử lý request, kiểm tra idempotency, và tạo ra `RuntimeCommand`.
  4.  Command được gửi đến `SessionActor` tương ứng. Nếu actor chưa chạy, hệ thống sẽ cố gắng khởi động nó bằng cách chiếm `DistributedSessionLock`.
  5.  `SessionActor` xử lý command, thay đổi trạng thái nội bộ.
  6.  `SessionActor` tạo ra một `RuntimeEvent` để ghi nhận sự thay đổi.
  7.  Event này được lưu vào `EventStore` và được publish lên `InternalEventBus`.
  8.  Các dịch vụ khác (ví dụ: broadcaster cho WebSocket) lắng nghe bus và phản ứng với event.

## 4. Public APIs & Configuration
- **API:** "API" của module này là tập hợp các `RuntimeCommand` mà nó có thể xử lý. `IngressRuntime` là điểm vào chính.
- **Configuration:** Cấu hình chủ yếu thông qua các dependency được inject vào, như Redis driver và Database driver.

## 5. Risk Matrix & Error-Prone Areas (Classified)
- **Complexity (Rủi ro cao):** Kiến trúc Actor Model và Event Sourcing cực kỳ mạnh mẽ nhưng rất phức tạp để triển khai, gỡ lỗi và duy trì.
- **Distributed State (Rủi ro cao):** Việc quản lý lock và định tuyến trong môi trường phân tán đòi hỏi sự ổn định tuyệt đối của Redis và mạng. Lỗi trong cơ chế lock có thể dẫn đến "split-brain", gây mất mát hoặc hỏng hóc dữ liệu trạng thái.
- **Event Schema Evolution (Rủi ro trung bình):** Khi cấu trúc của một `RuntimeEvent` thay đổi, việc tái hiện trạng thái từ các event cũ trong `EventStore` có thể trở nên khó khăn, đòi hỏi các chiến lược versioning cho schema.

## 6. Technical Debt (TODO / FIXME / HACK)
- `IngressRuntime` sử dụng một `dict` đơn giản cho `idempotency_store`. Trong thực tế, đây phải là một cache phân tán như Redis.
- Module này có vẻ là một nền tảng được thiết kế cho tương lai nhưng chưa được tích hợp đầy đủ vào luồng xử lý chính của `gateway`. Logic trong `gateway` chưa thấy gọi đến `runtime`.
- Hệ thống có hai Event Bus (`src/event_bus` và `src/runtime/bus.py`). Cần có một kiến trúc rõ ràng về cách chúng tương tác với nhau hoặc hợp nhất chúng.
