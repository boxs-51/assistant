import sys
import unittest
from unittest.mock import MagicMock, patch

# ==================== MOCK CÁC THƯ VIỆN NGOẠI VI TRƯỚC KHIN IMPORT ====================
mock_pyautogui = MagicMock()
mock_pyperclip = MagicMock()
mock_pynput = MagicMock()

sys.modules['pyautogui'] = mock_pyautogui
sys.modules['pyperclip'] = mock_pyperclip
sys.modules['pynput'] = mock_pynput
sys.modules['pynput.keyboard'] = mock_pynput

# Import module cần test sau khi đã mock dependencies
from tools.v1.desktop_tool import (
    DesktopAutomation,
    get_screen_info,
    mouse_click,
    mouse_move,
    mouse_scroll,
    type_text,
    press_key,
    hotkey,
    run,
)


class TestDesktopAutomation(unittest.TestCase):

    def setUp(self):
        """Reset trạng thái của các Mock object trước mỗi testcase."""
        mock_pyautogui.reset_mock()
        mock_pyperclip.reset_mock()
        mock_pynput.reset_mock()
        self.automation = DesktopAutomation(failsafe=True, pause=0.1)

    # ==================== 1. TEST MÀN HÌNH & CHUỘT ====================

    def test_get_screen_info(self):
        """Kiểm tra lấy thông tin màn hình và vị trí con trỏ chuột."""
        mock_pyautogui.size.return_value = (1920, 1080)
        mock_pyautogui.position.return_value = (500, 300)

        info = self.automation.get_screen_info()
        self.assertIsInstance(info, dict)
        self.assertEqual(info["screen_width"], 1920)
        self.assertEqual(info["screen_height"], 1080)
        self.assertEqual(info["mouse_x"], 500)
        self.assertEqual(info["mouse_y"], 300)

    def test_mouse_click_with_coordinates(self):
        """Kiểm tra click chuột tại tọa độ chỉ định."""
        res = self.automation.mouse_click(x=100, y=200, button="left", clicks=2)
        mock_pyautogui.click.assert_called_once_with(x=100, y=200, clicks=2, button="left")
        self.assertIn("Thành công: Click left 2 lần tại (100, 200)", res)

    def test_mouse_click_current_position(self):
        """Kiểm tra click chuột tại vị trí con trỏ hiện tại."""
        res = self.automation.mouse_click(button="right")
        mock_pyautogui.click.assert_called_once_with(x=None, y=None, clicks=1, button="right")
        self.assertIn("tại vị trí hiện tại", res)

    def test_mouse_move(self):
        """Kiểm tra di chuyển con trỏ chuột."""
        res = self.automation.mouse_move(x=400, y=500, duration=0.5)
        mock_pyautogui.moveTo.assert_called_once_with(400, 500, duration=0.5)
        self.assertIn("Đã di chuyển chuột tới (400, 500)", res)

    def test_mouse_scroll(self):
        """Kiểm tra cuộn chuột lên và xuống."""
        res_up = self.automation.mouse_scroll(clicks=5)
        mock_pyautogui.scroll.assert_called_with(5)
        self.assertIn("cuộn chuột lên 5 nấc", res_up)

        res_down = self.automation.mouse_scroll(clicks=-3)
        mock_pyautogui.scroll.assert_called_with(-3)
        self.assertIn("cuộn chuột xuống 3 nấc", res_down)

    # ==================== 2. TEST BÀN PHÍM & VĂN BẢN ====================

    def test_type_text_ascii_direct(self):
        """Kiểm tra gõ trực tiếp chuỗi ASCII ngắn."""
        res = self.automation.type_text(text="Hello World", force_direct=True)
        mock_pyautogui.write.assert_called_once_with("Hello World", interval=0.02)
        self.assertIn("Đã gõ trực tiếp 'Hello World'", res)

    def test_type_text_unicode_clipboard_paste(self):
        """Kiểm tra tự động dán qua Clipboard khi văn bản chứa Tiếng Việt."""
        mock_pyperclip.paste.return_value = "clipboard_cu"
        vietnamese_text = "Xin chào Việt Nam"

        res = self.automation.type_text(text=vietnamese_text, restore_clipboard=True)

        # Kiểm tra quy trình paste: sao chép -> ctrl+v -> khôi phục
        mock_pyperclip.copy.assert_any_call(vietnamese_text)
        mock_pyautogui.hotkey.assert_called_once_with("ctrl", "v")
        mock_pyperclip.copy.assert_called_with("clipboard_cu")
        self.assertIn("Đã dán 'Xin chào Việt Nam'", res)

    def test_type_text_empty_string_error(self):
        """Kiểm tra báo lỗi khi truyền chuỗi rỗng."""
        res = self.automation.type_text(text="")
        self.assertTrue(res.startswith("Lỗi: Chuỗi văn bản rỗng."))

    def test_press_key(self):
        """Kiểm tra nhấn phím đơn."""
        res = self.automation.press_key(key="enter", presses=2)
        mock_pyautogui.press.assert_called_once_with("enter", presses=2)
        self.assertIn("Đã nhấn phím 'enter' 2 lần", res)

    def test_hotkey(self):
        """Kiểm tra bấm tổ hợp phím tắt."""
        res = self.automation.hotkey(keys=["ctrl", "c"])
        mock_pyautogui.hotkey.assert_called_once_with("ctrl", "c")
        self.assertIn("Đã bấm tổ hợp phím ctrl + c", res)

    # ==================== 3. TEST DISPATCHER & EXECUTE ====================

    def test_execute_dispatcher_valid_actions(self):
        """Kiểm tra dispatcher 'execute' điều hướng đúng hàm thành phần."""
        # Test mouse_click qua dispatcher
        self.automation.execute(action="mouse_click", x=10, y=20)
        mock_pyautogui.click.assert_called_with(x=10, y=20, clicks=1, button="left")

        # Test type_text qua dispatcher
        self.automation.execute(action="type_text", text="test")
        mock_pyautogui.write.assert_called_with("test", interval=0.02)

    def test_execute_dispatcher_missing_params(self):
        """Kiểm tra báo lỗi khi thiếu tham số bắt buộc trong dispatcher."""
        res_move = self.automation.execute(action="mouse_move")
        self.assertTrue(res_move.startswith("Lỗi: Action 'move' yêu cầu"))

        res_type = self.automation.execute(action="type_text")
        self.assertTrue(res_type.startswith("Lỗi: Action 'type' yêu cầu"))

    def test_execute_invalid_action(self):
        """Kiểm tra báo lỗi khi truyền action không tồn tại."""
        res = self.automation.execute(action="invalid_action_name")
        self.assertTrue(res.startswith("Lỗi: Action 'invalid_action_name' không hợp lệ."))

    # ==================== 4. TEST WRAPPER FUNCTIONS ====================

    def test_global_wrapper_functions(self):
        """Kiểm tra các hàm wrapper module-level và hàm run()."""
        mouse_move(50, 60)
        mock_pyautogui.moveTo.assert_called_with(50, 60, duration=0.2)

        press_key("esc")
        mock_pyautogui.press.assert_called_with("esc", presses=1)

        run(action="hotkey", keys=["alt", "tab"])
        mock_pyautogui.hotkey.assert_called_with("alt", "tab")


if __name__ == "__main__":
    unittest.main(verbosity=2)