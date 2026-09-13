import os
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, Union, Callable

TOOL_METADATA = {
    "name": "terminal_tool",
    "description": "Thực thi câu lệnh terminal/shell đồng bộ (chờ kết quả) hoặc mở ứng dụng/lệnh bất đồng bộ ngầm.",
    "base_risk": "HIGH",
    "danger_patterns": [
        r"rm\s+-rf",
        r"mkfs",
        r"format\s+[a-z]:",
        r"shutdown",
        r"reboot",
        r":\(\)\{\s*:\|:&\s*\};:",  # Fork bomb
    ],
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["run", "launch"],
                "description": "'run' để chạy đồng bộ và lấy STDOUT/STDERR. 'launch' để mở ứng dụng/lệnh ngầm không chặn luồng.",
            },
            "command": {
                "type": "string",
                "description": "Câu lệnh terminal hoặc ứng dụng cần chạy (ví dụ: 'dir', 'python --version', 'notepad').",
            },
            "timeout": {
                "type": "integer",
                "description": "Thời gian chờ tối đa tính bằng giây (Chỉ dùng cho action='run', mặc định: 30s).",
            },
            "cwd": {
                "type": "string",
                "description": "Đường dẫn thư mục làm việc khi chạy lệnh (Mặc định: thư mục hiện tại).",
            },
        },
        "required": ["action", "command"],
    },
}


class TerminalTool:
    """Class quản lý thực thi các câu lệnh Terminal và khởi chạy ứng dụng hệ thống."""

    def __init__(
        self, 
        default_timeout: int = 30,
        confirm_callback: Optional[Callable[[str, Dict[str, Any]], bool]] = None
    ):
        self.default_timeout = default_timeout
        self.confirm_callback = confirm_callback

    def _validate_cwd(self, cwd: Optional[str]) -> Union[Optional[str], str]:
        """Kiểm tra tính hợp lệ của thư mục làm việc."""
        if not cwd:
            return None
        path = Path(cwd)
        if not path.exists():
            return f"Lỗi: Thư mục làm việc (cwd) '{cwd}' không tồn tại."
        if not path.is_dir():
            return f"Lỗi: Đường dẫn (cwd) '{cwd}' không phải là thư mục."
        return str(path.resolve())

    def run(
        self, 
        command: str, 
        timeout: Optional[int] = None, 
        cwd: Optional[str] = None
    ) -> str:
        """Thực thi câu lệnh terminal đồng bộ và chờ thu thập kết quả."""
        if not command or not command.strip():
            return "Lỗi: Lệnh terminal không được để trống."

        valid_cwd = self._validate_cwd(cwd)
        if isinstance(valid_cwd, str) and valid_cwd.startswith("Lỗi:"):
            return valid_cwd

        exec_timeout = timeout if (timeout is not None and timeout > 0) else self.default_timeout

        try:
            process = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=exec_timeout,
                cwd=valid_cwd,
                errors="replace"  # Tránh lỗi Decode khi terminal trả về ký tự lạ
            )

            output = [f"Exit Code: {process.returncode}"]
            if process.stdout and process.stdout.strip():
                output.append(f"--- STDOUT ---\n{process.stdout.strip()}")
            if process.stderr and process.stderr.strip():
                output.append(f"--- STDERR ---\n{process.stderr.strip()}")

            return "\n".join(output)

        except subprocess.TimeoutExpired:
            return f"Lỗi: Lệnh '{command}' vượt quá thời gian chờ ({exec_timeout}s)."
        except Exception as e:
            return f"Lỗi khi thực thi lệnh: {str(e)}"

    def launch(
        self, 
        command: str, 
        cwd: Optional[str] = None
    ) -> str:
        """Mở ứng dụng GUI hoặc chạy lệnh bất đồng bộ ngầm không chặn luồng (non-blocking)."""
        if not command or not command.strip():
            return "Lỗi: Lệnh không được để trống."

        valid_cwd = self._validate_cwd(cwd)
        if isinstance(valid_cwd, str) and valid_cwd.startswith("Lỗi:"):
            return valid_cwd

        try:
            subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=valid_cwd,
            )
            return f"Thành công: Đã khởi chạy '{command}' ngầm."
        except Exception as e:
            return f"Lỗi khi khởi chạy ứng dụng: {str(e)}"

    def execute(
        self,
        action: str,
        command: str,
        timeout: Optional[int] = None,
        cwd: Optional[str] = None,
        **kwargs
    ) -> str:
        """Hàm điều hướng chung hỗ trợ gọi theo action cho Tool Executor."""
        if action == "run":
            return self.run(command=command, timeout=timeout, cwd=cwd)
        elif action == "launch":
            return self.launch(command=command, cwd=cwd)
        else:
            return f"Lỗi: Action '{action}' không hợp lệ. Chỉ chấp nhận 'run' hoặc 'launch'."


def run(
    action: str,
    command: str,
    timeout: Optional[int] = None,
    cwd: Optional[str] = None,
    **kwargs
) -> str:
    """Hàm entrypoint dạng standalone tương thích với LocalToolManager."""
    tool = TerminalTool()
    return tool.execute(action=action, command=command, timeout=timeout, cwd=cwd, **kwargs)
