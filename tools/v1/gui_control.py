import time
from typing import List, Optional, Tuple, Union

try:
    import pyautogui

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.1
except ImportError:
    pyautogui = None

try:
    import pyperclip
except ImportError:
    pyperclip = None

try:
    from pynput.keyboard import Controller as KeyboardController

    keyboard = KeyboardController()
except ImportError:
    keyboard = None


def _check_pyautogui() -> Optional[str]:
    if pyautogui is None:
        return "Lỗi: Thư viện 'pyautogui' chưa cài đặt. Hãy chạy 'pip install pyautogui'."
    return None


def get_screen_info() -> Union[dict, str]:
    """Lấy kích thước màn hình và vị trí chuột hiện tại."""
    err = _check_pyautogui()
    if err:
        return err
    width, height = pyautogui.size()
    x, y = pyautogui.position()
    return {
        "screen_width": width,
        "screen_height": height,
        "mouse_x": x,
        "mouse_y": y,
    }


def mouse_click(
    x: Optional[int] = None,
    y: Optional[int] = None,
    button: str = "left",
    clicks: int = 1,
) -> str:
    """Click chuột tại vị trí (x, y) hoặc tại vị trí hiện tại."""
    err = _check_pyautogui()
    if err:
        return err
    try:
        pyautogui.click(x=x, y=y, clicks=clicks, button=button)
        pos_str = (
            f"tại ({x}, {y})"
            if x is not None and y is not None
            else "tại vị trí hiện tại"
        )
        return f"Thành công: Click {button} {clicks} lần {pos_str}."
    except Exception as e:
        return f"Lỗi điều khiển chuột: {str(e)}"


def mouse_move(x: int, y: int, duration: float = 0.2) -> str:
    """Di chuyển chuột đến tọa độ (x, y)."""
    err = _check_pyautogui()
    if err:
        return err
    try:
        pyautogui.moveTo(x, y, duration=duration)
        return f"Thành công: Đã di chuyển chuột tới ({x}, {y})."
    except Exception as e:
        return f"Lỗi di chuyển chuột: {str(e)}"


def mouse_scroll(clicks: int) -> str:
    """Cuộn chuột lên (số dương) hoặc xuống (số âm)."""
    err = _check_pyautogui()
    if err:
        return err
    try:
        pyautogui.scroll(clicks)
        direction = "lên" if clicks > 0 else "xuống"
        return f"Thành công: Đã cuộn chuột {direction} {abs(clicks)} nấc."
    except Exception as e:
        return f"Lỗi cuộn chuột: {str(e)}"


def type_text(
    text: str,
    force_direct: bool = False,
    restore_clipboard: bool = True,
    interval: float = 0.02,
) -> str:
    """Gõ/Dán chuỗi văn bản vào ứng dụng đang focus (Hỗ trợ Tiếng Việt & Emoji).

    Args:
        text (str): Văn bản cần nhập.
        force_direct (bool): Bắt buộc gõ phím trực tiếp (dùng cho mật khẩu/chuỗi
          ngắn).
        restore_clipboard (bool): Khôi phục dữ liệu Clipboard cũ của người dùng
          sau khi dán.
        interval (float): Độ trễ giữa các phím (khi gõ trực tiếp).
    """
    err = _check_pyautogui()
    if err:
        return err

    if not text:
        return "Lỗi: Chuỗi văn bản rỗng."

    is_unicode = any(ord(char) > 127 for char in text)
    is_long_text = len(text) > 20
    use_paste = (is_unicode or is_long_text) and not force_direct

    if use_paste:
        if pyperclip is None:
            return "Lỗi: Cần cài 'pyperclip' (pip install pyperclip) để dán tiếng Việt."

        try:
            old_clipboard = pyperclip.paste()
            pyperclip.copy(text)
            time.sleep(0.05)

            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.05)

            if restore_clipboard:
                pyperclip.copy(old_clipboard)

            return f"Thành công: Đã dán '{text}' ({len(text)} ký tự)."
        except Exception as e:
            return f"Lỗi dán Clipboard: {str(e)}"

    try:
        if keyboard is not None and is_unicode:
            keyboard.type(text)
        else:
            pyautogui.write(text, interval=interval)
        return f"Thành công: Đã gõ trực tiếp '{text}' ({len(text)} ký tự)."
    except Exception as e:
        return f"Lỗi gõ phím: {str(e)}"


def press_key(key: str, presses: int = 1) -> str:
    """Nhấn phím đơn (ví dụ: 'enter', 'tab', 'backspace', 'esc', 'f5')."""
    err = _check_pyautogui()
    if err:
        return err
    try:
        pyautogui.press(key, presses=presses)
        return f"Thành công: Đã nhấn phím '{key}' {presses} lần."
    except Exception as e:
        return f"Lỗi nhấn phím: {str(e)}"


def hotkey(keys: List[str]) -> str:
    """Thực thi tổ hợp phím (ví dụ: ['ctrl', 'c'], ['alt', 'tab'])."""
    err = _check_pyautogui()
    if err:
        return err
    try:
        pyautogui.hotkey(*keys)
        return f"Thành công: Đã bấm tổ hợp phím {' + '.join(keys)}."
    except Exception as e:
        return f"Lỗi bấm tổ hợp phím: {str(e)}"