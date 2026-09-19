import unittest
from unittest.mock import MagicMock, patch
import tools.v1.window_tool
from tools.v1.window_tool import WindowTool, run


class TestWindowTool(unittest.TestCase):

    def setUp(self):
        """Khởi tạo đối tượng WindowTool trước mỗi test case."""
        self.tool = WindowTool()

    # ==================== 1. TEST KIỂM TRA DEPENDENCY ====================

    @patch("tools.v1.window_tool.pwc", None)
    def test_dependency_missing(self):
        """Kiểm tra báo lỗi khi chưa cài đặt thư viện pywinctl."""
        err = self.tool._check_dependency()
        self.assertIn("Lỗi: Thư viện 'pywinctl' chưa được cài đặt", err)

        # Kiểm tra qua hàm list_windows
        res = self.tool.list_windows()
        self.assertIn("Lỗi: Thư viện 'pywinctl' chưa được cài đặt", res)

    # ==================== 2. TEST HELPER _GET_WINDOW_OBJECTS ====================

    @patch("tools.v1.window_tool.pwc")
    def test_get_window_objects_empty_query(self, mock_pwc):
        """Kiểm tra báo lỗi khi query tìm kiếm trống."""
        res = self.tool._get_window_objects("")
        self.assertEqual(res, "Lỗi: Từ khóa tìm kiếm cửa sổ không được để trống.")

    @patch("tools.v1.window_tool.pwc")
    def test_get_window_objects_search(self, mock_pwc):
        """Kiểm tra tìm kiếm cửa sổ thông qua getWindowsWithTitle của pywinctl."""
        mock_win = MagicMock()
        mock_win.title = "Notepad - Draft.txt"
        mock_pwc.getWindowsWithTitle.return_value = [mock_win]

        wins = self.tool._get_window_objects("Notepad")
        self.assertEqual(len(wins), 1)
        self.assertEqual(wins[0].title, "Notepad - Draft.txt")
        mock_pwc.getWindowsWithTitle.assert_called_once()

    # ==================== 3. TEST LIỆT KÊ CỬA SỔ (LIST) ====================

    @patch("tools.v1.window_tool.pwc")
    def test_list_windows_success(self, mock_pwc):
        """Kiểm tra lấy danh sách tiêu đề cửa sổ thành công."""
        mock_pwc.getAllTitles.return_value = ["  Chrome  ", "", "Notepad", "   "]
        titles = self.tool.list_windows()
        self.assertEqual(titles, ["Chrome", "Notepad"])

    @patch("tools.v1.window_tool.pwc")
    def test_list_windows_empty(self, mock_pwc):
        """Kiểm tra thông báo khi không có cửa sổ nào."""
        mock_pwc.getAllTitles.return_value = ["", "   "]
        res = self.tool.list_windows()
        self.assertIn("Không tìm thấy cửa sổ nào đang mở", res)

    # ==================== 4. TEST TÌM KIẾM CỬA SỔ (FIND) ====================

    @patch.object(WindowTool, "_get_window_objects")
    def test_find_windows_success(self, mock_get_objs):
        """Kiểm tra tìm danh sách tiêu đề cửa sổ khớp từ khóa."""
        mock_win = MagicMock(title="Terminal - Bash")
        mock_get_objs.return_value = [mock_win]

        matches = self.tool.find_windows("Terminal")
        self.assertEqual(matches, ["Terminal - Bash"])

    # ==================== 5. TEST TỌA ĐỘ VÀ CLIENT AREA (GEOMETRY) ====================

    @patch.object(WindowTool, "_get_window_objects")
    def test_get_geometry_with_client_area(self, mock_get_objs):
        """Kiểm tra lấy thông tin tọa độ tổng thể và Client Area từ pywinctl."""
        mock_win = MagicMock()
        mock_win.title = "Calculator"
        mock_win.left = 100
        mock_win.top = 100
        mock_win.width = 400
        mock_win.height = 500
        mock_win.right = 500
        mock_win.bottom = 600

        # Mock Client Area Object từ pywinctl
        mock_client = MagicMock()
        mock_client.left = 105
        mock_client.top = 130
        mock_client.width = 390
        mock_client.height = 460
        mock_client.right = 495
        mock_client.bottom = 590
        mock_win.client = mock_client

        mock_get_objs.return_value = [mock_win]

        res = self.tool.get_geometry("Calculator")
        self.assertIsInstance(res, dict)
        self.assertEqual(res["title"], "Calculator")
        self.assertEqual(res["overall"]["width"], 400)
        self.assertEqual(res["client_area"]["width"], 390)
        self.assertEqual(res["frame_elements"]["titlebar_height"], 30)  # 130 - 100
        self.assertEqual(res["frame_elements"]["border_left"], 5)       # 105 - 100

    # ==================== 6. TEST THAO TÁC (FOCUS, CLOSE, MIN/MAX) ====================

    @patch.object(WindowTool, "_get_window_objects")
    def test_focus_restores_and_activates(self, mock_get_objs):
        """Kiểm tra focus tự động restore nếu cửa sổ đang bị minimize."""
        mock_win = MagicMock(title="VSCode", isMinimized=True)
        mock_get_objs.return_value = [mock_win]

        res = self.tool.focus("VSCode")
        mock_win.restore.assert_called_once()
        mock_win.activate.assert_called_once()
        self.assertIn("Thành công: Đã kích hoạt (focus) cửa sổ 'VSCode'", res)

    @patch.object(WindowTool, "_get_window_objects")
    def test_close_window(self, mock_get_objs):
        """Kiểm tra đóng cửa sổ thành công."""
        mock_win = MagicMock(title="Calculator")
        mock_get_objs.return_value = [mock_win]

        res = self.tool.close("Calculator")
        mock_win.close.assert_called_once()
        self.assertIn("Thành công: Đã gửi lệnh đóng cửa sổ 'Calculator'", res)

    @patch.object(WindowTool, "_get_window_objects")
    def test_minimize_and_maximize(self, mock_get_objs):
        """Kiểm tra thu nhỏ và phóng to cửa sổ."""
        mock_win = MagicMock(title="Paint")
        mock_get_objs.return_value = [mock_win]

        res_min = self.tool.minimize("Paint")
        mock_win.minimize.assert_called_once()
        self.assertIn("Thành công: Đã thu nhỏ cửa sổ 'Paint'", res_min)

        res_max = self.tool.maximize("Paint")
        mock_win.maximize.assert_called_once()
        self.assertIn("Thành công: Đã phóng to cửa sổ 'Paint'", res_max)

    # ==================== 7. TEST DISPATCHER & ALIASES ====================

    @patch.object(WindowTool, "focus", return_value="focused")
    def test_execute_alias_and_kwargs(self, mock_focus):
        """Kiểm tra Dispatcher dung hòa tham số title/query và alias 'activate'."""
        res = self.tool.execute(action="activate", query="Sublime Text")
        self.assertEqual(res, "focused")
        mock_focus.assert_called_once_with(title_query="Sublime Text")

    def test_execute_invalid_action(self):
        """Kiểm tra xử lý action không hợp lệ."""
        res = self.tool.execute(action="invalid_act")
        self.assertIn("Lỗi: Action 'invalid_act' không hợp lệ", res)


if __name__ == "__main__":
    unittest.main(verbosity=2)