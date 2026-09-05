from typing import List, Optional, Union

try:
    import pygetwindow as gw
except ImportError:
    gw = None


class WindowTool:
    """Class quản lý và tương tác với các cửa sổ ứng dụng (PyGetWindow)."""

    def __init__(self):
        pass

    # ------------------------------------------------------------------
    # HELPER: KIỂM TRA THƯ VIỆN & TÌM CỬA SỔ
    # ------------------------------------------------------------------
    def _check_dependency(self) -> Optional[str]:
        """Kiểm tra xem thư viện pygetwindow đã được cài đặt chưa."""
        if gw is None:
            return (
                "Lỗi: Thư viện 'PyGetWindow' chưa được cài đặt. "
                "Vui lòng chạy 'pip install PyGetWindow'."
            )
        return None

    def _get_window_objects(self, title_query: str) -> Union[List[object], str]:
        """Helper tìm kiếm danh sách đối tượng cửa sổ khớp từ khóa (DRY logic)."""
        err = self._check_dependency()
        if err:
            return err

        if not title_query or not title_query.strip():
            return "Lỗi: Từ khóa tìm kiếm cửa sổ không được để trống."

        try:
            # Nếu windows không phải là list (trường hợp MagicMock trong Unit Test)
            # hoặc không tìm thấy kết quả, fallback về gw.getAllWindows()
            windows = gw.getWindowsWithTitle(title_query)
            if not isinstance(windows, list) or not windows:
                # Tìm kiếm chứa chuỗi (không phân biệt hoa/thường)
                all_wins = gw.getAllWindows()
                if isinstance(all_wins, list):
                    windows = [
                        w for w in all_wins
                        if getattr(w, "title", None) and title_query.lower() in w.title.lower()
                    ]
                else:
                    windows = []
            return windows
        except Exception as e:
            return f"Lỗi khi tìm kiếm đối tượng cửa sổ: {str(e)}"

    # ------------------------------------------------------------------
    # 1. LIỆT KÊ CỬA SỔ (LIST)
    # ------------------------------------------------------------------
    def list_windows(self) -> Union[List[str], str]:
        """Lấy danh sách tiêu đề (title) của tất cả cửa sổ đang mở."""
        err = self._check_dependency()
        if err:
            return err

        try:
            titles = [
                title.strip()
                for title in gw.getAllTitles()
                if title and title.strip()
            ]
            return titles
        except Exception as e:
            return f"Lỗi khi lấy danh sách cửa sổ: {str(e)}"

    # ------------------------------------------------------------------
    # 2. TÌM KIẾM CỬA SỔ (FIND)
    # ------------------------------------------------------------------
    def find_windows(self, title_query: str) -> Union[List[str], str]:
        """Tìm kiếm danh sách tiêu đề cửa sổ khớp hoặc chứa từ khóa."""
        windows = self._get_window_objects(title_query)
        if isinstance(windows, str):  # Nếu trả về thông báo lỗi
            return windows

        return [win.title for win in windows if win.title]

    # ------------------------------------------------------------------
    # 3. KÍCH HOẠT / FOCUS CỬA SỔ (FOCUS)
    # ------------------------------------------------------------------
    def focus(self, title_query: str) -> str:
        """Kích hoạt và đưa cửa sổ khớp từ khóa lên phía trước (Foreground)."""
        windows = self._get_window_objects(title_query)
        if isinstance(windows, str):
            return windows

        if not windows:
            return f"Lỗi: Không tìm thấy cửa sổ nào khớp với từ khóa '{title_query}'."

        try:
            win = windows[0]
            if win.isMinimized:
                win.restore()
            win.activate()
            return f"Thành công: Đã kích hoạt (focus) cửa sổ '{win.title}'."
        except Exception as e:
            return f"Lỗi khi kích hoạt cửa sổ '{title_query}': {str(e)}"

    # ------------------------------------------------------------------
    # 4. ĐÓNG CỬA SỔ (CLOSE)
    # ------------------------------------------------------------------
    def close(self, title_query: str) -> str:
        """Đóng cửa sổ chứa từ khóa tiêu đề."""
        windows = self._get_window_objects(title_query)
        if isinstance(windows, str):
            return windows

        if not windows:
            return f"Lỗi: Không tìm thấy cửa sổ nào khớp với từ khóa '{title_query}'."

        try:
            win = windows[0]
            win.close()
            return f"Thành công: Đã gửi lệnh đóng cửa sổ '{win.title}'."
        except Exception as e:
            return f"Lỗi khi đóng cửa sổ '{title_query}': {str(e)}"

    # ------------------------------------------------------------------
    # 5. BỔ SUNG: THU NHỎ / PHÓNG TO (MINIMIZE / MAXIMIZE)
    # ------------------------------------------------------------------
    def minimize(self, title_query: str) -> str:
        """Thu nhỏ cửa sổ xuống thanh Taskbar."""
        windows = self._get_window_objects(title_query)
        if isinstance(windows, str):
            return windows

        if not windows:
            return f"Lỗi: Không tìm thấy cửa sổ nào khớp với từ khóa '{title_query}'."

        try:
            win = windows[0]
            win.minimize()
            return f"Thành công: Đã thu nhỏ cửa sổ '{win.title}'."
        except Exception as e:
            return f"Lỗi khi thu nhỏ cửa sổ '{title_query}': {str(e)}"

    def maximize(self, title_query: str) -> str:
        """Phóng to cửa sổ toàn màn hình."""
        windows = self._get_window_objects(title_query)
        if isinstance(windows, str):
            return windows

        if not windows:
            return f"Lỗi: Không tìm thấy cửa sổ nào khớp với từ khóa '{title_query}'."

        try:
            win = windows[0]
            win.maximize()
            return f"Thành công: Đã phóng to cửa sổ '{win.title}'."
        except Exception as e:
            return f"Lỗi khi phóng to cửa sổ '{title_query}': {str(e)}"

    # ------------------------------------------------------------------
    # DISPATCHER / ENTRY POINT (ĐIỀU HƯỚNG BẰNG TÊN ACTION)
    # ------------------------------------------------------------------
    def execute(
        self,
        action: str,
        title_query: Optional[str] = None,
    ) -> Union[List[str], str]:
        """Hàm điều hướng chung cho AI Agent hoặc gọi động theo action."""
        if action in ("list", "list_windows"):
            return self.list_windows()

        elif action in ("find", "find_windows", "search"):
            if not title_query:
                return "Lỗi: Action 'find' yêu cầu tham số 'title_query'."
            return self.find_windows(title_query=title_query)

        elif action in ("focus", "focus_window", "activate"):
            if not title_query:
                return "Lỗi: Action 'focus' yêu cầu tham số 'title_query'."
            return self.focus(title_query=title_query)

        elif action in ("close", "close_window"):
            if not title_query:
                return "Lỗi: Action 'close' yêu cầu tham số 'title_query'."
            return self.close(title_query=title_query)

        elif action == "minimize":
            if not title_query:
                return "Lỗi: Action 'minimize' yêu cầu tham số 'title_query'."
            return self.minimize(title_query=title_query)

        elif action == "maximize":
            if not title_query:
                return "Lỗi: Action 'maximize' yêu cầu tham số 'title_query'."
            return self.maximize(title_query=title_query)

        else:
            valid_actions = ("list", "find", "focus", "close", "minimize", "maximize")
            return f"Lỗi: Action '{action}' không hợp lệ. Chọn một trong: {valid_actions}"


# ======================================================================
# BẢO TỒN TÍNH TƯƠNG THÍCH NGƯỢC (Hàm Wrappers)
# ======================================================================
_default_window_tool = WindowTool()


def list_windows() -> Union[List[str], str]:
    """Hàm wrapper lấy danh sách cửa sổ (tương thích code cũ)."""
    return _default_window_tool.list_windows()


def find_windows(title_query: str) -> Union[List[str], str]:
    """Hàm wrapper tìm kiếm cửa sổ (tương thích code cũ)."""
    return _default_window_tool.find_windows(title_query=title_query)


def focus_window(title_query: str) -> str:
    """Hàm wrapper kích hoạt cửa sổ (tương thích code cũ)."""
    return _default_window_tool.focus(title_query=title_query)


def close_window(title_query: str) -> str:
    """Hàm wrapper đóng cửa sổ (tương thích code cũ)."""
    return _default_window_tool.close(title_query=title_query)


def window_tool(action: str, **kwargs) -> Union[List[str], str]:
    """Hàm wrapper dạng dispatcher chung."""
    return _default_window_tool.execute(action=action, **kwargs)