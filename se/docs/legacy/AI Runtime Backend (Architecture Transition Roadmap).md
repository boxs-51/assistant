# AI Runtime Backend: Architecture Transition Roadmap
**Version:** 1.0

---

## 1. Tổng quan lộ trình (Roadmap Overview)

Lộ trình chuyển đổi được chia làm 9 giai đoạn (từ **Phase -1** đến **Phase 7**), tuân thủ nghiêm ngặt nguyên tắc **"Không phá vỡ hệ thống" (Zero-Downtime Refactoring)**. Các module cũ sẽ được bọc lại bằng lớp **Adapter** trước khi chuyển giao hoàn toàn trách nhiệm cho các **Runtime** mới.

Phase -1 : Project Modularization
│
Phase 0  : Architecture Freeze
│
Phase 1  : Runtime Foundation
│
Phase 2  : Connection Layer
│
Phase 3  : Execution Layer
│
Phase 4  : Capability Layer
│
Phase 5  : Context & Provider Layer
│
Phase 6  : Workflow & Agent Layer
│
Phase 7  : Gateway Simplification

---

## 2. Chi tiết các giai đoạn (Phase Details)

### Phase -1 — Project Modularization
> **Mục tiêu:** Chuẩn bị codebase để việc refactor diễn ra an toàn. Đây không phải là thay đổi kiến trúc mà là tái cấu trúc kỹ thuật hạ tầng.

* **Nhiệm vụ chính:**
  * Chuẩn hóa cấu trúc thư mục (`runtime/`, `infrastructure/`, `application/`, `transport/`, `domain/`,...).
  * Phát hiện và loại bỏ triệt để các phụ thuộc vòng (circular dependencies).
  * Định nghĩa các Interface và Abstraction layer còn thiếu.
  * Chuẩn hóa Logging, Configuration, và Dependency Injection.
  * Bổ sung Integration Tests cho các luồng hiện tại: Chat, Tool, và Provider.
* **Kết quả:** Codebase hoạt động như cũ nhưng sạch sẽ, độc lập và sẵn sàng cho quá trình chuyển đổi.

---

### Phase 0 — Architecture Freeze
> **Mục tiêu:** Đóng băng thiết kế kiến trúc. Không sửa code, chỉ tập trung chuẩn hóa tài liệu kỹ thuật.

* **Các tài liệu kết quả:**
  1. *Architecture Transition Document*
  2. *Runtime Kernel Specification*
  3. *Runtime API Specification*
  4. *Capability Manifest Specification*
  5. *Execution Model*
  6. *Connection Protocol*
  7. *Event Specification*
* **Lưu ý:** Sau khi kết thúc Phase 0, toàn bộ kiến trúc hệ thống sẽ không bị thay đổi trong suốt quá trình triển khai.

---

### Phase 1 — Runtime Foundation
> **Mục tiêu:** Xây dựng trái tim của hệ thống — **Runtime Kernel**.

* **Nhiệm vụ chính:**
  * Tạo cấu trúc gói `runtime/kernel/`:
    ```text
    runtime/
    └── kernel/
        ├── kernel.py
        ├── registry.py
        ├── lifecycle.py
        ├── bootstrap.py
        ├── runtime.py
        ├── context.py
        └── manifest.py
    ```
  * Định nghĩa giao diện chuẩn cho Vòng đời Runtime:
    $$\text{Runtime} \longrightarrow \text{initialize()} \longrightarrow \text{start()} \longrightarrow \text{stop()} \longrightarrow \text{dispose()}$$
  * Thay thế kiểu truy cập `app.state.xxx` bằng `kernel.get_runtime()`.
  * Ép buộc mọi truy cập dữ liệu qua `RuntimeContext` thay vì `request.app.state`.
* **Kết quả:** Gateway cũ vẫn vận hành bình thường, đồng thời có thêm Runtime Kernel chạy nền.
* **Milestone M1:** `Kernel Running`

---

### Phase 2 — Connection Layer
> **Mục tiêu:** Xây dựng **Connection Runtime** để quản lý đa nền tảng Client (Desktop, CLI, Browser).

* **Nhiệm vụ chính:**
  * Khởi tạo các thành phần kết nối: `Client Registry`, `Transport`, `Heartbeat`, `Reconnect`, và `Routing`.
  * Xây dựng cơ chế đăng ký và giữ kết nối cho Client (ví dụ: ứng dụng Desktop mở kết nối $\rightarrow$ gửi `Register` $\rightarrow$ gửi `Heartbeat`).
  * Thực hiện luồng đăng ký năng lực (Capability Registration):
    $$\text{Desktop} \xrightarrow{\text{filesystem.search}} \text{Connection Runtime} \longrightarrow \text{Capability Runtime} \longrightarrow \text{Registry}$$
* **Kết quả:** Gateway chủ động định danh và quản lý danh sách Client kết nối.
* **Milestone M2:** `Desktop Register`

---

### Phase 3 — Execution Layer
> **Mục tiêu:** Chuyển đổi mô hình xử lý request từ Stateless sang **Execution-centric**.

