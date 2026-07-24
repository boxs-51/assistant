---
name: cpp-commenter
description: Tự động kích hoạt khi chỉnh sửa, tái cấu trúc (refactor) hoặc viết mã C++. Bắt buộc thêm comment chi tiết cho mọi thay đổi code.
---

# Quy tắc Viết Comment C++

Khi tạo mới, sửa đổi hoặc refactor bất kỳ mã nguồn C++ nào, hãy tuân thủ nghiêm ngặt các quy tắc sau:

1. **Giải thích thay đổi (Diff Comments):**
   - Với mỗi đoạn code được sửa hoặc thêm mới, hãy thêm comment giải thích **tại sao** lại thay đổi và **logic** đằng sau thay đổi đó.
   - Dùng định dạng comment ngắn gọn nhưng rõ ràng.

2. **Cấu trúc Comment chuẩn:**
   - **Đầu hàm / Phương thức:** Nhắc lại mục đích, tham số (`@param`), giá trị trả về (`@return`) và các lưu ý về hiệu năng/luồng dữ liệu (thread safety, memory lifecycle nếu có).
   - **Trong thân hàm:** Comment trước các khối lệnh phức tạp, các thuật toán xử lý chính hoặc các đoạn mã tối ưu hóa (optimization).

3. **Ví dụ Minh họa:**

```cpp
// [Thay đổi]: Chuyển sang truyền hằng tham chiếu (const reference) 
// [Lý do]: Tránh việc copy chuỗi không cần thiết, giúp tối ưu hiệu năng bộ nhớ khi gọi hàm liên tục.
void processData(const std::string& inputData) {
    // Kiểm tra điều kiện đầu vào trước khi xử lý logic chính
    if (inputData.empty()) {
        return;
    }

    // ...
}