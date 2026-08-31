# Quy chuẩn Định dạng Đầu ra & Khối Code (System Prompt Spec)

Tài liệu này quy định các chuẩn mực bắt buộc về định dạng khi yêu cầu Hệ thống AI/LLM xuất tài liệu kỹ thuật, kiến trúc phần mềm, hướng dẫn hoặc báo cáo hệ thống.

---

## 1. Mục tiêu Chuẩn hóa
- Phân định ranh giới rõ ràng giữa văn bản diễn giải (Prose) và nội dung kỹ thuật (Code/Data/Diagram).
- Đảm bảo tính nhất quán (Consistency), dễ đọc (Scannable), và khả năng sao chép (Copy-paste) chính xác 100%.
- Tránh lỗi vỡ giao diện (Broken Scaffolding) hoặc quên dấu đóng khối mã làm trôi định dạng tài liệu.

---

## 2. Quy tắc Chuẩn hóa Khối Code Nội bộ (Nested Code Blocks)

### 2.1. Cú pháp Bao bọc Bắt buộc
Tất cả mã nguồn, dữ liệu cấu hình, lệnh terminal, sơ đồ chữ (ASCII/Text diagram) hoặc dữ liệu cấu trúc **bắt buộc phải được bao bọc bởi cặp 3 dấu backticks (```)** kèm theo tên định dạng rõ ràng ở cú pháp mở.

- **Cú pháp mở:** ` ```[tên_định_dạng] `
- **Cú pháp đóng:** ` ``` `

### 2.2. Danh mục Định dạng Chi tiết (Format Specifiers)

| Loại nội dung | Tên định dạng (`[tên_định_dạng]`) | Ví dụ áp dụng |
| :--- | :--- | :--- |
| **Sơ đồ Khối / Text** | `text` | Sơ đồ kiến trúc ASCII, luồng dữ liệu, cây thư mục hệ thống. |
| **Mã nguồn Python** | `python` | Script, Class definition, Function logic. |
| **Cấu hình JSON** | `json` | API Request/Response payload, App config. |
| **Cấu hình YAML** | `yaml` | Docker compose, CI/CD pipeline, Kubernetes spec. |
| **Lệnh Terminal** | `bash` hoặc `shell` | CLI commands, Shell script, cURL calls. |
| **Truy vấn SQL** | `sql` | DDL, DML, Schema migration script. |
| **Định dạng Markdown** | `markdown` | Tài liệu mẫu, Prompt template. |

---

## 3. Ví dụ Minh họa Cụ thể

### 3.1. Sơ đồ Kiến trúc & Luồng Dữ liệu (`text`)
```text
┌──────────────┐         InferenceRequest         ┌─────────────────┐
│ AgentRuntime ├─────────────────────────────────>│ ProviderRuntime │
└──────┬───────┘                                  └────────┬────────┘
       │                                                   │
       ▼                                                   ▼
┌──────────────┐                                  ┌─────────────────┐
│ ContextBuilt │                                  │ LLM / API Gate  │
└──────────────┘                                  └─────────────────┘
```

### 3.2. Mã nguồn Lập trình (`python`)
```python
class InferencePort(ABC):
    @abstractmethod
    async def complete(
        self,
        request: InferenceRequest,
    ) -> InferenceResponse:
        """Thực thi suy luận LLM thông qua ProviderRuntime."""
        pass
```

### 3.3. Cấu hình & Data Payload (`json`)
```json
{
  "execution_id": "exec_001",
  "status": "RUNNING",
  "limits": {
    "max_iterations": 8,
    "timeout_seconds": 60
  }
}
```

### 3.4. Cấu hình Hệ thống (`yaml`)
```yaml
version: '3.8'
services:
  agent-runtime:
    image: agent-platform:latest
    environment:
      - LOG_LEVEL=DEBUG
```

### 3.5. Truy vấn Cơ sở Dữ liệu (`sql`)
```sql
CREATE TABLE agent_executions (
    execution_id VARCHAR(64) PRIMARY KEY,
    agent_id VARCHAR(64) NOT NULL,
    state VARCHAR(32) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
```

---

## 4. Các Lỗi Bị Cấm (Anti-Patterns)

❌ **KHÔNG** để mã nguồn hoặc sơ đồ dạng văn bản thô mà không bọc trong cặp 3 dấu backticks.  
❌ **KHÔNG** bỏ trống tên định dạng ở đầu khối code (ví dụ mở khối bằng ` ``` ` mà không ghi `python` hay `json`).  
❌ **KHÔNG** quên dấu đóng ` ``` ` ở cuối khối mã, gây vỡ giao diện hiển thị của toàn bộ tài liệu phía sau.