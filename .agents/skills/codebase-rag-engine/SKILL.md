---
name: codebase-rag-engine
description: Tự động kích hoạt khi cần tra cứu ngữ nghĩa sâu (Semantic Search), tìm kiếm đoạn code/tài liệu liên quan rộng trong toàn bộ dự án mà các file Index (Level 1-3) không bao phủ hết, hoặc khi Context Window tiệm cận giới hạn.
---

# Quy Trình Tra Cứu Tri Thức Ngữ Nghĩa & Nén Context (Codebase RAG Engine)

Skill này đóng vai trò cầu nối Retrieval (RAG) giữa Codebase/Tài liệu và LLM Context Window.

---

## 🔍 1. Workflow RAG 4 Bước (Retrieval Pipeline)

Khi nhận câu hỏi phức tạp hoặc truy vấn diện rộng (VD: *"Tìm tất cả những nơi dùng OpenGL FBO"*, *"Kiểm tra cách quản lý Memory ở toàn bộ các module UI"*):

```text
[User Query] ➔ [Query Expansion & Tokenize] ➔ [Top-K Chunk Retrieval] ➔ [Context Rerank & Compress] ➔ [LLM Response]
```
# Bước 1: Phân Luồng Chunking & Indexing
- Quét các file mã nguồn (.cpp, .h) và tài liệu (.md).

- Chia nhỏ code thành các Chunk có ý nghĩa (Semantic Chunks) dựa trên Class, Function Boundary, hoặc Struct, không cắt ngang hàm.

# Bước 2: Tối Ưu Hóa Truy Xuất (Hybrid Retrieval Strategy)
- Kết hợp 2 phương pháp tra cứu:

- Keyword/Symbol Match (Lexical Search): Tra cứu chính xác Tên hàm, Tên biến, Macro (Sử dụng symbol_index từ memory-manager).

- Intent/Behavior Match (Semantic Search): Tra cứu theo hành vi/mục đích (VD: "xóa bộ nhớ đệm", "khởi tạo OpenGL context").

# Bước 3: Re-ranking & Filtering (Lọc Top-K Context)
- Chọn ra tối đa Top 3 - Top 5 Chunks có điểm liên quan (Relevance Score) cao nhất.

- Lọc bỏ code thừa, comment không cần thiết để tối ưu hóa lượng Token (Token Budgeting).

# Bước 4: Inject Context & Citations
- Đưa đoạn code/tài liệu đã lọc vào prompt hiện tại kèm theo trích dẫn nguồn chính xác:

Source File: src/graphics/OpenGLBackend.cpp (Lines 120-155)
---
### ⚙️ 2. Mối Tương Tác Với Các Skill Khác
- Đứng Sau memory-manager: Nếu memory-manager có sẵn câu trả lời trong Project Memory ➔ KHÔNG cần chạy RAG.

- Hỗ Trợ bug-flow-navigator: Khi bug-flow-navigator tìm ở Level 3 (Symbol Search) thất bại, nó sẽ gọi codebase-rag-engine để tìm kiếm ngữ nghĩa ở Level 4.

- Hỗ Trợ learning-mode: Giúp gom tất cả các Chunks liên quan đến một chủ đề (VD: Audio, Shader, ImGui Event) từ nhiều thư mục khác nhau về một chỗ.


---

## 🧩 Sơ Đồ Định Vị `codebase-rag-engine` Trong Hệ Thống

```text
                    [User Request]
                          │
                          ▼
                  (memory-manager) ──► Hit Memory? ──► YES ──► [Trả lời ngay]
                          │
                         NO
                          ▼
              (bug-flow-navigator / feature-architect)
                          │
                   Cần tra cứu rộng?
                          │
                          ▼
               (codebase-rag-engine)
             ┌────────────┴────────────┐
             ▼                         ▼
   Lexical Search (Symbol)    Semantic Search (Intent)
             │                         │
             └────────────┬────────────┘
                          ▼
            [Top-K Chunks + Citation]
                          ▼
              [Xử lý logic / Sửa code]
```