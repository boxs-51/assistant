import time
from typing import Dict, List, Optional, Tuple, Union

try:
    import pyautogui
except ImportError:
    pyautogui = None

try:
    import pyperclip
except ImportError:
    pyperclip = None

try:
    from pynput.keyboard import Controller as KeyboardController
except ImportError:
    KeyboardController = None


TOOL_METADATA = {
    "name": "desktop_automation",
    "description": "Điều khiển tự động hóa các thao tác chuột và bàn phím trên máy tính desktop.",
    "base_risk": "HIGH",  # Tương tác trực tiếp với thiết bị ngoại vi và giao diện OS
    "effects": ["EXECUTE", "EXTERNAL_SIDE_EFFECT"],
    "danger_patterns": [],
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "get_screen_info",
                    "mouse_click",
                    "mouse_move",
                    "mouse_scroll",
                    "type_text",
                    "press_key",
                    "hotkey",
                    "mouse_drag",
                ],
                "description": "Thao tác điều khiển cần thực hiện.",
            },
            "x": {
                "type": "integer",
                "description": "Tọa độ X trên màn hình (dùng cho mouse_click, mouse_move).",
            },
            "y": {
                "type": "integer",
                "description": "Tọa độ Y trên màn hình (dùng cho mouse_click, mouse_move).",
            },
            "start_x": {
                "type": "integer",
                "description": "Tọa độ X bắt đầu kéo (dùng cho mouse_drag).",
            },
            "start_y": {
                "type": "integer",
                "description": "Tọa độ Y bắt đầu kéo (dùng cho mouse_drag).",
            },
            "button": {
                "type": "string",
                "enum": ["left", "right", "middle"],
                "description": "Nút chuột cần click (Mặc định: 'left').",
            },
            "clicks": {
                "type": "integer",
                "description": "Số lần click hoặc số nấc cuộn chuột (dùng cho mouse_click, mouse_scroll).",
            },
            "duration": {
                "type": "number",
                "description": "Thời gian di chuyển chuột tính bằng giây (Mặc định: 0.2).",
            },
            "text": {
                "type": "string",
                "description": "Chuỗi văn bản cần gõ hoặc dán (dùng cho type_text).",
            },
            "force_direct": {
                "type": "boolean",
                "description": "Bắt buộc gõ phím trực tiếp thay vì dán qua Clipboard (dùng cho type_text).",
            },
            "restore_clipboard": {
                "type": "boolean",
                "description": "Khôi phục dữ liệu Clipboard cũ sau khi dán (dùng cho type_text).",
            },
            "interval": {
                "type": "number",
                "description": "Khoảng nghỉ giữa các ký tự khi gõ phím trực tiếp (Mặc định: 0.02).",
            },
            "key": {
                "type": "string",
                "description": "Tên phím đơn cần nhấn (ví dụ: 'enter', 'tab', 'esc') (dùng cho press_key).",
            },
            "presses": {
                "type": "integer",
                "description": "Số lần nhấn phím (dùng cho press_key).",
            },
            "keys": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Danh sách các phím trong tổ hợp phím tắt (ví dụ: ['ctrl', 'c']) (dùng cho hotkey).",
            },
        },
        "required": ["action"],
    },
}

