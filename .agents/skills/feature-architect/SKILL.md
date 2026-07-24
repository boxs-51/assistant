---
name: feature-architect
description: Phân tích, thiết kế và triển khai tính năng mới (Feature Expansion). Hỗ trợ New Module Discovery cho các module hoàn toàn mới, đề xuất Ownership, Thread Model và Proposal trước khi viết code.
---

# Quy Trình Triển Khai Tính Năng Mới An Toàn

Khi nhận yêu cầu thêm tính năng (ví dụ: *"Thêm Audio Mixer"*), tuân thủ quy trình thiết kế sau:

---

## 📐 Pipeline Triển Khai:

### Bước 1: Đọc Kiến Trúc Hoặc Khám Phá Module Mới (New Module Discovery)
1. Kiểm tra tài liệu sẵn có tại `ARCHITECTURE_INDEX.md`.
2. **Nếu tính năng thuộc Module CHƯA TỒN TẠI (Unknown Component):**
   - Kích hoạt **New Module Discovery**:
     - Phân tích vị trí thích hợp nhất trong cây thư mục.
     - Xác định Class sở hữu (**Parent/Owner**).
     - Xác định Luồng chạy (**Thread Context**).
     - Đề xuất phụ thuộc (**Dependency Graph**).

### Bước 2: Lập Kịch Bản Thiết Kế (Proposal)
Trình bày kịch bản ngắn gọn trước khi viết code:

### 📐 Kịch Bản Thiết Kế: [Tên_Tính_Năng]
- **Unkown Module Discovery:** AudioMixer (Parent: PlaybackSession - Confidence: 85%)
- **Proposed Location:** `src/core/audio/`
- **Ownership Diagram:** `PlaybackSession` ──owns──> `AudioMixer`
- **Thread Context:** Worker Thread (Bất đồng bộ với Main Render Thread)
- **Proposed Public APIs:** `Init()`, `MixStreams()`, `SetVolume()`

### Bước 3: Viết Code & Đồng Bộ Tài Liệu
Triển khai code theo đúng Design Pattern sẵn có.

- Gọi `architecture-repair` hoặc `project-summarizer` để ghi tài liệu `FOLDER_INFO.md` mới cho module này.