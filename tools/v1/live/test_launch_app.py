import time
from tools.v1.gui_control import DesktopAutomation
from tools.v1.terminal_tool import launch_app
from tools.v1.window_tools import close_window, focus_window, list_windows

# Khởi tạo đối tượng điều khiển GUI
gui = DesktopAutomation()

# 1. Mở ứng dụng Notepad
print("=== 1. MỞ ỨNG DỤNG ===")
print(launch_app("notepad"))
time.sleep(1.5)

# 2. Liệt kê các cửa sổ
print("\n=== 2. DANH SÁCH CỬA SỔ ===")
print(list_windows())

# 3. Focus Notepad
print("\n=== 3. FOCUS CỬA SỔ ===")
print(focus_window("Notepad"))
time.sleep(0.5)

# 4. Gõ văn bản
print("\n=== 4. BẮT ĐẦU GÕ VĂN BẢN ===")
print(gui.type_text("BÀI THỬ NGHIỆM TỰ ĐỘNG HÓA AI AGENT"))
print(gui.press_key("enter", presses=2))

long_paragraph = (
    "Xin chào! Đây là đoạn văn bản dài thử nghiệm khả năng nhập liệu tự động của hệ thống. \n"
    "Nhờ cơ chế Hybrid phối hợp giữa gõ phím trực tiếp và xử lý bộ nhớ tạm (Clipboard), AI Agent có thể \n"
    "nhập trơn tru toàn bộ văn bản tiếng Việt có dấu, ký tự đặc biệt cũng như Emoji 😊 với tốc độ siêu nhanh. \n"
    "Sau khi nhập xong, bộ nhớ tạm cũ của người dùng cũng được tự động khôi phục hoàn toàn.\n"
)

print(gui.type_text(text=long_paragraph))
print(gui.press_key("enter", presses=2))
print(gui.type_text(text="--- Kết thúc bài test: Thành công 100% ---"))

# 5. Đóng ứng dụng
print("\n=== 5. ĐÓNG ỨNG DỤNG ===")
time.sleep(3)

print(gui.hotkey(["alt", "f4"]))
time.sleep(0.5)
print(gui.press_key("n"))