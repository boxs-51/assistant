from typing import List, Union

try:
    import pygetwindow as gw
except ImportError:
    gw = None


def _check_dependency() -> Union[str, None]:
    if gw is None:
        return "Lỗi: Thư viện 'PyGetWindow' chưa được cài đặt. Vui lòng chạy 'pip install PyGetWindow'."
    return None


def list_windows() -> Union[List[str], str]:
    """Lấy danh sách tiêu đề (title) của tất cả cửa sổ đang mở.

    Returns:
        Union[List[str], str]: Danh sách các tiêu đề cửa sổ không rỗng hoặc thông
        báo lỗi.
    """
    err = _check_dependency()
    if err:
        return err

    try:
        titles = [
            title.strip() for title in gw.getAllTitles() if title and title.strip()
        ]
        return titles
    except Exception as e:
        return f"Lỗi khi lấy danh sách cửa sổ: {str(e)}"


def find_windows(title_query: str) -> Union[List[str], str]:
    """Tìm kiếm danh sách tiêu đề cửa sổ khớp hoặc chứa từ khóa.

    Args:
        title_query (str): Từ khóa tiêu đề cần tìm (không phân biệt hoa/thường).

    Returns:
        Union[List[str], str]: Danh sách tiêu đề khớp hoặc thông báo lỗi.
    """
    err = _check_dependency()
    if err:
        return err

    if not title_query or not title_query.strip():
        return "Lỗi: Từ khóa tìm kiếm cửa sổ không được để trống."

    try:
        all_windows = gw.getAllWindows()
        matches = [
            win.title
            for win in all_windows
            if win.title and title_query.lower() in win.title.lower()
        ]
        return matches
    except Exception as e:
        return f"Lỗi khi tìm kiếm cửa sổ: {str(e)}"


def focus_window(title_query: str) -> str:
    """Kích hoạt và đưa cửa sổ khớp từ khóa lên phía trước (Foreground).

    Args:
        title_query (str): Tên hoặc một phần tiêu đề cửa sổ.
    """
    err = _check_dependency()
    if err:
        return err

    try:
        windows = gw.getWindowsWithTitle(title_query)
        if not windows:
            # Thử tìm kiếm không phân biệt hoa thường
            all_wins = gw.getAllWindows()
            windows = [
                w
                for w in all_wins
                if w.title and title_query.lower() in w.title.lower()
            ]

        if not windows:
            return f"Lỗi: Không tìm thấy cửa sổ nào khớp với từ khóa '{title_query}'."

        win = windows[0]
        if win.isMinimized:
            win.restore()
        win.activate()
        return f"Thành công: Đã kích hoạt (focus) cửa sổ '{win.title}'."
    except Exception as e:
        return f"Lỗi khi kích hoạt cửa sổ '{title_query}': {str(e)}"


def close_window(title_query: str) -> str:
    """Đóng cửa sổ chứa từ khóa tiêu đề.

    Args:
        title_query (str): Tên hoặc một phần tiêu đề cửa sổ cần đóng.
    """
    err = _check_dependency()
    if err:
        return err

    try:
        windows = gw.getWindowsWithTitle(title_query)
        if not windows:
            all_wins = gw.getAllWindows()
            windows = [
                w
                for w in all_wins
                if w.title and title_query.lower() in w.title.lower()
            ]

        if not windows:
            return f"Lỗi: Không tìm thấy cửa sổ nào khớp với từ khóa '{title_query}'."

        win = windows[0]
        win.close()
        return f"Thành công: Đã gửi lệnh đóng cửa sổ '{win.title}'."
    except Exception as e:
        return f"Lỗi khi đóng cửa sổ '{title_query}': {str(e)}"