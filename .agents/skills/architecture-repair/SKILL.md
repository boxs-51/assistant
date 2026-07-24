---
name: architecture-repair
description: Phát hiện và xử lý hiện tượng Documentation Drift (khi code thực tế lệch với tài liệu .md). Tự động cập nhật FOLDER_INFO.md cục bộ, đồng bộ lại ARCHITECTURE_INDEX.md và PROJECT_STRUCTURE.md mà không cần rebuild toàn bộ hệ thống tài liệu.
---

# Quy Trình Sửa Đổi & Đồng Bộ Tài Liệu Kiến Trúc (Architecture Repair)

Kích hoạt khi phát hiện sự bất đồng bộ giữa mã nguồn thực tế và hệ thống tài liệu `.md` (Ownership đổi, Thread Model thay đổi, API mới, hỏng Metadata).

---

## 🔄 Quy Trình 5 Bước Phục Hồi (Repair Pipeline):

### BƯỚC 1: Phát Hiện Sự Lệch Cấu Trúc (Drift Detection)
- So sánh code thực tế với các mục trong `FOLDER_INFO.md`:
  - **Ownership:** Code ghi `WindowResource` owns `Renderer`, nhưng `.md` ghi `WindowRuntime` owns `Renderer` ➔ **DRIFT DETECTED**.
  - **Thread Model / API:** Xuất hiện hàm public mới hoặc cơ chế mutex/sync mới chưa ghi nhận.

### BƯỚC 2: Đánh Dấu Cảnh Báo (Mark Documentation Drift)
Ghi tạm thời trạng thái vào file `FOLDER_INFO.md` bị lệch:
```markdown
> ⚠️ **STATUS: OUTDATED**
> **Reason:** [Lý do: Ownership / Thread / API / Structure Changed]
> **Drift Detected:** [Dòng code hoặc file gây lệch]
```

## Bước 3: Cập Nhật Cục Bộ (Localized Update)
Chỉ quét và viết lại đúng file FOLDER_INFO.md của thư mục bị ảnh hưởng.

- **Cập nhật Metadata:** Cập nhật lại ngày Last Scan, Source Files và Hash mới nhất.

### Bước 4: Lan Truyền Lên Root (Ripple Update - Nếu Cần)
Nếu thay đổi mang tính hệ thống (đổi tên Class lớn, thêm/xóa Module), tiến hành cập nhật lại bảng chỉ mục gọn nhẹ tại ARCHITECTURE_INDEX.md và PROJECT_STRUCTURE.md.

```markdown
📊 Format Báo Cáo Xuất Ra
1. Root Cause Report (Báo Cáo Nguyên Nhân Lỗi)

### 🚨 Root Cause Report

* **Root Cause:** [Giải thích ngắn gọn nguyên nhân gốc rễ]
* **Evidence:** `WindowRuntime.cpp:210`
* **Impact:** Black screen / Memory leak / Thread Crash
* **Risk Level:** High / Medium / Low
* **Category:** [Memory / Thread / Rendering / Exception / Ownership]
* **Suggested Fix:**
  [Đoạn code sửa đổi ngắn gọn]
2. Flow Trace Report (Sơ Đồ Luồng)
Markdown
### 🔄 Flow Trace: [Tên_Luồng]

`Component A` ──(Event)──> `Component B` ──(Data)──> `Thread Boundary` ──> `Renderer`
```