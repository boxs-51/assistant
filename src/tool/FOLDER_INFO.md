# Metadata
- **Last Scan:** 2026-07-26
- **Source Files:** ~9
- **Hash:** N/A
- **Depends On:** `mcp` (external library), `structlog`
- **Scanned Files:** All files in `src/tool`

# 📂 Thư Mục: `tool`

## 1. Architecture Decisions & Design Patterns
Module `tool` cung cấp một framework để định nghĩa, đăng ký, và thực thi "công cụ" (function calling) một cách linh hoạt và có khả năng mở rộng.

- **Kiến trúc tổng thể (Architectural Style):**
  - **Pluggable Framework:** Module được thiết kế như một framework có thể cắm thêm các loại executor khác nhau.
  - **Strategy-Based Execution:** Logic thực thi được lựa chọn dựa trên loại tool, cho phép hệ thống xử lý các tool từ nhiều nguồn khác nhau (local, remote) một cách thống nhất.

- **Design Patterns chính:**
  - **Strategy:** `ExecutorRegistry` là một υλοποίηση của Strategy pattern. Nó map một `ToolType` (LOCAL, MCP, WORKFLOW) tới một `BaseExecutor` cụ thể để thực thi.
  - **Facade:** `GatewayToolManager` là một Facade đơn giản, cung cấp một giao diện cấp cao (`execute_tool`, `get_accessible_tools`) để phần còn lại của ứng dụng tương tác với hệ thống tool.
  - **Registry:** `ToolRegistry` và `ExecutorRegistry` quản lý các "định nghĩa" và các "executor", tách biệt việc đăng ký khỏi việc sử dụng.
  - **Interpreter:** `WorkflowExecutor` hoạt động như một trình thông dịch, đọc một `WorkflowDefinition` và thực thi tuần tự các bước, có khả năng truyền output của bước trước làm input cho bước sau.
  - **Resilience (trong `_mcp`):** `GatewayMcpManager` sử dụng các pattern chống lỗi như **Automatic Reconnect with Exponential Backoff** và **Health Probes (Heartbeating)** để quản lý kết nối đến các server tool bên ngoài.

## 2. Dependency & Ownership Graph
- `GatewayToolManager` là orchestrator cấp cao nhất, sở hữu `ToolRegistry` và `ExecutorRegistry`.
- `ExecutorRegistry` sở hữu các instance của các executor (`LocalExecutor`, `McpExecutor`...).
- `McpExecutor` là client của `GatewayMcpManager`.
- `GatewayMcpManager` sở hữu một pool các `McpConnection`, mỗi connection quản lý trạng thái và kết nối đến một server tool bên ngoài.

## 3. Thread Model & Event/Data Flow
- **Thread Model:** Hoàn toàn bất đồng bộ (`asyncio`). `GatewayMcpManager` sử dụng các background task (`asyncio.create_task`) để quản lý kết nối và kiểm tra health check mà không block luồng chính.
- **Luồng dữ liệu (`execute_tool`):**
  1. `GatewayToolManager` nhận `tool_name` và `arguments`.
  2. Nó tra cứu `ToolType` của tool trong `ToolRegistry`.
  3. Dựa vào `ToolType`, nó lấy `executor` tương ứng từ `ExecutorRegistry`.
  4. Nó ủy quyền việc thực thi cho `executor` đó.
  5. **Nếu là `McpExecutor`:** Lấy credential, lấy kết nối sống từ `GatewayMcpManager`, và thực hiện RPC call.
  6. **Nếu là `WorkflowExecutor`:** Bắt đầu vòng lặp thông dịch các bước, giải quyết các placeholder (`{{...}}`), và gọi đệ quy đến các executor khác để chạy các tool con.
  7. Kết quả cuối cùng (một chuỗi) được trả ngược về cho `GatewayToolManager`.

## 4. Public APIs & Configuration
- **Public API:** `GatewayToolManager.execute_tool()` và `GatewayToolManager.get_accessible_tools()`.
- **Configuration:** Các MCP server được đăng ký vào hệ thống một cách chủ động thông qua code (`GatewayMcpManager.register_and_connect`), không qua file config tĩnh.

## 5. Risk Matrix & Error-Prone Areas (Classified)
- **Complexity (Rủi ro cao):** `WorkflowExecutor` rất mạnh mẽ nhưng cũng rất phức tạp. Lỗi trong một workflow definition (ví dụ: tham chiếu vòng, placeholder sai) có thể khó gỡ lỗi.
- **Reliability (Rủi ro trung bình):** `GatewayMcpManager` được thiết kế để phục hồi lỗi, nhưng nó phụ thuộc vào sự ổn định của các tiến trình tool server bên ngoài. Nếu một server bị "treo" thay vì crash, cơ chế health check có thể không phát hiện ra.
- **Security (Rủi ro trung bình):** `get_accessible_tools` dựa vào các scope OAuth trong `Identity` của người dùng. Việc quản lý và xác thực các scope này phải được thực hiện một cách cực kỳ cẩn thận. `CredentialManager` cũng là một điểm nhạy cảm, cần đảm bảo nó lấy credential một cách an toàn.

## 6. Technical Debt (TODO / FIXME / HACK)
- **Observability:** Việc publish sự kiện trong `GatewayToolManager` đang bị comment. Đây là một tính năng quan trọng để theo dõi và giám sát việc thực thi tool.
- **Credential Management:** `CredentialManager` hiện tại rất đơn giản. Một hệ thống thực tế cần một nơi an toàn để lưu trữ và truy xuất credential của người dùng (ví dụ: encrypted database, HashiCorp Vault).
- Chưa có executor cho `NATIVE` tool, mặc dù `ToolType` đã được định nghĩa.