* **Nhiệm vụ chính:**
  * Thêm **Execution Runtime**.
  * Quản lý thông tin từng lượt chạy: `Execution ID`, `Workflow ID`, `State`, `Current Node`, `Timeout`, `Cancellation`, `Retry`.
  * Thay thế mô hình `Chat Request` thành các đối tượng `Execution`.
  * Cho phép một Session quản lý đồng thời nhiều lượt thực thi:
    $$\text{Session} \longrightarrow \{\text{Execution 1}, \text{Execution 2}, \text{Execution 3}\}$$
* **Kết quả:** Luồng Chat có thể pause/resume, cancel hoặc retry độc lập.
* **Milestone M3:** `Execution Running`

---

### Phase 4 — Capability Layer
> **Mục tiêu:** Thay thế `GatewayToolManager` bằng **Capability Runtime**.

* **Nhiệm vụ chính:**
  * Xây dựng `Capability Registry`, `Capability Session`, `Capability Dispatcher`, và `Capability Driver`.
  * Định nghĩa các loại Driver: `Tool`, `Skill`, `Workflow`, `Plugin`, `Agent`.
  * Bọc `GatewayToolManager` bằng một Adapter kết nối tới `Capability Runtime`.
* **Kết quả:** Router không còn trực tiếp nắm giữ hay điều phối Tool.
* **Milestone M4:** `Capability Dispatch`

---

### Phase 5 — Context & Provider Layer
> **Mục tiêu:** Đưa các module xử lý ngữ cảnh và mô hình về dạng Runtime độc lập.

* **Nhiệm vụ chính:**
  * Chuyển đổi `Context Engine` $\longrightarrow$ `Context Runtime` (Bọc bởi `Context Runtime Adapter`).
  * Chuyển đổi `Provider` $\longrightarrow$ `Provider Runtime`.
  * Cấu trúc luồng xử lý thực thi:
    $$\text{Execution} \longrightarrow \text{Context} \longrightarrow \text{Provider}$$
* **Quy tắc:** `Context Runtime` không biết đến Tool; `Provider Runtime` không biết đến Context.
* **Milestone M5:** `Context Runtime`

---

### Phase 6 — Workflow & Agent Layer
> **Mục tiêu:** Xây dựng lớp điều phối công việc và trí tuệ cho Agent.

* **Nhiệm vụ chính:**
  * **Workflow Runtime:** Quản lý `Execution Graph`, `Scheduler`, `Retry`, `Parallel`, `Loop`.
  * **Agent Runtime:** Quản lý `Instruction`, `Role`, `Planner`, `Memory View`.
* **Kết quả:** Luồng Chat thông thường chuyển thành một dạng **Workflow Definition**.
* **Milestone M6:** `Workflow Runtime`

---

### Phase 7 — Gateway Simplification
> **Mục tiêu:** Dọn dẹp code cũ, thu gọn Gateway thành một Transport Layer thuần túy.

* **Nhiệm vụ chính:**
  * Loại bỏ hoàn toàn: `ToolManager`, `ContextManager`, `ProviderManager`.
  * Rút gọn logic tại Router: Chỉ gọi `runtime.execute(chat_request)`.
  * Rút gọn `main.py`:
    $$\text{Kernel} \longrightarrow \text{Load Runtime} \longrightarrow \text{Start}$$
* **Milestone M7:** `Gateway becomes Transport`

---

## 3. Bản đồ Chuyển đổi Kiến trúc (Architecture Transformation)

### Kiến trúc ban đầu (As-Is)

    FastAPI
         │
         ▼
      Router
         │
 Business Logic
         │
 Tool / Context / Provider

 ### Kiến trúc đích (To-Be)

              FastAPI
                │
                ▼
        Connection Adapter
                │
                ▼
          Runtime Kernel
                │
┌───────────────┼────────────────┐
▼               ▼                ▼
Session      Execution      Connection
│               │                │
▼               ▼                ▼
Workflow     Capability      Provider
│               │                │
└───────────────┼────────────────┘
                ▼
          Event Runtime
                │
                ▼
      Storage / Infrastructure

---

## 4. Bảng tổng hợp Cột mốc (Milestones Summary)

| Cột mốc | Tên Milestone | Phase liên quan | Mô tả mục tiêu nghiệm thu |
| :---: | :--- | :---: | :--- |
| **M0** | Codebase Ready | Phase -1 & 0 | Hoàn thành Modularization, tích hợp Integration Test, đóng băng các tài liệu spec. |
| **M1** | Kernel Running | Phase 1 | Runtime Kernel khởi chạy thành công song song với hệ thống Gateway hiện tại. |
| **M2** | Desktop Register | Phase 2 | Client (Desktop) thực hiện kết nối, gửi Heartbeat và đăng ký Capability thành công. |
| **M3** | Execution Running | Phase 3 | Requests được đóng gói thành các Execution Object; hỗ trợ hủy/theo dõi tiến trình. |
| **M4** | Capability Dispatch | Phase 4 | Chuyển toàn bộ việc thực thi Tool sang Capability Runtime qua Adapter. |
| **M5** | Context Runtime | Phase 5 | Tách biệt hoàn toàn Context Engine và Provider thành hai Runtime riêng biệt. |
| **M6** | Workflow Runtime | Phase 6 | Điều phối toàn bộ bài toán Chat và Agent thông qua Workflow Execution Graph. |
| **M7** | Gateway as Transport | Phase 7 | Xóa bỏ hoàn toàn code thừa; Gateway chỉ còn đóng vai trò tiếp nhận và chuyển tiếp request. |
        