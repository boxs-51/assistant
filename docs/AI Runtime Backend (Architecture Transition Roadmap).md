# AI Runtime Backend: Architecture Transition Roadmap

---

## Tổng quan Lộ trình (Roadmap Overview)

Phase -1 : Project Modularization│Phase  0 : Architecture Freeze│Phase  1 : Runtime Foundation│Phase  2 : Connection Layer│Phase  3 : Execution Layer│Phase  4 : Capability Layer│Phase  5 : Context & Provider│Phase  6 : Workflow & Agent│Phase  7 : Gateway Simplification
---

## Phase -1 — Project Modularization

### Mục tiêu
Chuẩn bị codebase để việc refactor diễn ra an toàn. Đây không phải là thay đổi kiến trúc mà là bước hạ tầng bắt buộc trước khi tái cấu trúc:
* Chuẩn hóa cấu trúc thư mục (`runtime/`, `infrastructure/`, `application/`, `transport/`, `domain/`,...).
* Loại bỏ triệt để các phụ thuộc vòng (circular dependencies).
* Định nghĩa các interface và abstraction còn thiếu.
* Chuẩn hóa logging, configuration, dependency injection.
* Viết thêm integration test cho các luồng chat, tool và provider hiện tại.

> **Kết quả:** Codebase vẫn chạy như cũ nhưng đã "sẵn sàng" cho việc chuyển đổi. Các phase sau sẽ tập trung vào việc di chuyển trách nhiệm thay vì vừa sửa cấu trúc vừa tái thiết kế.

---

## Phase 0 — Architecture Freeze

### Mục tiêu
Không sửa code. Chỉ tập trung chuẩn hóa và chốt cố định toàn bộ tài liệu kiến trúc.

### Kết quả
Sinh ra bộ tài liệu chuẩn hóa:
1. Architecture Transition Document
2. Runtime Kernel Specification
3. Runtime API Specification
4. Capability Manifest Specification
5. Execution Model
6. Connection Protocol
7. Event Specification

> **Quy tắc:** Sau Phase 0, kiến trúc toàn hệ thống sẽ đóng đóng băng (freeze) và không thay đổi nữa.

---

## Phase 1 — Runtime Foundation

### Mục tiêu
Khởi tạo nền tảng **Runtime Kernel** — trái tim của AI Runtime Backend.

### Các công việc chính
Tạo cấu trúc Runtime Kernel:
runtime/└── kernel/├── kernel.py├── registry.py├── lifecycle.py├── bootstrap.py├── runtime.py├── context.py└── manifest.py
* **Chuẩn hóa Runtime Lifecycle:**
  $$\text{Runtime} \longrightarrow \text{initialize()} \longrightarrow \text{start()} \longrightarrow \text{stop()} \longrightarrow \text{dispose()}$$
* **Runtime Registry:** Thay thế hoàn toàn cơ chế truy cập `app.state.xxx` bằng `kernel.get_runtime()`.
* **Runtime Context:** Cấm sử dụng `request.app.state`, toàn bộ dữ liệu đi qua `RuntimeContext`.

> **Kết quả:** Gateway vẫn hoạt động bình thường, nhưng hệ thống có thêm Runtime Kernel chạy nền.

---

## Phase 2 — Connection Layer

### Mục tiêu
Tạo tầng quản lý kết nối và nhận diện Client (`Connection Runtime`).

