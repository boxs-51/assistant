import time
from tools.v1.gui_control import hotkey, press_key, type_text
from tools.v1.terminal_tool import launch_app
from tools.v1.window_control import close_window, focus_window, list_windows

# 1. Mở ứng dụng Notepad (Chạy ngầm không block luồng)
print("=== 1. MỞ ỨNG DỤNG ===")
print(launch_app("notepad"))
time.sleep(1.5)  # Chờ cửa sổ Notepad khởi tạo thành công

# 2. Liệt kê các cửa sổ đang mở
print("\n=== 2. DANH SÁCH CỬA SỔ ===")
print(list_windows())

# 3. Focus đưa Notepad lên trên cùng
print("\n=== 3. FOCUS CỬA SỔ ===")
print(focus_window("Notepad"))
time.sleep(0.5)

# 4. Gõ văn bản thử nghiệm
print("\n=== 4. BẮT ĐẦU GÕ VĂN BẢN ===")

# Gõ tiêu đề bài viết
print(type_text("BÀI THỬ NGHIỆM TỰ ĐỘNG HÓA AI AGENT"))
print(press_key("enter", presses=2))

# Đoạn văn tiếng Việt dài (Tự động kích hoạt cơ chế Dán Clipboard nhanh & chuẩn dấu 100%)
long_paragraph = (
    "Xin chào! Đây là đoạn văn bản dài thử nghiệm khả năng nhập liệu tự động của hệ thống. \n"
    "Nhờ cơ chế Hybrid phối hợp giữa gõ phím trực tiếp và xử lý bộ nhớ tạm (Clipboard), AI Agent có thể \n"
    "nhập trơn tru toàn bộ văn bản tiếng Việt có dấu, ký tự đặc biệt cũng như Emoji 😊 với tốc độ siêu nhanh. \n"
    "Sau khi nhập xong, bộ nhớ tạm cũ của người dùng cũng được tự động khôi phục hoàn toàn.\n"
)

print(type_text(text=long_paragraph, force_direct=True, interval=0.5))
print(press_key("enter", presses=2))

# Gõ câu kết ngắn
print(type_text(text="--- Kết thúc bài test: Thành công 100% ---"))

# 5. Dừng quan sát và đóng ứng dụng
print("\n=== 5. ĐÓNG ỨNG DỤNG ===")
time.sleep(3)  # Tạm dừng 3 giây để xem kết quả trên màn hình

print(hotkey(["alt", "f4"]))  # Gửi tổ hợp Alt + F4
time.sleep(0.5)
print(press_key("n"))  # Bấm phím 'n' (Don't Save) nếu Notepad hỏi lưu file