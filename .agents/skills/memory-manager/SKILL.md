---
name: memory-manager
description: Điều phối, tải (Load), cập nhật (Update), kiểm tra độ tin cậy (Confidence) và lưu trữ (Persist) tri thức kiến trúc dự án giữa các Session. Đảm bảo quy tắc Staging -> Validation -> Project Memory.Skill này luôn chạy **ĐẦU TIÊN (Pre-execution)** và **CUỐI CÙNG (Post-execution)** trong mọi workflow của người dùng.
---

# Quy Trình Quản Lý Bộ Nhớ Kiến Trúc (Architecture Memory Orchestration)

---

## 🔄 1. Quy Trình Đầu Lượt (Pre-Execution / Load Phase)

Khi nhận câu hỏi từ người dùng, thực hiện theo thứ tự:
1. **Load Session & Project Memory:** Đọc `.agents/memory/project_memory.json` và `root_cause_memory.json`.
2. **Nạp Constraints & Decisions:** Kiểm tra các ràng buộc thiết kế bắt buộc (như *"One ImGui Context per Window"*, *"No Singletons"*).
3. **Merge Context:** Kết hợp thông tin từ Memory với Request của người dùng. Nếu thông tin đã có trong Project Memory với `Confidence >= 90%` và `Hash` mã nguồn không đổi ➔ **KHÔNG đọc lại code/tài liệu `.md`**, sử dụng trực tiếp Memory.

---

## ⚡ 2. Quy Trình Cuối Lượt (Post-Execution / Memory Commit Phase)

Chỉ được phép ghi Memory theo Quy tắc An Toàn 3 Cấp (Three-Tier Commit Rule):

```text
Discovery / Hypothesis (Đoán) ──> Staging Memory (Confidence < 70%)
                                       │
                         [Xác minh qua Code / User Confirm]
                                       │
                                       ▼
                              Project Memory (Confidence >= 90%)
```

---
- Điều Kiện Ghi Vào project_memory.json:
- ĐÃ XÁC MINH (Validated): Mã nguồn thực tế hoặc tài liệu đã được đọc và khớp 100%.

- QUYẾT ĐỊNH CỦA USER (Decision Confirmed): Người dùng chốt một hướng kiến trúc (ví dụ: "Tôi chọn dùng Lock-free Queue cho Audio").

- ROOT CAUSE ĐÃ FIX (Bug Resolved): Một nguyên nhân lỗi đã được debug thành công.

### 📝 3. Structure Mẫu Của project_memory.json
```json
{
  "constraints": [
    "One ImGui Context per Window",
    "No Global/Static Renderer Instance",
    "UI operations MUST remain on Main Thread"
  ],
  "decisions": [
    {
      "id": "DEC_001",
      "decision": "One ImGui Context per Window",
      "reason": "Ensure thread-safety across multi-window UI rendering",
      "date": "2026-07-24"
    }
  ],
  "knowledge_graph": {
    "WindowRuntime": {
      "owns": ["Renderer", "WindowController"],
      "thread": "MainThread",
      "lifetime": "AppLifecycle"
    },
    "Renderer": {
      "depends_on": ["IGraphicsBackend", "OpenGLContext"],
      "thread": "RenderThread"
    }
  },
  "symbol_index": {
    "WindowRuntime::Update": {
      "file": "src/core/WindowRuntime.cpp",
      "complexity": "High",
      "calls": ["Renderer::Render", "MPVSession::Observe"]
    }
  }
}
```
### 🎯 4. Structure Mẫu Của root_cause_memory.json
```json
{
  "known_issues": [
    {
      "id": "BUG_001",
      "symptom": "IM_ASSERT on Shared Font Atlas during DestroyContext",
      "root_cause": "Context destroyed before SDL_DestroyWindow freed the shared texture",
      "impact": "Crash on close",
      "fix_pattern": "Reorder cleanup: Destroy Window UI -> Destroy ImGui Context -> Destroy SDL Window",
      "confidence": 0.98
    }
  ]
}
```
---

## 🧩 3. Tích Hợp Memory Layer Vào Toàn Bộ Pipeline Sự Kiện

Bây giờ, khi kết hợp **Memory Layer** với các Skill trước đó, chúng ta có một Sơ đồ Tổng thể (Unified Pipeline Workflow) hoạt động như sau:

```text
                  [User Request]
                        │
                        ▼
            ┌──────────────────────┐
            │   Memory Manager     │ ◄─── Loads project_memory.json
            └───────────┬──────────┘      & root_cause_memory.json
                        │
                        ▼
         ┌────────────────────────────┐
         │  Workflow Planning Engine  │
         └──────────────┬─────────────┘
                        │
      ┌─────────────────┼─────────────────┐
      ▼                 ▼                 ▼
[Bug Debugging]  [Feature Design]   [Learning Mode]
(bug-navigator) (feature-architect) (learning-mode)
      │                 │                 │
      ├─────────────────┴─────────────────┤
      ▼                                   ▼
 [Level 1-3: Fast Memory Search]   [Level 4: Raw Code Scan]
      │                                   │
      └─────────────────┬─────────────────┘
                        ▼
             [Unknown Component?]
                        │
                 YES ───┼─── NO
                  │         │
                  ▼         │
      (architecture-discovery)
                  │         │
                  ▼         │
        [TEMP_FOLDER_INFO]  │
        [Staging Memory]    │
                  │         │
                  └───┬─────┘
                      ▼
         [Validation & User Confirm]
                      │
                      ▼
         (architecture-repair)
                      │
                      ▼
        ┌───────────────────────────┐
        │  Update Project Memory    │ ───► Commit to project_memory.json
        └───────────────────────────┘      & FOLDER_INFO.md
```