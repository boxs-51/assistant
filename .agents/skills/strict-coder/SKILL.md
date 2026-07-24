---
name: strict-coder
description: Kích hoạt khi phân tích hoặc chỉnh sửa mã nguồn để đảm bảo không phá vỡ kiến trúc cũ.
---

# Quy Tắc Viết Code Nâng Cao

1. **Tuân Thủ Kiến Trúc Hiện Tại (Strict Architecture):**
   - KHÔNG ĐƯỢC tự ý tái cấu trúc (refactor) các lớp, cấu trúc thư mục, hoặc thay đổi thiết kế mẫu (Design Pattern) sẵn có trừ khi người dùng yêu cầu rõ ràng.
   - Luôn giữ nguyên style guide,命名 (naming convention) và cách tổ chức file hiện tại của dự án.

2. **Ngắn Gọn & Tập Trung (Concise Output):**
   - Không giải thích dài dòng hoặc lặp lại câu hỏi.
   - Chỉ trả về đoạn code/diff thực sự thay đổi (giữ nguyên logic xung quanh nếu không liên quan).
   - Tránh thêm các thư viện bên ngoài (dependencies) mới nếu thư viện hiện tại trong project đã đáp ứng được.