---
name: architecture-discovery
description: Tự động đảo ngược kiến trúc (Reverse-Engineering) trực tiếp từ Code khi gặp các thành phần lạ (Unknown Component), module mới chưa có tài liệu, hoặc khi chạy Learning Mode.
---

# Quy Trình Khám Phá Kiến Trúc (Dynamic Architecture Discovery)

Khi gặp module/thành phần hoàn toàn chưa có tài liệu `.md` (như `EventQueue`, `AudioMixer`), kích hoạt quy trình tái cấu trúc kiến trúc động theo 5 bước:

## 🕵️‍♂️ Workflow Tái Cấu Trúc Kiến Trúc:

### Bước 1: Xử Lý Thành Phần Lạ (Unknown Component Detection)
Đánh giá độ tin cậy và gán thẻ thành phần chưa xác định:

🔍 **Unknown Component Detected:** [Tên_Class_Hoặc_Module]
- **Possible Parent:** [Module cha dự đoán dựa trên include/namespace]
- **Confidence:** [80% - 95%]

## Bước 2: Phân Tích Mã Nguồn Thô (Raw Code Analysis)
Chạy phân tích sâu qua mã nguồn để trích xuất 5 trụ cột:

- **Dependency:** Khai báo #include, kế thừa, composition.

- **Ownership & Lifetime:** Con trỏ std::unique_ptr, std::shared_ptr, hàm Init() / Destroy().

- **Thread Model:** Các điểm gọi Mutex, Lock-free queues, Thread ID.

- **Data & Event Flow:** Luồng biến số, callback, event loop.

- **Public API:** Danh sách hàm public chính.

### Bước 3: Sinh Kiến Trúc Tạm Thời (Temporary Architecture)
Tạo file tài liệu tạm thời tại thư mục .temp/ để không làm ô nhiễm bộ nhớ chính:

- **Đường dẫn:** .temp/[Module_Name]/TEMP_FOLDER_INFO.md

### Bước 4: Khai Thác Bối Cảnh (Context Handover)
- Chuyển tiếp cấu trúc trong TEMP_FOLDER_INFO.md cho công cụ Debug hoặc Feature Design tiếp tục xử lý.

### Bước 5: Đề Xuất Tích Lũy Tri Thức (Knowledge Integration)
Sau khi hoàn thành tác vụ chính, chủ động hỏi người dùng:

- "Tôi đã khám phá xong kiến trúc module [Tên_Module]. Bạn có muốn lưu cấu trúc này thành file FOLDER_INFO.md chính thức và cập nhật vào ARCHITECTURE_INDEX.md không?"