import unittest
from unittest.mock import MagicMock, patch
import tools.v1.window_control as window_control


class TestWindowControl(unittest.TestCase):

    @patch("tools.v1.window_control.gw")
    def test_list_windows(self, mock_gw):
        mock_gw.getAllTitles.return_value = [
            "Notepad",
            "",
            "   ",
            "Google Chrome",
        ]
        window_control.gw = mock_gw

        result = window_control.list_windows()
        self.assertIsInstance(result, list)
        self.assertEqual(result, ["Notepad", "Google Chrome"])

    @patch("tools.v1.window_control.gw")
    def test_find_windows(self, mock_gw):
        win1 = MagicMock()
        win1.title = "Untitled - Notepad"
        win2 = MagicMock()
        win2.title = "Calculator"

        mock_gw.getAllWindows.return_value = [win1, win2]
        window_control.gw = mock_gw

        result = window_control.find_windows("notepad")
        self.assertEqual(result, ["Untitled - Notepad"])

    @patch("tools.v1.window_control.gw")
    def test_focus_window_success(self, mock_gw):
        mock_win = MagicMock()
        mock_win.title = "Untitled - Notepad"
        mock_win.isMinimized = True

        mock_gw.getWindowsWithTitle.return_value = [mock_win]
        window_control.gw = mock_gw

        res = window_control.focus_window("Notepad")
        mock_win.restore.assert_called_once()
        mock_win.activate.assert_called_once()
        self.assertIn("Thành công", res)

    @patch("tools.v1.window_control.gw")
    def test_close_window_not_found(self, mock_gw):
        mock_gw.getWindowsWithTitle.return_value = []
        mock_gw.getAllWindows.return_value = []
        window_control.gw = mock_gw

        res = window_control.close_window("NonExistentWindow")
        self.assertIn("Không tìm thấy cửa sổ", res)


if __name__ == "__main__":
    unittest.main()