# Chi tiết Metadata tách riêng cho từng sub-action
ACTIONS_METADATA = {
    "get_screen_info": {
        "name": "get_screen_info",
        "description": "Lấy thông tin kích thước màn hình và tọa độ chuột hiện tại.",
        "base_risk": "LOW",
        "danger_patterns": [],
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    "mouse_click": {
        "name": "mouse_click",
        "description": "Thực hiện click chuột tại vị trí (x, y) hoặc vị trí chuột hiện tại.",
        "base_risk": "HIGH",
        "danger_patterns": [],
        "parameters": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Tọa độ X trên màn hình."},
                "y": {"type": "integer", "description": "Tọa độ Y trên màn hình."},
                "button": {
                    "type": "string",
                    "enum": ["left", "right", "middle"],
                    "description": "Nút chuột (Mặc định: 'left').",
                },
                "clicks": {
                    "type": "integer",
                    "description": "Số lần click (Mặc định: 1).",
                },
            },
            "required": [],
        },
    },
    "mouse_move": {
        "name": "mouse_move",
        "description": "Di chuyển con trỏ chuột tới tọa độ (x, y).",
        "base_risk": "LOW",
        "danger_patterns": [],
        "parameters": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Tọa độ X."},
                "y": {"type": "integer", "description": "Tọa độ Y."},
                "duration": {
                    "type": "number",
                    "description": "Thời gian di chuyển tính bằng giây.",
                },
            },
            "required": ["x", "y"],
        },
    },
    "mouse_drag": {
        "name": "mouse_drag",
        "description": "Kéo thả chuột từ vị trí hiện tại (hoặc từ start_x, start_y) đến vị trí đích (x, y).",
        "base_risk": "HIGH",
        "danger_patterns": [],
        "parameters": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Tọa độ X đích."},
                "y": {"type": "integer", "description": "Tọa độ Y đích."},
                "start_x": {"type": "integer", "description": "Tọa độ X bắt đầu (Tùy chọn)."},
                "start_y": {"type": "integer", "description": "Tọa độ Y bắt đầu (Tùy chọn)."},
                "button": {
                    "type": "string",
                    "enum": ["left", "right", "middle"],
                    "description": "Nút chuột giữ khi kéo (Mặc định: 'left').",
                },
                "duration": {
                    "type": "number",
                    "description": "Thời gian kéo chuột tính bằng giây (Mặc định: 0.5).",
                },
            },
            "required": ["x", "y"],
        },
    },
    "mouse_scroll": {
        "name": "mouse_scroll",
        "description": "Cuộn con lăn chuột lên hoặc xuống.",
        "base_risk": "LOW",
        "danger_patterns": [],
        "parameters": {
            "type": "object",
            "properties": {
                "clicks": {
                    "type": "integer",
                    "description": "Số nấc cuộn (Số dương: cuộn lên, Số âm: cuộn xuống).",
                }
            },
            "required": ["clicks"],
        },
    },
    "type_text": {
        "name": "type_text",
        "description": "Gõ hoặc dán chuỗi văn bản vào cửa sổ ứng dụng đang kích hoạt (Hỗ trợ Unicode/Tiếng Việt).",
        "base_risk": "HIGH",
        "danger_patterns": [],
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Nội dung văn bản cần nhập.",
                },
                "force_direct": {
                    "type": "boolean",
                    "description": "Bắt buộc gõ phím trực tiếp thay vì dán Clipboard.",
                },
                "restore_clipboard": {
                    "type": "boolean",
                    "description": "Khôi phục Clipboard sau khi dán.",
                },
                "interval": {
                    "type": "number",
                    "description": "Khoảng thời gian trễ giữa các phím khi gõ trực tiếp.",
                },
            },
            "required": ["text"],
        },
    },
    "press_key": {
        "name": "press_key",
        "description": "Nhấn phím đơn trên bàn phím (ví dụ: 'enter', 'tab', 'backspace', 'esc').",
        "base_risk": "HIGH",
        "danger_patterns": [],
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Tên phím cần nhấn."},
                "presses": {
                    "type": "integer",
                    "description": "Số lần nhấn phím (Mặc định: 1).",
                },
            },
            "required": ["key"],
        },
    },
    "hotkey": {
        "name": "hotkey",
        "description": "Thực thi tổ hợp phím tắt trên bàn phím (ví dụ: ['ctrl', 'c'], ['alt', 'tab']).",
        "base_risk": "HIGH",
        "danger_patterns": [],
        "parameters": {
            "type": "object",
            "properties": {
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Danh sách các phím cấu thành tổ hợp phím tắt.",
                }
            },
            "required": ["keys"],
        },
    },
}

