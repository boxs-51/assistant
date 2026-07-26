# AI Runtime Backend: Runtime Kernel Specification
**Version:** 1.0

---

## 1. Mục tiêu

**Runtime Kernel** là trái tim của **AI Runtime Backend**. Nó chịu trách nhiệm quản lý toàn bộ các Runtime trong hệ thống.

* **Runtime Kernel không xử lý business logic.**
* **Runtime Kernel chỉ quản lý:**
  * Runtime Lifecycle
  * Runtime Registry
  * Runtime Context
  * Runtime Dependency
  * Runtime Event
  * Runtime Bootstrap
  * Runtime Discovery
  * Runtime Health

### Mô hình tương đương

Tương tự như Kiến trúc Hệ điều hành:

Operating System
│
▼
Kernel
│
┌─────┼────────┐
▼     ▼        ▼
Memory Process Driver

AI Runtime Backend được thiết kế tương tự:

AI Runtime Backend
│
▼
Runtime Kernel
│
┌─────┼────────────┐
▼     ▼            ▼
Session Context Capability

---

## 2. Design Goals

Runtime Kernel phải bảo đảm các tiêu chí:

* **Runtime độc lập:** Không Runtime nào biết sự tồn tại của Runtime khác.
* **Runtime có thể thay thế:** Ví dụ chuyển đổi `Provider Runtime V1` $\longrightarrow$ `Provider Runtime V2` mà Kernel không cần thay đổi.
* **Runtime có thể mở rộng:** Các Runtime mới (như `ROS Runtime`, `Docker Runtime`, `Desktop Runtime`) có thể được thêm vào mà không cần sửa đổi các Runtime hiện có.
* **Vòng đời rõ ràng:**
  $$\text{Created} \longrightarrow \text{Initialized} \longrightarrow \text{Started} \longrightarrow \text{Running} \longrightarrow \text{Stopping} \longrightarrow \text{Stopped} \longrightarrow \text{Disposed}$$

---

## 3. Runtime Definition

Mọi Runtime đều phải tuân theo cấu trúc chuẩn:

Runtime
│
├── Manifest
├── Lifecycle
├── Context
├── Event
├── Service
└── Health

### Runtime Manifest
Manifest dùng để mô tả thông tin Runtime:
* `id`
* `name`
* `version`
* `dependencies`
* `exports`
* `permissions`
* `metadata`

**Ví dụ:** `Provider Runtime` khai báo:
* `depends`: `Event Runtime`, `Storage Runtime`
* `exports`: `ProviderService`

Kernel sẽ đọc Manifest trước khi bắt đầu khởi động hệ thống.

---

## 4. Runtime Lifecycle

Tất cả các Runtime đều tuân theo một vòng đời đồng nhất:

Create ──> Initialize ──> Start ──> Running ──> Pause ──> Resume ──> Stop ──> Dispose

| Giai đoạn | Ý nghĩa |
| :--- | :--- |
| **Create** | Khởi tạo object |
| **Initialize** | Khởi tạo các tài nguyên (resources) |
| **Start** | Đăng ký (subscribe) các event |
| **Running** | Sẵn sàng nhận và xử lý request |
| **Pause** | Tạm ngừng hoạt động |
| **Resume** | Tiếp tục chạy sau khi tạm ngừng |
| **Stop** | Ngừng tiếp nhận request mới |
| **Dispose** | Giải phóng toàn bộ tài nguyên |

---

## 5. Runtime Context

Kernel sẽ cấp cho mỗi Runtime một `RuntimeContext`:

RuntimeContext
│
├── Kernel
├── Config
├── Logger
├── EventBus
├── Storage
├── Metrics
└── Clock


> **Quy tắc:** Runtime **không** tự lấy Config, **không** đọc Global Variable, **không** đọc `app.state`. Mọi thứ bắt buộc phải thông qua `RuntimeContext`.

---

## 6. Runtime Registry

Kernel quản lý toàn bộ các Runtime thông qua Registry:

