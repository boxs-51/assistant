---
name: bug-flow-navigator
description: Tự động kích hoạt BẤT KỲ KHI NÀO người dùng hỏi về lỗi, bug, nhấp nháy, màn hình đen, crash, giật lag, leak bộ nhớ, hoặc yêu cầu trace luồng dữ liệu (ví dụ: 'sao lại lỗi...', 'tại sao bị nhấp nháy...', 'trace luồng...').

---


# Quy Trình Truy Vết Lỗi & Luồng Dữ Liệu 6 Cấp Độ (6-Level Trace Debugger)


Khi nhận câu hỏi tìm lỗi, trace luồng dữ liệu (như *"Tại sao EventQueue lỗi?"*, *"Trace MPVSession"*), thực hiện nghiêm ngặt theo **Pipeline Cascade 6 Level**:

Ưu tiên truy vết 6 cấp độ (Architecture Index -> Folder Info -> Symbol -> Raw Source -> Discovery -> Repair). Hỗ trợ xuất Root Cause Report, Flow Trace và đề xuất tự động cập nhật tài liệu.

---

## 🧗‍♂️ Pipeline Trace 6 Cấp Độ

```text
Level 1: ARCHITECTURE_INDEX.md / PROJECT_STRUCTURE.md (Root Fast Lookup)
   ↓ (Nếu không đủ thông tin hoặc thiếu file)
Level 2: FOLDER_INFO.md (Thư mục con)
   ↓ (Nếu tài liệu cũ / không khớp với code)
Level 3: Code Symbol Search (Tìm định nghĩa Class, Function, AST)
   ↓ (Nếu không tìm thấy Symbol)
Level 4: Raw Source Scan (Đọc trực tiếp file .cpp / .h)
   ↓ (Nếu là Unknown Component / Chưa từng có tài liệu)
Level 5: Architecture Discovery (Tự sinh TEMP_FOLDER_INFO.md)
   ↓ (Nếu phát hiện tài liệu cũ hoặc mới Discovery xong)
Level 6: Documentation Repair (Gọi architecture-repair để sửa/đồng bộ tài liệu)
```