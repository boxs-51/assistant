---
name: architecture-validation
description: Kiểm chứng tính đúng đắn và độ đồng bộ 100% giữa mã nguồn thực tế (Codebase) và hệ thống tài liệu kiến trúc (.md) sau khi thực hiện Discovery, Repair hoặc Refactor. Đảm bảo không còn Documentation Drift.
---

### 📋 Quy Trình Kiểm Chứng 4 Bước (Validation Pipeline)Khi được gọi (hoặc tự động kích hoạt ở bước cuối của Orchestrator):
```text
       ┌──────────────────────────────────────────┐
       │     1. Structural Rescan & Hashing       │
       └────────────────────┬─────────────────────┘
                            │
                            ▼
       ┌──────────────────────────────────────────┐
       │    2. Multi-Axis Consistency Matrix      │
       └────────────────────┬─────────────────────┘
                            │
                            ▼
       ┌──────────────────────────────────────────┐
       │       3. Pass/Fail Decision Engine       │
       └──────────────┬─────────────────┬─────────┘
                      │                 │
                 [ PASS ]           [ FAIL ]
                      │                 │
                      ▼                 ▼
             Update Meta & Hash   Trigger Re-Repair
             Mark Version PASS    (Max 2 Loops)
```
---

### BƯỚC 1: Re-scan Cục Bộ & Tính Hash Mới (Structural Rescan)

- Quét lại toàn bộ mã nguồn của module vừa cập nhật (.cpp, .h).

- Tính toán lại Hash nội dung hiện tại và so sánh với Hash trong DEPENDENCY_CACHE.md hoặc Metadata của FOLDER_INFO.md.

### BƯỚC 2: Kiểm Tra Đối Chiếu 5 Trục Kiến Trúc (Consistency Matrix)
Sử dụng ma trận đối chiếu 5 trục giữa Code Thực Tế và File .md:Trục Kiểm Tra (Axis)Mã Nguồn Thực Tế (Source Code)Tài Liệu Architecture (.md)Trạng Thái (Match Status)
- 1. **OwnershipWindowResource** chứa std::unique_ptr<Renderer>Ghi nhận WindowResource owns Renderer✅ MATCH / ❌ DRIFT
- 2. **Symbol DB** Hàm `InitContext()` có trong `WindowRuntime.h` Đã xuất hiện trong `SYMBOL_INDEX.md`✅ MATCH / ❌ MISSING
- 3. **Dependencies#include** `"GraphicsBackend.h"`Bảng `Dependency Matrix` có `GraphicsBackend` ✅ MATCH / ❌ OMISSION
- 4. `Thread Model` Dùng std::mutex m_queueLock trong `EventQueue` Phần `Thread Model` có khai báo Mutex sync✅ MATCH / ❌ UNCHAINED5. `API List` Xuất hiện public method `Resize(int, int)` Section Public APIs đã liệt kê `Resize()`✅ MATCH / ❌ OUTDATED

### BƯỚC 3: Quyết Định Pass / Fail & Xử Lý Vòng Lặp (Decision Engine)

- NẾU TẤT CẢ TRỤC ĐỀU MATCH (Status: PASS):Cập nhật Last Scan timestamp và Hash mới nhất vào Metadata.

- Nâng Version tài liệu (ví dụ: v1.2 $\rightarrow$ v1.3).Đánh dấu cờ: ⚠️ STATUS: OUTDATED $\rightarrow$ ✅ STATUS: VALIDATED & SYNCED.

- NẾU VẪN CÒN DRIFT (Status: FAIL):Liệt kê chính xác các điểm còn lệch (Mismatch Delta Log).Tự động đẩy ngược lại cho architecture-repair xử lý đúng vị trí chưa khớp (Maximum retry limit: 2 vòng).Nếu quá 2 vòng vẫn FAIL $\rightarrow$ Báo cáo cảnh báo cho Developer kiểm tra thủ công.

### BƯỚC 4: Báo Cáo Kiểm Chứng (Validation Report)In ra output ngắn gọn để xác nhận với Developer:Markdown### 🛡️ Architecture Validation Completed

- **Target Module:** `src/window/` (`WindowRuntime`)
- **Validation Result:** ✅ **PASS (100% Synced)**
- **Checked Axes:**
  - [x] Ownership & Lifetime Graph
  - [x] Symbol Database Sync (`SYMBOL_INDEX.md`)
  - [x] Dependency Cache Hash (`DEPENDENCY_CACHE.md`)
  - [x] Threading & Synchronization Model
  - [x] Public API Declarations
- **Document Status Updated:** `FOLDER_INFO.md` (v1.3 - SHA: `a1b2c3d4`)