import ctypes
from ctypes import wintypes
from typing import Dict, List, Optional, Union, Any

try:
    import pygetwindow as gw
except ImportError:
    gw = None

TOOL_METADATA = {
    "name": "window_tool",
    "description": "Công cụ quản lý cửa sổ ứng dụng: Liệt kê, tìm kiếm, kích hoạt (focus), thu nhỏ, phóng to, hoặc đóng cửa sổ đang chạy.",
    "base_risk": "MEDIUM",
    "danger_patterns": [],
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "find", "get_geometry", "focus", "close", "minimize", "maximize"],
                "description": "Hành động: 'list' (liệt kê), 'find' (tìm), 'focus' (kích hoạt lên trước), 'close' (đóng), 'minimize' (thu nhỏ), 'maximize' (phóng to).",
            },
            "title_query": {
                "type": "string",
                "description": "Từ khóa hoặc tên tiêu đề cửa sổ cần thao tác (Bắt buộc với ngoại trừ action='list').",
            },
        },
        "required": ["action"],
    },
}


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]
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
            windows = gw.getWindowsWithTitle(title_query)
            if not isinstance(windows, list) or not windows:
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
            return titles if titles else "Thông báo: Không tìm thấy cửa sổ nào đang mở."
        except Exception as e:
            return f"Lỗi khi lấy danh sách cửa sổ: {str(e)}"

    # ------------------------------------------------------------------
    # 2. TÌM KIẾM CỬA SỔ (FIND)
    # ------------------------------------------------------------------
    def find_windows(self, title_query: str) -> Union[List[str], str]:
        """Tìm kiếm danh sách tiêu đề cửa sổ khớp hoặc chứa từ khóa."""
        windows = self._get_window_objects(title_query)
        if isinstance(windows, str):
            return windows

        matches = [win.title for win in windows if getattr(win, "title", None)]
        if not matches:
            return f"Thông báo: Không tìm thấy cửa sổ nào chứa từ khóa '{title_query}'."
        return matches

    # ------------------------------------------------------------------
    # 3. LẤY TỌA ĐỘ VÀ KÍCH THƯỚC CỬA SỔ (GEOMETRY / BOUNDS)
    # ------------------------------------------------------------------
    def get_geometry(self, title_query: str) -> Union[Dict[str, Any], str]:
        """
        Lấy thông tin chi tiết vị trí, kích thước khung cửa sổ, 
        Client Area (vùng chứa nội dung), Titlebar và Border.
        """
        windows = self._get_window_objects(title_query)
        if isinstance(windows, str):
            return windows

        if not windows:
            return f"Lỗi: Không tìm thấy cửa sổ nào khớp với từ khóa '{title_query}'."

        win = windows[0]

        # 1. Tọa độ tổng thể từ PyGetWindow
        result = {
            "title": getattr(win, "title", ""),
            "overall": {
                "left": win.left,
                "top": win.top,
                "width": win.width,
                "height": win.height,
                "right": win.right,
                "bottom": win.bottom,
            },
            "client_area": None,
            "frame_elements": None
        }

        # 2. Lấy chi tiết Client Area, Titlebar, Border qua Win32 API (Nếu chạy trên Windows)
        hwnd = getattr(win, "_hWnd", None)
        if hwnd:
            try:
                user32 = ctypes.windll.user32

                # Lấy kích thước Client Area (Gốc tọa độ [0,0] tương đối trong lòng cửa sổ)
                client_rect = RECT()
                user32.GetClientRect(hwnd, ctypes.byref(client_rect))
                c_width = client_rect.right - client_rect.left
                c_height = client_rect.bottom - client_rect.top

                # Quy đổi điểm (0,0) của Client Area sang tọa độ màn hình thực tế (Absolute Screen Coordinates)
                pt = wintypes.POINT(0, 0)
                user32.ClientToScreen(hwnd, ctypes.byref(pt))
                c_left = pt.x
                c_top = pt.y

                result["client_area"] = {
                    "left": c_left,
                    "top": c_top,
                    "width": c_width,
                    "height": c_height,
                    "right": c_left + c_width,
                    "bottom": c_top + c_height,
                }

                # Tính toán kích thước Titlebar và viền (Border) dựa trên độ lệch tọa độ
                border_left = c_left - win.left
                titlebar_height = c_top - win.top
                border_right = win.right - (c_left + c_width)
                border_bottom = win.bottom - (c_top + c_height)

                result["frame_elements"] = {
                    "titlebar_height": titlebar_height,  # Chiều cao thanh tiêu đề (+ viền trên)
                    "border_left": border_left,          # Viền trái
                    "border_right": border_right,        # Viền phải
                    "border_bottom": border_bottom       # Viền dưới
                }
            except Exception as e:
                result["note"] = f"Không thể lấy chi tiết Client/Border qua Win32 API: {str(e)}"

        return result

    # ------------------------------------------------------------------
    # 4. KÍCH HOẠT / FOCUS CỬA SỔ (FOCUS)
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
            if getattr(win, "isMinimized", False):
                win.restore()
            win.activate()
            return f"Thành công: Đã kích hoạt (focus) cửa sổ '{win.title}'."
        except Exception as e:
            return f"Lỗi khi kích hoạt cửa sổ '{title_query}': {str(e)}"

    # ------------------------------------------------------------------
    # 5. ĐÓNG CỬA SỔ (CLOSE)
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
    # 6. THU NHỎ / PHÓNG TO (MINIMIZE / MAXIMIZE)
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
    # DISPATCHER / ENTRY POINT
    # ------------------------------------------------------------------
    def execute(
        self,
        action: str,
        title_query: Optional[str] = None,
        **kwargs  # Tiếp nhận và bỏ qua các tham số thừa từ ToolExecutor
    ) -> Union[List[str], str]:
        """Hàm điều hướng chung hỗ trợ gọi động theo action."""
        # Dung hòa tên tham số từ LLM (title_query, title, query)
        target_title = title_query or kwargs.get("title") or kwargs.get("query")

        if action in ("list", "list_windows"):
            return self.list_windows()

        elif action in ("find", "find_windows", "search"):
            if not target_title:
                return "Lỗi: Action 'find' yêu cầu tham số 'title_query'."
            return self.find_windows(title_query=target_title)

        elif action in ("get_geometry", "geometry", "get_bounds", "info"):
            if not target_title:
                return "Lỗi: Action 'get_geometry' yêu cầu tham số 'title_query'."
            return self.get_geometry(title_query=target_title)

        elif action in ("focus", "focus_window", "activate"):
            if not target_title:
                return "Lỗi: Action 'focus' yêu cầu tham số 'title_query'."
            return self.focus(title_query=target_title)

        elif action in ("close", "close_window"):
            if not target_title:
                return "Lỗi: Action 'close' yêu cầu tham số 'title_query'."
            return self.close(title_query=target_title)

        elif action == "minimize":
            if not target_title:
                return "Lỗi: Action 'minimize' yêu cầu tham số 'title_query'."
            return self.minimize(title_query=target_title)

        elif action == "maximize":
            if not target_title:
                return "Lỗi: Action 'maximize' yêu cầu tham số 'title_query'."
            return self.maximize(title_query=target_title)

        else:
            valid_actions = ("list", "find", "get_geometry", "focus", "close", "minimize", "maximize")
            return f"Lỗi: Action '{action}' không hợp lệ. Chọn một trong các thao tác: {valid_actions}"


# ======================================================================
# BẢO TỒN TÍNH TƯƠNG THÍCH NGƯỢC (Hàm Wrappers)
# ======================================================================
_default_window_tool = WindowTool()


def list_windows() -> Union[List[str], str]:
    return _default_window_tool.list_windows()


def find_windows(title_query: str) -> Union[List[str], str]:
    return _default_window_tool.find_windows(title_query=title_query)


def focus_window(title_query: str) -> str:
    return _default_window_tool.focus(title_query=title_query)


def close_window(title_query: str) -> str:
    return _default_window_tool.close(title_query=title_query)


def run(action: str, **kwargs) -> Union[List[str], str]:
    """Hàm entrypoint chuẩn tương thích hoàn toàn với LocalToolManager & ToolExecutor."""
    return _default_window_tool.execute(action=action, **kwargs)
