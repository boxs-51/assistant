---
name: learning-mode
description: Chế độ nghiên cứu và tích lũy tri thức sâu (Deep Knowledge Accumulation). Phân tích toàn diện một module hoặc toàn bộ codebase để làm giàu hệ thống tài liệu kiến trúc dài hạn mà không thực hiện sửa lỗi.
---

# Quy Trình Nghiên Cứu & Tích Lũy Tri Thức Kiến Trúc (Learning Mode)

Khi người dùng yêu cầu: *"Hãy nghiên cứu module MPV"*, *"Nghiên cứu toàn bộ luồng Render"*, hoặc *"Học kiến trúc hệ thống"*, kích hoạt quy trình tích lũy tri thức 6 bước:

---

## 🧠 Workflow Nghiên Cứu Sâu (Deep Reconstruction Pipeline):

### Bước 1: Quét Mã Nguồn & Tài Liệu Cũ
Đọc tất cả file `.cpp`, `.h` và các file `.md` hiện có liên quan đến module được chỉ định.

### Bước 2: Dựng Lại Kiến Trúc Toàn Diện (Full Reconstruction)
Trích xuất và chuẩn hóa 8 góc nhìn kiến trúc cốt lõi:
1. **Ownership Graph:** Cây sở hữu tài nguyên và bộ nhớ.
2. **Dependency Graph:** Ma trận phụ thuộc giữa các class/module.
3. **State Machine:** Sơ đồ chuyển đổi trạng thái (State Diagram).
4. **Thread Model & Sync:** Bản đồ luồng và vị trí dùng Mutex/Lock.
5. **Event & Data Flow:** Luồng truyền tin nhắn và dữ liệu qua lại.
6. **Public API List:** Danh sách tất cả API công khai và mục đích.
7. **Potential Bugs & Risks:** Phân loại rủi ro (Memory, Race, Context).
8. **Technical Debt:** Quét tất cả thẻ `TODO`, `FIXME`, `HACK`.

### Bước 3: Cập Nhật & Tích Lũy Tri Thức (Knowledge Enrichment)
- Ghi đè/Cập nhật hoàn chỉnh file `FOLDER_INFO.md` của module đó.
- Cập nhật chỉ mục nhanh tại `ARCHITECTURE_INDEX.md`.
- Bổ sung sơ đồ tổng thể vào `PROJECT_STRUCTURE.md`.

### Bước 4: Xuất Báo Cáo Nghiên Cứu (Learning Report)
In ra tóm tắt ngắn gọn kết quả nghiên cứu cho người dùng:
```markdown
### 🎓 Learning Mode Completed: Module [Tên_Module]

- **Status:** Tài liệu đã được làm giàu hoàn toàn.
- **Key Findings:**
  - Phát hiện State Machine gồm 4 trạng thái: `Uninitialized` -> `Ready` -> `Playing` -> `Error`.
  - Phát hiện 2 vị trí rủi ro Data Race tại callback thread.
- **Updated Files:**
  - `src/mpv/FOLDER_INFO.md`
  - `ARCHITECTURE_INDEX.md`