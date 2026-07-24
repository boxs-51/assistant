import threading
from enum import Enum

class AgentState(Enum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    WAITING_APPROVAL = "WAITING_APPROVAL" # Dành cho HITL

class ExecutionController:
    def __init__(self):
        self.state = AgentState.STOPPED
        self._pause_event = threading.Event()
        self._pause_event.set() # Set = True nghĩa là KHÔNG bị tạm dừng

    def start(self):
        self.state = AgentState.RUNNING
        self._pause_event.set()

    def pause(self):
        """Người dùng ấn Tạm dừng"""
        if self.state == AgentState.RUNNING:
            self.state = AgentState.PAUSED
            self._pause_event.clear() # Đưa Event về False -> Các luồng chờ sẽ bị ngắt/tạm dừng

    def resume(self):
        """Người dùng ấn Tiếp tục"""
        if self.state == AgentState.PAUSED:
            self.state = AgentState.RUNNING
            self._pause_event.set() # Đưa Event về True -> Giải phóng luồng chờ

    def stop(self):
        """Người dùng ấn Hủy/Dừng hẳn"""
        self.state = AgentState.STOPPED
        self._pause_event.set() # Giải phóng nếu đang bị unpause để luồng thoát ra ngay

    def check_and_wait_if_paused(self, render_cb=None) -> bool:
        """
        Hàm này được gọi ở ĐẦU MỖI BƯỚC (Step) trong vòng lặp Agent.
        Trả về True nếu tiếp tục, False nếu đã bị HỦY HẲN (STOPPED).
        """
        if self.state == AgentState.PAUSED:
            if render_cb:
                render_cb("system", "⏸️ **Agent đã tạm dừng**. Đang chờ lệnh tiếp tục từ người dùng...")
            
            # Tạm dừng luồng tại đây cho đến khi _pause_event được .set() lại (Resume hoặc Stop)
            self._pause_event.wait()

        # Nếu sau khi unpause mà trạng thái là STOPPED -> Người dùng chọn hủy
        if self.state == AgentState.STOPPED:
            if render_cb:
                render_cb("system", "🛑 **Tác vụ đã bị hủy bỏ bởi người dùng.**")
            return False

        return True