### Các công việc chính
* **Tạo Connection Runtime:** Quản lý Client Registry, Transport, Heartbeat, Reconnect, và Routing.
* **Nhận diện Client:** Gateway phân biệt rõ các Client (Desktop A, Desktop B, CLI, Browser).
* **Đăng ký Capability:** Desktop chủ động đăng ký công cụ khi mở:
  ```json
  {
      "type": "tool",
      "name": "filesystem.search"
  }
$$\text{Desktop} \longrightarrow \text{Connection Runtime} \longrightarrow \text{Capability Runtime} \longrightarrow \text{Registry}$$Phase 3 — Execution LayerMục tiêuTách biệt trạng thái thực thi khỏi Session, biến các thao tác Chat thành các đợt thực thi cụ thể (Execution).Các công việc chínhThêm Execution Runtime: Quản lý Execution ID, Workflow ID, State, Current Node, Timeout, Cancellation, Retry.Chuyển đổi mô hình xử lý từ Chat Request thành Execution.Quản lý đa nhiệm trong một Session:$$\text{Session} \longrightarrow \{\text{Execution 1}, \text{Execution 2}, \text{Execution 3}\}$$Kết quả: Luồng Chat không còn ở dạng stateless mà được kiểm soát theo vòng đời Execution độc lập.Phase 4 — Capability LayerMục tiêuThay thế GatewayToolManager bằng Capability Runtime.Các công việc chínhTạo các thành phần: Capability Registry, Capability Session, Capability Dispatcher, Capability Driver.Định nghĩa các Drivers: Tool, Skill, Workflow, Plugin, Agent.Chuyển đổi mềm (Migration Adapter):$$\text{GatewayToolManager} \longrightarrow \text{Adapter} \longrightarrow \text{Capability Runtime}$$Kết quả: Router không còn nắm giữ hay biết chi tiết về Tool.Phase 5 — Context & ProviderMục tiêuDi chuyển Context Engine và Provider thành các Runtime độc lập.Các công việc chínhChuyển đổi Context Engine $\longrightarrow$ Context Runtime.Chuyển đổi Provider $\longrightarrow$ Provider Runtime.Luồng xử lý mới:$$\text{Execution} \longrightarrow \text{Context} \longrightarrow \text{Provider}$$Nguyên tắc phân tách: Context Runtime không biết về Tool; Provider Runtime không biết về Context.Phase 6 — Workflow & AgentMục tiêuXây dựng tầng điều phối nghiệp vụ nâng cao.Các công việc chínhWorkflow Runtime: Quản lý Execution Graph, Scheduler, Retry, Parallel, Loop.Agent Runtime: Quản lý Instruction, Role, Planner, Memory View.Kết quả: Luồng Chat chỉ còn đóng vai trò là một Workflow Definition.Phase 7 — Gateway SimplificationMục tiêuLoại bỏ toàn bộ code cũ/dư thừa tại Gateway, biến Gateway thành Transport Layer thuần túy.Các công việc chínhThu gọn Router: Chỉ còn gọi runtime.execute(chat_request).Thu gọn main.py: Chỉ còn nhiệm vụ khởi tạo và chạy Kernel:$$\text{Kernel} \longrightarrow \text{Load Runtime} \longrightarrow \text{Start}$$Xóa bỏ các Manager cũ đã qua Adapter: ToolManager, ContextManager, ProviderManager.Nguyên tắc Chuyển đổi (Backward Compatibility Rule)Để không làm đứt gãy hệ thống trong quá trình refactor, tất cả các Phase đều áp dụng cơ chế Adapter/Wrapper:Phase 4: GatewayToolManager $\longrightarrow$ Adapter $\longrightarrow$ Capability RuntimePhase 5: ContextEngine $\longrightarrow$ Context Runtime AdapterMọi module cũ vẫn hoạt động bình thường thông qua Adapter cho đến khi được xóa bỏ hoàn toàn ở Phase 7.Các Mốc Hoàn Thành (Milestones)MilestoneTên mốcMô tả trạng tháiM-1Codebase ReadyHoàn thành modularize, sẵn sàng cho refactorM0Frozen SpecsĐóng đóng băng bộ tài liệu kiến trúcM1Kernel RunningRuntime Kernel chạy thành côngM2Desktop RegisterClient Desktop đăng ký được với Connection RuntimeM3Execution RunningLuồng thực thi chuyển sang dạng ExecutionM4Capability DispatchCapability Runtime tiếp nhận điều phối ToolM5Context RuntimeContext & Provider chuyển đổi thành RuntimeM6Workflow RuntimeWorkflow Runtime điều phối toàn bộ nghiệp vụM7Transport GatewayGateway thu gọn hoàn toàn thành Transport LayerSo sánh Kiến trúc (Before vs. After)Kiến trúc ban đầu (As-Is)          FastAPI
             │
             ▼
          Router
             │
     Business Logic
             │
     Tool / Context / Provider
Kiến trúc mục tiêu (To-Be - Post Phase 7)              FastAPI
                 │
                 ▼
        Connection Adapter
                 │
                 ▼
           Runtime Kernel
                 │
  ┌──────────────┼──────────────┐
  ▼              ▼              ▼
Session      Execution      Connection
  │              │              │
  ▼              ▼              ▼
Workflow    Capability      Provider
  │              │              │
  └──────────────┼──────────────┘
                 ▼
           Event Runtime
                 │
                 ▼
         Storage / Infrastructure