class DesktopAutomation:
    """Class điều khiển tự động hóa thao tác chuột và bàn phím."""

    def __init__(self, failsafe: bool = True, pause: float = 0.1):
        self.pyautogui = pyautogui
        self.pyperclip = pyperclip
        self.keyboard = KeyboardController() if KeyboardController else None

        if self.pyautogui is not None:
            self.pyautogui.FAILSAFE = failsafe
            self.pyautogui.PAUSE = pause

    def _check_pyautogui(self) -> Optional[str]:
        if self.pyautogui is None:
            return "Lỗi: Thư viện 'pyautogui' chưa cài đặt. Hãy chạy 'pip install pyautogui'."
        return None

    def get_screen_info(self) -> Union[dict, str]:
        """Lấy kích thước màn hình và vị trí chuột hiện tại."""
        err = self._check_pyautogui()
        if err:
            return err
        width, height = self.pyautogui.size()
        x, y = self.pyautogui.position()
        return {
            "screen_width": width,
            "screen_height": height,
            "mouse_x": x,
            "mouse_y": y,
        }

    def mouse_click(
        self,
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
        clicks: int = 1,
    ) -> str:
        """Click chuột tại vị trí (x, y) hoặc tại vị trí hiện tại."""
        err = self._check_pyautogui()
        if err:
            return err
        try:
            self.pyautogui.click(x=x, y=y, clicks=clicks, button=button)
            pos_str = (
                f"tại ({x}, {y})"
                if x is not None and y is not None
                else "tại vị trí hiện tại"
            )
            return f"Thành công: Click {button} {clicks} lần {pos_str}."
        except Exception as e:
            return f"Lỗi điều khiển chuột: {str(e)}"

    def mouse_move(self, x: int, y: int, duration: float = 0.2) -> str:
        """Di chuyển chuột đến tọa độ (x, y)."""
        err = self._check_pyautogui()
        if err:
            return err
        try:
            self.pyautogui.moveTo(x, y, duration=duration)
            return f"Thành công: Đã di chuyển chuột tới ({x}, {y})."
        except Exception as e:
            return f"Lỗi di chuyển chuột: {str(e)}"

    def mouse_drag(
        self,
        x: int,
        y: int,
        start_x: Optional[int] = None,
        start_y: Optional[int] = None,
        button: str = "left",
        duration: float = 0.5,
    ) -> str:
        """Kéo giữ chuột đến tọa độ (x, y)."""
        err = self._check_pyautogui()
        if err:
            return err
        try:
            if start_x is not None and start_y is not None:
                self.pyautogui.moveTo(start_x, start_y)
            self.pyautogui.dragTo(x, y, duration=duration, button=button)
            from_str = f"từ ({start_x}, {start_y}) " if start_x is not None and start_y is not None else ""
            return f"Thành công: Đã kéo chuột {from_str}đến ({x}, {y}) bằng nút {button}."
        except Exception as e:
            return f"Lỗi kéo chuột: {str(e)}"


    def mouse_scroll(self, clicks: int) -> str:
        """Cuộn chuột lên (số dương) hoặc xuống (số âm)."""
        err = self._check_pyautogui()
        if err:
            return err
        try:
            self.pyautogui.scroll(clicks)
            direction = "lên" if clicks > 0 else "xuống"
            return f"Thành công: Đã cuộn chuột {direction} {abs(clicks)} nấc."
        except Exception as e:
            return f"Lỗi cuộn chuột: {str(e)}"

    def type_text(
        self,
        text: str,
        force_direct: bool = False,
        restore_clipboard: bool = True,
        interval: float = 0.02,
    ) -> str:
        """Gõ/Dán chuỗi văn bản vào ứng dụng đang focus (Hỗ trợ Tiếng Việt & Emoji)."""
        err = self._check_pyautogui()
        if err:
            return err

        if not text:
            return "Lỗi: Chuỗi văn bản rỗng."

        is_unicode = any(ord(char) > 127 for char in text)
        is_long_text = len(text) > 20
        use_paste = (is_unicode or is_long_text) and not force_direct

        if use_paste:
            if self.pyperclip is None:
                return "Lỗi: Cần cài 'pyperclip' (pip install pyperclip) để dán tiếng Việt."

            try:
                old_clipboard = self.pyperclip.paste()
                self.pyperclip.copy(text)
                time.sleep(0.05)

                self.pyautogui.hotkey("ctrl", "v")
                time.sleep(0.05)

                if restore_clipboard:
                    self.pyperclip.copy(old_clipboard)

                return f"Thành công: Đã dán '{text}' ({len(text)} ký tự)."
            except Exception as e:
                return f"Lỗi dán Clipboard: {str(e)}"

        try:
            if self.keyboard is not None and is_unicode:
                self.keyboard.type(text)
            else:
                self.pyautogui.write(text, interval=interval)
            return f"Thành công: Đã gõ trực tiếp '{text}' ({len(text)} ký tự)."
        except Exception as e:
            return f"Lỗi gõ phím: {str(e)}"

    def press_key(self, key: str, presses: int = 1) -> str:
        """Nhấn phím đơn (ví dụ: 'enter', 'tab', 'backspace', 'esc', 'f5')."""
        err = self._check_pyautogui()
        if err:
            return err
        try:
            self.pyautogui.press(key, presses=presses)
            return f"Thành công: Đã nhấn phím '{key}' {presses} lần."
        except Exception as e:
            return f"Lỗi nhấn phím: {str(e)}"

    def hotkey(self, keys: List[str]) -> str:
        """Thực thi tổ hợp phím (ví dụ: ['ctrl', 'c'], ['alt', 'tab'])."""
        err = self._check_pyautogui()
        if err:
            return err
        try:
            self.pyautogui.hotkey(*keys)
            return f"Thành công: Đã bấm tổ hợp phím {' + '.join(keys)}."
        except Exception as e:
            return f"Lỗi bấm tổ hợp phím: {str(e)}"

    # ------------------------------------------------------------------
    # DISPATCHER / ENTRY POINT (ĐIỀU HƯỚNG BẰNG TÊN ACTION)
    # ------------------------------------------------------------------
    def execute(
        self,
        action: str,
        x: Optional[int] = None,
        y: Optional[int] = None,
        start_x: Optional[int] = None,
        start_y: Optional[int] = None,
        button: str = "left",
        clicks: int = 1,
        duration: float = 0.2,
        text: Optional[str] = None,
        force_direct: bool = False,
        restore_clipboard: bool = True,
        interval: float = 0.02,
        key: Optional[str] = None,
        presses: int = 1,
        keys: Optional[List[str]] = None,
        **kwargs
    ) -> Union[dict, str]:
        """Hàm điều hướng chung cho AI Agent hoặc gọi động theo action."""
        if action in ("get_screen_info", "screen_info", "info"):
            return self.get_screen_info()

        elif action in ("click", "mouse_click"):
            return self.mouse_click(x=x, y=y, button=button, clicks=clicks)

        elif action in ("move", "mouse_move"):
            if x is None or y is None:
                return "Lỗi: Action 'move' yêu cầu hai tham số 'x' và 'y'."
            return self.mouse_move(x=x, y=y, duration=duration)

        elif action in ("drag", "mouse_drag"):
            if x is None or y is None:
                return "Lỗi: Action 'drag' yêu cầu hai tham số 'x' và 'y'."
            return self.mouse_drag(
                x=x,
                y=y,
                start_x=start_x,
                start_y=start_y,
                button=button,
                duration=duration,
            )

        elif action in ("scroll", "mouse_scroll"):
            return self.mouse_scroll(clicks=clicks)

        elif action in ("type", "type_text", "write"):
            if text is None:
                return "Lỗi: Action 'type' yêu cầu tham số 'text'."
            return self.type_text(
                text=text,
                force_direct=force_direct,
                restore_clipboard=restore_clipboard,
                interval=interval,
            )

        elif action in ("press", "press_key"):
            if key is None:
                return "Lỗi: Action 'press' yêu cầu tham số 'key'."
            return self.press_key(key=key, presses=presses)

        elif action in ("hotkey", "shortcut"):
            if not keys:
                return "Lỗi: Action 'hotkey' yêu cầu tham số danh sách 'keys'."
            return self.hotkey(keys=keys)

        else:
            valid_actions = (
                "get_screen_info",
                "mouse_click",
                "mouse_move",
                "mouse_scroll",
                "type_text",
                "press_key",
                "hotkey",
                "mouse_drag",
            )
            return f"Lỗi: Action '{action}' không hợp lệ. Chọn một trong: {valid_actions}"


