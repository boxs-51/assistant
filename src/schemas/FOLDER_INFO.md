# Metadata
- **Last Scan:** 2026-07-26
- **Source Files:** ~22
- **Hash:** N/A
- **Depends On:** `pydantic`
- **Scanned Files:** All files in `src/schemas`

# 📂 Thư Mục: `schemas`

## 1. Architecture Decisions & Design Patterns
Module `schemas` là "từ điển dữ liệu" của toàn bộ ứng dụng. Nó định nghĩa các cấu trúc dữ liệu (Data Transfer Objects - DTOs) được xác thực chặt chẽ và sử dụng bởi tất cả các module khác. Việc sử dụng Pydantic là một quyết định thiết kế cốt lõi, cung cấp validation, serialization, và tài liệu hóa trong cùng một nơi.

- **Kiến trúc tổng thể (Architectural Style):**
  - **Shared Kernel / Canonical Data Model:** Module này định nghĩa một mô hình dữ liệu chuẩn hóa, không phụ thuộc vào nhà cung cấp dịch vụ (provider-agnostic). Các module khác, đặc biệt là các adapter trong `provider`, chịu trách nhiệm chuyển đổi từ định dạng riêng của họ sang định dạng chung này và ngược lại.
  - **Schema-First Design:** Cấu trúc chi tiết và toàn diện của các schema cho thấy một triết lý thiết kế trong đó các hợp đồng dữ liệu được định nghĩa trước, tạo ra một giao ước rõ ràng cho các thành phần khác trong hệ thống.

- **Design Patterns chính:**
  - **Data Transfer Object (DTO):** Mọi model Pydantic trong module này đều là một DTO, chỉ dùng để vận chuyển dữ liệu, không chứa logic nghiệp vụ.
  - **Enum:** Được sử dụng rộng rãi (`FinishReason`, `MessageContentType`, `ModelCapability`) để định nghĩa các hằng số một cách an toàn về kiểu dữ liệu, tránh lỗi "magic string".

## 2. Dependency & Ownership Graph
- **Central Dependency:** Đây là module phụ thuộc trung tâm. Hầu hết các module khác trong `src` (`gateway`, `provider`, `context`, `runtime`...) đều phụ thuộc vào `schemas`.
- **Low Coupling:** `schemas` gần như không có dependency nào khác ngoài `pydantic`, giúp nó rất ổn định và dễ dàng tái sử dụng.

## 3. Phân loại Schema
Các schema được tổ chức một cách hợp lý theo từng lĩnh vực nghiệp vụ:
- **Request/Response (`request.py`, `response.py`):** Định nghĩa các DTO chuẩn hóa cho đầu vào (`GatewayChatRequest`) và đầu ra (`GatewayResponse`, `GatewayStreamChunk`) của gateway.
- **Message & Content (`message.py`, `attachment.py`):** Định nghĩa cấu trúc message đa phương tiện (multimodal), cho phép một tin nhắn chứa cả văn bản, hình ảnh, âm thanh...
- **Tooling (`tool.py`):** Định nghĩa cách một "công cụ" (tool) được mô tả, được gọi bởi LLM, và cách kết quả được trả về.
- **Authentication & Identity (`auth.py`, `identity.py`):** Định nghĩa các schema cho API xác thực (đăng nhập, tạo API key) và quan trọng nhất là schema `Identity`—đối tượng bất biến đại diện cho một người dùng đã được xác thực.
- **Context & Session (`context.py`, `session.py`):** Định nghĩa các đối tượng trạng thái cho một phiên làm việc, project, và ngữ cảnh tổng thể được nạp vào runtime.
- **Provider & Model Metadata (`model.py`, `provider.py`, `pricing.py`):** Các DTO giàu thông tin mô tả một model, nhà cung cấp, và các khả năng (capabilities) của chúng, là nền tảng cho thuật toán routing.
- **Runtime (`runtime/runtime.py`):** Định nghĩa các message `RuntimeCommand` và `RuntimeEvent` cho kiến trúc Actor Model.

## 4. Rủi ro & Nợ kỹ thuật
- **Complexity (Rủi ro trung bình):** Số lượng schema và mối quan hệ giữa chúng là khá lớn. Việc hiểu rõ cách `GatewayMessage`, `MessageContentPart`, và `GatewayAttachment` kết hợp với nhau đòi hỏi sự nghiên cứu cẩn thận.
- **Coupling (Rủi ro thấp):** Mặc dù là một dependency trung tâm, bất kỳ thay đổi nào gây lỗi (breaking change) ở đây sẽ ngay lập tức bị phát hiện ở các module khác thông qua lỗi validation hoặc type error trong quá trình phát triển, giúp giảm thiểu rủi ro.
- **Technical Debt:**
  - File `user.py` có vẻ thừa hoặc chưa hoàn thiện, vì hầu hết các schema liên quan đến người dùng đã nằm trong `auth.py`.
  - Cấu trúc file hiện tại khá phẳng. Khi ứng dụng phát triển lớn hơn, có thể cần nhóm chúng vào các thư mục con (ví dụ: `schemas/http`, `schemas/domain`).
