import unittest
from unittest.mock import patch
import tools.v1.gui_control as gui_control


class TestGUIControl(unittest.TestCase):

    @patch("tools.v1.gui_control.pyautogui")
    def test_mouse_click_mock(self, mock_pyautogui):
        gui_control.pyautogui = mock_pyautogui
        res = gui_control.mouse_click(100, 200, button="left")

        mock_pyautogui.click.assert_called_once_with(
            x=100, y=200, clicks=1, button="left"
        )
        self.assertIn("Thành công", res)

    @patch("tools.v1.gui_control.pyautogui")
    def test_type_text_direct_ascii(self, mock_pyautogui):
        gui_control.pyautogui = mock_pyautogui
        res = gui_control.type_text("Hello World", force_direct=True)

        mock_pyautogui.write.assert_called_once_with(
            "Hello World", interval=0.02
        )
        self.assertIn("Thành công", res)

    @patch("tools.v1.gui_control.pyperclip")
    @patch("tools.v1.gui_control.pyautogui")
    def test_type_text_vietnamese_clipboard(
        self, mock_pyautogui, mock_pyperclip
    ):
        gui_control.pyautogui = mock_pyautogui
        gui_control.pyperclip = mock_pyperclip

        mock_pyperclip.paste.return_value = "clipboard_cu"

        res = gui_control.type_text("Xin chào Việt Nam")

        mock_pyperclip.copy.assert_any_call("Xin chào Việt Nam")
        mock_pyautogui.hotkey.assert_called_once_with("ctrl", "v")
        mock_pyperclip.copy.assert_any_call("clipboard_cu")
        self.assertIn("Thành công: Đã dán", res)

    @patch("tools.v1.gui_control.pyautogui")
    def test_hotkey_mock(self, mock_pyautogui):
        gui_control.pyautogui = mock_pyautogui
        res = gui_control.hotkey(["ctrl", "v"])

        mock_pyautogui.hotkey.assert_called_once_with("ctrl", "v")
        self.assertIn("ctrl + v", res)


if __name__ == "__main__":
    unittest.main()