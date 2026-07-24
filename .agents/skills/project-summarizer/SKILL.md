---
name: project-summarizer
description: Phân tích kiến trúc mã nguồn C++, trích xuất metadata, đồ thị phụ thuộc/sở hữu (Ownership/Lifetime), mô hình đa luồng, phân loại rủi ro lỗi và nợ kỹ thuật. Xuất các file FOLDER_INFO.md ở các folder con, ARCHITECTURE_INDEX.md và PROJECT_STRUCTURE.md ở root.
---

# Quy Trắc Phân Tích Kiến Trúc & Nguy Cơ Lỗi C++ Nâng Cao

Khi được yêu cầu "tóm tắt cấu trúc thư mục", "phân tích dự án" hoặc "tạo tài liệu kiến trúc", thực hiện chính xác các bước sau:

## 1. Bốn File Đầu Ra Cần Sinh:
1. **`FOLDER_INFO.md`** tại từng thư mục con (chứa Metadata, Ownership, Threading, Classified Bugs, Tech Debt,...).
2. **`ARCHITECTURE_INDEX.md`** tại root (Index ngắn gọn vài trăm dòng để AI tra cứu cực nhanh).
3. **`PROJECT_STRUCTURE.md`** tại root (Tài liệu kiến trúc toàn diện tổng hợp từ các module).

---

## 2. Template Chuẩn Cho `FOLDER_INFO.md` (Thư Mục Con)

```markdown
# Metadata
- **Last Scan:** [YYYY-MM-DD]
- **Source Files:** [Số lượng file]
- **Hash:** [Mã Hash nội dung]
- **Depends On:** [Tên các module phụ thuộc]
- **Scanned Files:** [Danh sách các file .cpp/.h]

# 📂 Thư Mục: `[Tên_Thư_Mục]`

## 1. Architecture Decisions & Design Patterns
- **Patterns:** [Factory / Observer / Strategy / Singleton / Command]
- **Decisions:** [Ví dụ: One ImGui Context per Window -> Reason: Thread-safe]

## 2. Dependency & Ownership Graph
### Dependency
`[Module]` → `[Lower Module]` → `[Backend]`

### Ownership & Lifetime
- `[OwnerClass]` **owns** `[ChildClass]`
- `[ClassA]` **shares** `[SharedResource]`
- **Lifetime:** `Create()` → `InitContext()` → `Running` → `DestroyContext()` → `Destroy()`

## 3. Thread Model & Event/Data Flow
- **Main Thread:** [SDL Event, UI Logic]
- **Render Thread:** [ImGui, OpenGL context]
- **Worker/Callback Thread:** [MPV Observer]
- **Synchronization:** `std::mutex`, `std::atomic`
- **Event Flow:** `SDL_Event` → `WindowManager` → `WindowRuntime` → `Renderer`
- **Data Flow:** `VideoInfo` → `MPVObserver` → `PropertyBag` → `Renderer`

## 4. Public APIs & Configuration
- **APIs:** `Create()`, `Destroy()`, `Render()`, `Update()`, `Resize()`
- **Configuration:** Uses `WindowStyle`, `PropertyBag`, `GraphicsBackend`

## 5. Risk Matrix & Error-Prone Areas (Classified)
- **Memory:** [Leak, dangling pointer, double free]
- **Thread:** [Data race, deadlock, UI block]
- **Rendering:** [Invalid context, unbound FBO]
- **Exception:** [Uncaught exceptions]
- **Performance / Complexity:** [O(n²) loops, `WindowRuntime::Update()` - 320 lines (Risk: High)]
- **Ownership:** [Circular reference]

## 6. Technical Debt (TODO / FIXME / HACK)
- `WindowRuntime.cpp` - **TODO:** Chưa có EventQueue
- `Renderer.cpp` - **FIXME:** Context cleanup timing