Runtime Registry
│
├── Session Runtime
├── Capability Runtime
├── Provider Runtime
└── ...


* **Registry chỉ quản lý:** `runtime_id`, `manifest`, `state`, và `instance`.
* **Registry không quản lý:** Business logic.

---

## 7. Runtime Bootstrap

Tiến trình khởi động (Boot Process) của hệ thống:

Load Config ──> Discover Runtime ──> Read Manifest ──> Resolve Dependency ──> Create Runtime ──> Initialize ──> Start ──> Running


> **Tuyệt đối:** Không Runtime nào được tự ý khởi tạo một Runtime khác.

---

## 8. Dependency Resolution

Khi `Capability Runtime` khai báo phụ thuộc (`depends`) vào `Event Runtime` và `Storage Runtime`:

Kernel ──> Topological Sort ──> Init Order & Start Order


**Thứ tự khởi tạo ví dụ:**
$$\text{Storage} \longrightarrow \text{Event} \longrightarrow \text{Session} \longrightarrow \text{Capability} \longrightarrow \text{Provider} \longrightarrow \text{Workflow}$$

---

## 9. Runtime Communication

Hệ thống cung cấp 2 phương thức giao tiếp:

1. **Event Driven (Khuyến nghị cho Business):**
   $$\text{Runtime} \longrightarrow \text{Publish Event} \longrightarrow \text{Bus} \longrightarrow \text{Subscriber}$$
2. **Service Driven (Chỉ dùng cho Hạ tầng):**
   $$\text{Runtime} \longrightarrow \text{Kernel} \longrightarrow \text{Get Service}$$

Ví dụ các Infrastructure Service: `Metrics`, `Clock`, `Logger`. các Business Runtime **không được phép** gọi trực tiếp lẫn nhau qua Service.

---

## 10. Runtime Service

Runtime có thể xuất (Export) Service cho hạ tầng:
* `Provider Runtime` $\longrightarrow$ Export `ProviderService`
* Kernel thực hiện `registerService()`
* Runtime khác lấy qua `kernel.getService()`

> **Lưu ý:** Chỉ áp dụng cho Infrastructure, không dùng cho Business Logic.

---

## 11. Runtime Event

Mỗi Runtime chỉ đóng vai trò Publish Event ra hệ thống:

* **Provider Events:** `ProviderStarted`, `ProviderFinished`, `ProviderFailed`
* **Capability Events:** `CapabilityRequested`, `CapabilityCompleted`, `CapabilityFailed`
* **Session Events:** `SessionCreated`, `SnapshotSaved`, `ConversationUpdated`

---

## 12. Runtime Health

Kernel định kỳ kiểm tra trạng thái sức khỏe của các Runtime:

$$\text{Healthy} \longrightarrow \text{Warning} \longrightarrow \text{Degraded} \longrightarrow \text{Failed}$$

Nếu ở trạng thái **Failed**, Kernel sẽ thực thi chuỗi xử lý:

$$\text{Restart} \longrightarrow \text{Recover} \longrightarrow \text{Shutdown (nếu thất bại)}$$

---

## 13. Runtime Metrics

Kernel tự động thu thập các thông số hệ thống:
* CPU
* Memory
* Latency
* Queue
* Events
* Errors

> Các Runtime không tự đứng ra publish Metrics riêng lẻ.

---

## 14. Runtime Discovery

Kernel tự động tìm kiếm các Runtime trong thư mục dự án:

runtime/
├── provider/
├── session/
└── workflow/
└── manifest: runtime.yaml


**Luồng Discovery:**
$$\text{Kernel} \longrightarrow \text{Load} \longrightarrow \text{Validate} \longrightarrow \text{Register}$$

---

## 15. Runtime Security

Manifest phải khai báo rõ quyền hạn (`permissions`):
* `filesystem`
* `network`
* `provider`
* `terminal`

Kernel sẽ thực hiện kiểm tra quyền trước khi `Start` bất kỳ Runtime nào.

---

## 16. Runtime Recovery

Khi một Runtime gặp sự cố văng lỗi (Crash):