# ======================================================================
# BẢO TỒN TÍNH TƯƠNG THÍCH NGƯỢC (Hàm Wrappers)
# ======================================================================
_default_desktop_automation = DesktopAutomation()


def get_screen_info() -> Union[dict, str]:
    """Hàm wrapper lấy thông tin màn hình."""
    return _default_desktop_automation.get_screen_info()


def mouse_click(
    x: Optional[int] = None,
    y: Optional[int] = None,
    button: str = "left",
    clicks: int = 1,
) -> str:
    """Hàm wrapper click chuột."""
    return _default_desktop_automation.mouse_click(x=x, y=y, button=button, clicks=clicks)


def mouse_move(x: int, y: int, duration: float = 0.2) -> str:
    """Hàm wrapper di chuyển chuột."""
    return _default_desktop_automation.mouse_move(x=x, y=y, duration=duration)

def mouse_drag(
    x: int,
    y: int,
    start_x: Optional[int] = None,
    start_y: Optional[int] = None,
    button: str = "left",
    duration: float = 0.5,
) -> str:
    """Hàm wrapper kéo chuột."""
    return _default_desktop_automation.mouse_drag(
        x=x,
        y=y,
        start_x=start_x,
        start_y=start_y,
        button=button,
        duration=duration,
    )

def mouse_scroll(clicks: int) -> str:
    """Hàm wrapper cuộn chuột."""
    return _default_desktop_automation.mouse_scroll(clicks=clicks)


def type_text(
    text: str,
    force_direct: bool = False,
    restore_clipboard: bool = True,
    interval: float = 0.02,
) -> str:
    """Hàm wrapper gõ văn bản."""
    return _default_desktop_automation.type_text(
        text=text,
        force_direct=force_direct,
        restore_clipboard=restore_clipboard,
        interval=interval,
    )


def press_key(key: str, presses: int = 1) -> str:
    """Hàm wrapper nhấn phím."""
    return _default_desktop_automation.press_key(key=key, presses=presses)


def hotkey(keys: List[str]) -> str:
    """Hàm wrapper bấm tổ hợp phím."""
    return _default_desktop_automation.hotkey(keys=keys)


def run(action: str, **kwargs) -> Union[dict, str]:
    """Hàm wrapper dạng dispatcher chung."""
    return _default_desktop_automation.execute(action=action, **kwargs)
