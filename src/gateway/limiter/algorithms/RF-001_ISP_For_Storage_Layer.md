# Kế hoạch Refactor: RF-001 - Tuân thủ ISP cho Storage Layer

**Ngày tạo:** 30/06/2026

**Người thực hiện:** Gemini Code Assist

**Trạng thái:** Đã hoàn thành

---

## 1. Vấn đề (Problem)

Interface `BaseStorage` hiện tại vi phạm Nguyên tắc Phân tách Interface (Interface Segregation Principle - ISP) và làm rò rỉ abstraction.

- **Vi phạm ISP:** `BaseStorage` định nghĩa các phương thức rất cụ thể cho từng thuật toán (`consume_token_bucket`, `consume_sliding_window`). Bất kỳ lớp `Storage` mới nào (ví dụ: `MemcachedStorage`) đều bị buộc phải triển khai tất cả các phương thức này, ngay cả khi nó không hỗ trợ hoặc không cần đến chúng.
- **Rò rỉ Abstraction:** Lớp `Storage` biết quá nhiều về logic nghiệp vụ của lớp `Algorithm`. Nó biết các tham số như `capacity`, `refill_rate`, `window_size`, vốn là chi tiết triển khai của các thuật toán.

## 2. Mục tiêu (Goal)

Refactor lại sự tương tác giữa `Algorithm` và `Storage` để:

- `BaseStorage` tuân thủ ISP bằng cách cung cấp một interface chung, tối giản.
- Loại bỏ hoàn toàn sự phụ thuộc của `Storage` vào logic của `Algorithm`.
- `Storage` chỉ đóng vai trò là một "executor" cho các script hoặc lệnh, làm cho nó trở nên "agnostic" (không biết) về nghiệp vụ.
- Tăng cường tính module hóa, giúp việc thêm các backend `Storage` mới hoặc các `Algorithm` mới trở nên dễ dàng hơn.

## 3. Giải pháp đề xuất (Proposed Solution)

1.  **Thay đổi Interface `BaseStorage`:**
    -   Xóa các phương thức `consume_token_bucket` và `consume_sliding_window`.
    -   Thêm một phương thức trừu tượng duy nhất: `async def execute(script_name: str, keys: list, args: list) -> Any`.

2.  **Cập nhật `RedisStorage`:**
    -   Triển khai phương thức `execute`.
    -   Trong `__init__`, tải tất cả các Lua script vào một dictionary `self.scripts` với key là tên của script (ví dụ: `"token_bucket"`).
    -   Phương thức `execute` sẽ tìm script trong dictionary này và thực thi nó bằng cách sử dụng `script_object(keys=keys, args=args)`.

3.  **Cập nhật các lớp `Algorithm` (`TokenBucketLimiter`, `SlidingWindowLimiter`):**
    -   Trong phương thức `is_allowed`, thay đổi lời gọi từ `self.storage.consume_...` thành `self.storage.execute(...)`.
    -   Truyền vào `script_name` tương ứng và một danh sách các `args` cần thiết cho script đó.
    -   Toàn bộ logic về việc "thuật toán này cần những tham số nào" sẽ được đóng gói hoàn toàn bên trong lớp `Algorithm`.

## 4. Rủi ro & Đánh đổi (Risks & Trade-offs)

- **Rủi ro:** Thấp. Đây là một refactor nội bộ, không làm thay đổi API bên ngoài của `RateLimiterManager`. Cần có unit test đầy đủ để đảm bảo hành vi không thay đổi.
- **Đánh đổi:** Tăng một chút độ phức tạp ban đầu khi phải truyền tên script, nhưng lợi ích về sự tách biệt và khả năng bảo trì trong dài hạn là rất lớn.

---