Runtime (Crash) ──> State: Failed ──> Kernel ──> Dispose ──> Recreate ──> Recover State ──> State: Running


> Nếu quá trình Recovery thất bại, hệ thống sẽ chuyển sang **Degraded Mode**.

---

## 17. Runtime Version

Manifest định nghĩa `Version` và mức độ tương thích `Compatibility`. Kernel sẽ từ chối tải bất kỳ Runtime nào không tương thích phiên bản.

---

## 18. Runtime Rules

### ❌ Các hành vi BỊ CẤM đối với Runtime:
* Gọi trực tiếp Runtime khác
* Tự ý khởi tạo Runtime khác
* Sửa đổi thông tin trong Registry
* Sử dụng Global State
* Đọc trực tiếp `app.state`
* Tự đọc cấu hình (Config) bên ngoài
* Tự khởi tạo `EventBus` riêng

### ✅ Các quy tắc BẮT BUỘC đối với Runtime:
* Phải Publish Event ra hệ thống
* Phải có cơ chế Health Check
* Phải cung cấp đầy đủ Manifest
* Tuân thủ đúng các trạng thái Lifecycle
* Tuyên bố rõ ràng các Dependency

---

## 19. Runtime State Machine

      +-------------+
      |   Created   |
      +------+------+ 
             |
             v
      +-------------+
      | Initialized |
      +------+------+ 
             |
             v
      +-------------+
      |   Started   |
      +------+------+ 
             |
             v
      +-------------+
      |   Running   |
      +------+------+ 
       |     |      |
       |     |      |
       v     |      v
   Paused    |   Failed
       |     |      |
       +-----+------+
             |
             v
      +-------------+
      |  Stopping   |
      +------+------+ 
             |
             v
      +-------------+
      |  Disposed   |
      +-------------+

---

## 20. Kernel Architecture

             Runtime Kernel
                   │
┌─────────────────────┼──────────────────────┐
│                     │                      │
▼                     ▼                      ▼
Lifecycle        Runtime Registry       Event Manager
│                     │                      │
▼                     ▼                      ▼
Dependency        Service Registry      Health Monitor
│                     │                      │
└─────────────────────┼──────────────────────┘
│
Runtime Context
│
┌───────────────┼─────────────────┐
▼               ▼                 ▼
Session Runtime  Provider Runtime  Capability Runtime
▼               ▼                 ▼
Workflow Runtime Agent Runtime Connection Runtime


---

## 21. Bổ sung: Execution Layer (Tầng Thực thi)

Để khắc phục thiếu sót về đối tượng đại diện cho một lần thực thi cụ thể trong hệ thống, **Execution Layer** được bổ sung nhằm tách biệt rõ ràng giữa **Năng lực / Vòng đời (Runtime)**, **Trạng thái (Session)**, **Cấu trúc luồng (Workflow)** và **Lần chạy thực tế (Execution)**.

### 21.1 Cấu trúc Execution Object

Execution
│
├── execution_id
├── session_id
├── workflow_id
├── current_node
├── current_state
├── context_snapshot
├── cancellation_token
├── timeout
├── retry_policy
└── execution_events


### 21.2 Luồng xử lý khi có Execution Layer

Chat Request
│
▼
Create Execution
│
▼
Workflow Runtime
│
▼
Capability Runtime
│
▼
Provider Runtime
│
▼
Complete Execution


### 21.3 Lợi ích của Execution Layer
1. **Đa nhiệm trong Session:** Một Session có thể quản lý và chạy đồng thời nhiều Execution độc lập.
2. **Kiểm soát linh hoạt:** Cho phép `Pause`, `Resume`, hoặc `Cancel` từng Execution cụ thể mà không gây ảnh hưởng đến toàn bộ Session.
3. **Giám sát & Replay:** Dễ dàng theo dõi, ghi log, và phát lại (replay) lịch sử theo từng đợt thực thi.
4. **Mở rộng tương lai:** Làm nền tảng vững chắc hỗ trợ các Long-running Agent, Background Workfl
