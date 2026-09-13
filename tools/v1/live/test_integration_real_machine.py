import datetime
import os
import sys
import time
import unittest
import warnings

# Import các công cụ thực thi
from tools.desktop_tool import DesktopAutomation
from tools.terminal_tool import TerminalTool
from tools.window_tool import WindowTool


class TestRealMachineIntegration(unittest.TestCase):
    # Cờ kiểm soát trạng thái Pipeline (Nếu True -> Ngắt toàn bộ các bước sau)
    _pipeline_failed = False

    # -------------------------------------------------------------------------
    # CẤU HÌNH ĐƯỜNG DẪN LÀM VIỆC & NHẬT KÝ KIỂM THỬ (LOGS)
    # -------------------------------------------------------------------------
    BASE_DIR = r"D:\client_name"
    LOG_DIR = os.path.join(BASE_DIR, "logs", "test_integration_real_machine")
    LOG_FILE = os.path.join(LOG_DIR, "pipeline_execution.log")

    @classmethod
    def setUpClass(cls):
        """Thiết lập môi trường làm việc, khởi tạo thư mục log và các công cụ thực thi."""
        # Chuyển về thư mục làm việc gốc D:\client_name nếu tồn tại
        if os.path.exists(cls.BASE_DIR):
            os.chdir(cls.BASE_DIR)

        # Khởi tạo thư mục và file log
        os.makedirs(cls.LOG_DIR, exist_ok=True)
        with open(cls.LOG_FILE, "w", encoding="utf-8") as f:
            f.write(
                f"=== KHỞI TẠO PIPELINE REAL MACHINE INTEGRATION TEST [{datetime.datetime.now()}] ===\n"
            )
            f.write(f"Thư mục làm việc: {os.getcwd()}\n\n")

        # Khởi tạo các công cụ
        cls.window_tool = WindowTool()
        cls.terminal_tool = TerminalTool()
        cls.desktop_tool = DesktopAutomation()

        # Bỏ qua ResourceWarning từ subprocess ngầm của Windows/Python
        warnings.filterwarnings("ignore", category=ResourceWarning)

        # Xác định ứng dụng test và phím modifier tùy theo hệ điều hành
        if sys.platform.startswith("win"):
            cls.app_command = "notepad.exe"
            cls.app_title_keyword = "Notepad"
            cls.modifier_key = "ctrl"
        elif sys.platform.startswith("linux"):
            cls.app_command = "gedit"
            cls.app_title_keyword = "Text Editor"
            cls.modifier_key = "ctrl"
        else:
            cls.skipTest(
                cls, "Chưa hỗ trợ hệ điều hành này cho Real Machine Test."
            )

    def log(self, message: str):
        """Ghi log đồng thời ra màn hình terminal và file log."""
        print(message)
        with open(self.LOG_FILE, "a", encoding="utf-8") as f:
            f.write(message + "\n")

    def setUp(self):
        """Kiểm tra cờ pipeline trước mỗi testcase. Hủy ngay nếu có bước trước đã lỗi."""
        if TestRealMachineIntegration._pipeline_failed:
            self.skipTest(
                "[PIPELINE ABORTED] Đã dừng do bước trước đó gặp lỗi."
            )

    def tearDown(self):
        """Nếu testcase hiện tại lỗi, bật cờ đánh dấu pipeline đã hỏng và ghi log."""
        if hasattr(self, "_outcome"):
            result = self._outcome.result
            if result.failures or result.errors:
                TestRealMachineIntegration._pipeline_failed = True
                self.log(
                    f"\n[!] PIPELINE FAILURE: Test '{self._testMethodName}' gặp lỗi. Đã hủy các test còn lại."
                )

    @classmethod
    def tearDownClass(cls):
        """Dọn dẹp triệt để cửa sổ và tổng kết trạng thái sau khi chạy xong pipeline."""
        try:
            if hasattr(cls.window_tool, "execute"):
                cls.window_tool.execute(
                    action="close", title_query=cls.app_title_keyword
                )
            elif hasattr(cls.window_tool, "close"):
                cls.window_tool.close(title_query=cls.app_title_keyword)
        except Exception:
            pass

        print("\n" + "=" * 60)
        if cls._pipeline_failed:
            print(
                "❌ PIPELINE THẤT BẠI: Quá trình test đã bị ngắt ngay khi gặp lỗi."
            )
        else:
            print(
                "✅ PIPELINE THÀNH CÔNG: Hoàn thành toàn bộ kịch bản kiểm thử tích hợp trên máy thật!"
            )
        print(f"📄 Log quá trình: {cls.LOG_FILE}")
        print("=" * 60)

    def _mark_failed_and_stop(self, step_name: str, message: str):
        """Đánh dấu lỗi, ghi log ngắt pipeline và dừng unittest."""
        error_msg = f"❌ [{step_name}] LỖI NGẮT PIPELINE: {message}"
        self.log(error_msg)
        TestRealMachineIntegration._pipeline_failed = True
        self.fail(error_msg)

    # =========================================================================
    # HELPER METHODS
    # =========================================================================
    def _exec_terminal(self, action="run", command=None, **kwargs):
        """Gọi TerminalTool tương thích chuẩn Metadata hoặc method sẵn có."""
        if hasattr(self.terminal_tool, "execute"):
            return self.terminal_tool.execute(
                action=action, command=command, **kwargs
            )
        elif action == "launch" and hasattr(self.terminal_tool, "launch"):
            return self.terminal_tool.launch(command=command, **kwargs)
        elif action == "run" and hasattr(self.terminal_tool, "run"):
            return self.terminal_tool.run(command=command, **kwargs)
        else:
            raise AttributeError(
                f"TerminalTool không hỗ trợ action '{action}'"
            )

    def _exec_window(self, action, title_query=None, **kwargs):
        """Gọi WindowTool tương thích chuẩn Metadata hoặc method sẵn có."""
        if hasattr(self.window_tool, "execute"):
            params = {"action": action}
            if title_query is not None:
                params["title_query"] = title_query
            params.update(kwargs)
            return self.window_tool.execute(**params)
        elif action == "find" and hasattr(self.window_tool, "find_windows"):
            return self.window_tool.find_windows(title_query=title_query)
        elif hasattr(self.window_tool, action):
            method = getattr(self.window_tool, action)
            return (
                method(title_query=title_query, **kwargs)
                if title_query
                else method(**kwargs)
            )
        else:
            raise AttributeError(f"WindowTool không hỗ trợ action '{action}'")

    def _get_window_rect(self, title_query):
        """Parse chính xác tọa độ (x, y, w, h) từ cấu trúc Geometry lồng nhau."""
        geo = self._exec_window(action="get_geometry", title_query=title_query)
        if isinstance(geo, dict):
            target = geo.get("overall", geo)
            if isinstance(target, dict):
                win_x = target.get("left", target.get("x", 100))
                win_y = target.get("top", target.get("y", 100))
                win_w = target.get(
                    "width", target.get("right", win_x + 600) - win_x
                )
                win_h = target.get(
                    "height", target.get("bottom", win_y + 400) - win_y
                )
                return win_x, win_y, win_w, win_h
        elif isinstance(geo, (list, tuple)) and len(geo) >= 4:
            return geo[0], geo[1], geo[2], geo[3]
        return 100, 100, 600, 400

    # =========================================================================
    # PIPELINE CÁC BƯỚC THỰC THI KIỂM THỬ REAL MACHINE
    # =========================================================================

    def test_01_terminal_tool_operations(self):
        """Bước 1: Kiểm tra toàn bộ chức năng của TerminalTool."""
        self.log("\n--- [TEST 1] TerminalTool Operations ---")

        cmd_dir = "dir" if sys.platform.startswith("win") else "ls"
        self.log(f"-> Chạy lệnh terminal: '{cmd_dir}'...")
        exec_res = self._exec_terminal(action="run", command=cmd_dir)
        self.log(f"   Kết quả: {str(exec_res)[:100]}...")
        if exec_res is None:
            self._mark_failed_and_stop(
                "TEST_01", "Thực thi lệnh terminal trả về None."
            )

        self.log(f"-> Khởi chạy ứng dụng: '{self.app_command}'...")
        launch_res = self._exec_terminal(
            action="launch", command=self.app_command
        )
        self.log(f"   Kết quả launch: {launch_res}")

        window_found = False
        for _ in range(20):
            time.sleep(0.5)
            wins = self._exec_window(
                action="find", title_query=self.app_title_keyword
            )
            if isinstance(wins, list) and len(wins) > 0:
                window_found = True
                break

        if not window_found:
            self._mark_failed_and_stop(
                "TEST_01",
                f"Ứng dụng '{self.app_command}' không xuất hiện giao diện sau khi launch.",
            )

    def test_02_window_tool_operations(self):
        """Bước 2: Kiểm tra toàn bộ chức năng của WindowTool."""
        self.log("\n--- [TEST 2] WindowTool Operations ---")

        self.log(f"-> Tìm cửa sổ với từ khóa '{self.app_title_keyword}'...")
        windows = self._exec_window(
            action="find", title_query=self.app_title_keyword
        )
        self.log(
            f"   Tìm thấy {len(windows) if isinstance(windows, list) else 0} cửa sổ."
        )
        if not (isinstance(windows, list) and len(windows) > 0):
            self._mark_failed_and_stop(
                "TEST_02", "Không tìm thấy cửa sổ ứng dụng."
            )

        fake_windows = self._exec_window(
            action="find", title_query="NonExistentWindow_12345"
        )
        self.assertIn("Không tìm thấy cửa sổ", str(fake_windows))

        self.log("-> Focus cửa sổ ứng dụng...")
        focus_res = self._exec_window(
            action="focus", title_query=self.app_title_keyword
        )
        self.assertIn("Thành công", str(focus_res))
        time.sleep(0.5)

        self.log("-> Thu nhỏ (Minimize) cửa sổ...")
        min_res = self._exec_window(
            action="minimize", title_query=self.app_title_keyword
        )
        self.assertIn("Thành công", str(min_res))
        time.sleep(0.5)

        self.log("-> Phóng to (Maximize) cửa sổ...")
        max_res = self._exec_window(
            action="maximize", title_query=self.app_title_keyword
        )
        self.assertIn("Thành công", str(max_res))
        time.sleep(0.5)

        self._exec_window(
            action="restore", title_query=self.app_title_keyword
        )
        self._exec_window(action="focus", title_query=self.app_title_keyword)
        time.sleep(0.5)

    def test_03_desktop_mouse_operations(self):
        """Bước 3: Kiểm tra các thao tác chuột bên trong lòng cửa sổ."""
        self.log(
            "\n--- [TEST 3] DesktopAutomation - Mouse Operations In-Canvas ---"
        )

        self._exec_window(action="focus", title_query=self.app_title_keyword)
        time.sleep(0.5)

        win_x, win_y, win_w, win_h = self._get_window_rect(
            self.app_title_keyword
        )
        center_x = win_x + (win_w // 2)
        center_y = win_y + (win_h // 2)
        self.log(
            f"-> RECT cửa sổ thực tế: x={win_x}, y={win_y}, w={win_w}, h={win_h}"
        )
        self.log(f"-> Tọa độ trung tâm vùng làm việc: ({center_x}, {center_y})")

        self.log(
            f"-> Di chuyển chuột vào lòng cửa sổ tại ({center_x}, {center_y})..."
        )
        move_res = self.desktop_tool.execute(
            action="mouse_move", x=center_x, y=center_y, duration=0.2
        )
        self.assertIn("Thành công", str(move_res))

        self.log("-> Click chuột trái vào cửa sổ...")
        click_res = self.desktop_tool.execute(
            action="mouse_click", button="left", clicks=1
        )
        self.assertIn("Thành công", str(click_res))

        self.log("-> Kéo thả chuột (drag) nội bộ trong cửa sổ...")
        drag_res1 = self.desktop_tool.execute(
            action="mouse_drag",
            x=center_x + 80,
            y=center_y,
            button="left",
            duration=0.3,
        )
        self.assertIn("Thành công", str(drag_res1))

        self.log("-> Cuộn chuột trong cửa sổ...")
        scroll_res = self.desktop_tool.execute(
            action="mouse_scroll", clicks=-300
        )
        self.assertIn("Thành công", str(scroll_res))

    def test_03b_desktop_mouse_window_controls(self):
        """Bước 3B: Thao tác điều khiển cửa sổ trực tiếp bằng chuột."""
        self.log(
            "\n--- [TEST 3B] DesktopAutomation - Window Control Via Mouse ---"
        )

        self._exec_window(
            action="restore", title_query=self.app_title_keyword
        )
        self._exec_window(action="focus", title_query=self.app_title_keyword)
        time.sleep(0.5)

        win_x, win_y, win_w, win_h = self._get_window_rect(
            self.app_title_keyword
        )

        titlebar_x = win_x + (win_w // 2)
        titlebar_y = max(win_y + 15, 15)
        self.log(
            f"-> Nhấn đúp chuột vào Titlebar tại ({titlebar_x}, {titlebar_y}) để Phóng to..."
        )
        self.desktop_tool.execute(
            action="mouse_click",
            x=titlebar_x,
            y=titlebar_y,
            button="left",
            clicks=2,
        )
        time.sleep(0.8)

        win_x, win_y, win_w, win_h = self._get_window_rect(
            self.app_title_keyword
        )
        titlebar_x = win_x + (win_w // 2)
        titlebar_y = max(win_y + 15, 15)
        self.log(
            f"-> Nhấn đúp chuột lại vào Titlebar tại ({titlebar_x}, {titlebar_y}) để Thu nhỏ về bình thường..."
        )
        self.desktop_tool.execute(
            action="mouse_click",
            x=titlebar_x,
            y=titlebar_y,
            button="left",
            clicks=2,
        )
        time.sleep(0.8)

        win_x, win_y, win_w, win_h = self._get_window_rect(
            self.app_title_keyword
        )
        start_title_x = win_x + 200
        start_title_y = max(win_y + 15, 15)
        target_title_x = start_title_x + 100
        target_title_y = start_title_y + 100

        self.log(
            f"-> Kéo thả Titlebar từ ({start_title_x}, {start_title_y}) tới ({target_title_x}, {target_title_y}) để DI CHUYỂN cửa sổ..."
        )
        self.desktop_tool.execute(
            action="mouse_drag",
            start_x=start_title_x,
            start_y=start_title_y,
            x=target_title_x,
            y=target_title_y,
            button="left",
            duration=0.5,
        )
        time.sleep(0.8)

        win_x, win_y, win_w, win_h = self._get_window_rect(
            self.app_title_keyword
        )
        corner_x = win_x + win_w - 3
        corner_y = win_y + win_h - 3
        resize_target_x = corner_x + 60
        resize_target_y = corner_y + 60

        self.log(
            f"-> Kéo góc cửa sổ từ ({corner_x}, {corner_y}) sang ({resize_target_x}, {resize_target_y}) để RESIZE..."
        )
        self.desktop_tool.execute(
            action="mouse_drag",
            start_x=corner_x,
            start_y=corner_y,
            x=resize_target_x,
            y=resize_target_y,
            button="left",
            duration=0.5,
        )
        time.sleep(0.8)

        if sys.platform.startswith("win"):
            win_x, win_y, win_w, win_h = self._get_window_rect(
                self.app_title_keyword
            )
            btn_min_x = win_x + win_w - 130
            btn_min_y = win_y + 15
            self.log(
                f"-> Click nút Minimize tại tọa độ ({btn_min_x}, {btn_min_y})..."
            )
            self.desktop_tool.execute(
                action="mouse_click", x=btn_min_x, y=btn_min_y, button="left"
            )
            time.sleep(0.8)

            self._exec_window(
                action="focus", title_query=self.app_title_keyword
            )
            time.sleep(0.5)

    def test_04_desktop_keyboard_operations(self):
        """Bước 4: Kiểm tra thao tác bàn phím (Type Text, Press Key, Hotkey)."""
        self.log("\n--- [TEST 4] DesktopAutomation - Keyboard Operations ---")

        self._exec_window(action="focus", title_query=self.app_title_keyword)
        time.sleep(0.5)

        clip_text = (
            "Tiếng Việt có dấu: Cập nhật tính năng kéo thả chuột mouse_drag!"
        )
        self.log(f"-> Gõ văn bản qua Clipboard: '{clip_text}'...")
        res_clip = self.desktop_tool.execute(
            action="type_text",
            text=clip_text,
            force_direct=False,
            restore_clipboard=True,
        )
        self.assertIsNotNone(res_clip)
        time.sleep(0.3)

        self.desktop_tool.execute(action="press_key", key="enter")

        direct_text = "Direct typing test 1234567890!"
        self.log(f"-> Gõ văn bản trực tiếp (Direct): '{direct_text}'...")
        res_direct = self.desktop_tool.execute(
            action="type_text",
            text=direct_text,
            force_direct=True,
            interval=0.01,
        )
        self.assertIsNotNone(res_direct)
        time.sleep(0.3)

        self.log("-> Nhấn phím 'backspace' 5 lần...")
        res_press = self.desktop_tool.execute(
            action="press_key", key="backspace", presses=5
        )
        self.assertIn("Thành công", str(res_press))

        self.log(
            f"-> Thực thi Hotkey [{self.modifier_key}, 'a'] để chọn tất cả..."
        )
        res_hotkey = self.desktop_tool.execute(
            action="hotkey", keys=[self.modifier_key, "a"]
        )
        self.assertIn("Thành công", str(res_hotkey))
        time.sleep(0.3)

        self.desktop_tool.execute(action="press_key", key="delete")
        time.sleep(0.3)

    def test_05_full_e2e_integration_lifecycle(self):
        """Bước 5: Kịch bản E2E hoàn chỉnh (Launch -> Focus -> Type -> Clear -> Close)."""
        self.log("\n--- [TEST 5] Full E2E Integration Lifecycle ---")

        self._exec_window(action="focus", title_query=self.app_title_keyword)
        time.sleep(0.5)

        sample_text = "E2E Test Line 1\nE2E Test Line 2"
        self.desktop_tool.execute(
            action="type_text", text=sample_text, force_direct=False
        )
        time.sleep(0.5)

        self.desktop_tool.execute(
            action="hotkey", keys=[self.modifier_key, "a"]
        )
        time.sleep(0.2)
        self.desktop_tool.execute(action="press_key", key="backspace")
        time.sleep(0.5)

        self.log(f"-> Đóng cửa sổ ứng dụng '{self.app_title_keyword}'...")
        close_res = self._exec_window(
            action="close", title_query=self.app_title_keyword
        )
        self.log(f"   Kết quả đóng cửa sổ: {close_res}")
        self.assertIn("Thành công", str(close_res))
        time.sleep(1.0)

        remaining = self._exec_window(
            action="find", title_query=self.app_title_keyword
        )
        if isinstance(remaining, list) and len(remaining) > 0:
            self.log(
                "-> Phát hiện hộp thoại Save, gửi lệnh từ chối lưu ('n')..."
            )
            if sys.platform.startswith("win"):
                self.desktop_tool.execute(action="press_key", key="n")
            else:
                self.desktop_tool.execute(action="hotkey", keys=["alt", "n"])
            time.sleep(0.5)


if __name__ == "__main__":
    unittest.main(verbosity=2, failfast=True)