import subprocess
from typing import Optional, Any

TOOL_METADATA = {
    "name": "execute_terminal",
    "description": "Thực thi lệnh shell/bash/cmd trên hệ điều hành và trả về output (stdout/stderr).",
    "base_risk": "HIGH",
    "danger_patterns": [
        r"rm\s+-rf", r"del\s+/f", r"format", r"sudo", r"shutdown", r"reboot", r">\s*/dev/sd"
    ],
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Lệnh terminal cần chạy (e.g., 'pytest', 'cmake --build build', 'git status')."
            },
            "cwd": {
                "type": "string",
                "description": "Thư mục làm việc thực thi lệnh (Mặc định: Thư mục hiện tại)."
            },
            "timeout": {
                "type": "integer",
                "description": "Thời gian chờ tối đa tính bằng giây (Mặc định: 60s)."
            }
        },
        "required": ["command"]
    }
}

def run(command: str, cwd: Optional[str] = None, timeout: int = 60, context_session: Optional[Any] = None) -> str:
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout
        )
        
        output = result.stdout
        if result.stderr:
            output += f"\n[STDERR]\n{result.stderr}"

        if not output.strip():
            output = "(Lệnh thực thi thành công nhưng không trả về output)"

        # Lưu mã lỗi trả về vào Scratchpad
        if context_session and hasattr(context_session, "scratchpad"):
            context_session.scratchpad["last_cmd_exit_code"] = result.returncode

        status_prefix = "✅ SUCCESS" if result.returncode == 0 else f"⚠️ EXIT CODE {result.returncode}"
        return f"[{status_prefix}]\n```text\n{output[:4000]}\n```" # Cắt bớt nếu output quá dài
    except subprocess.TimeoutExpired:
        return f"⏰ Lỗi: Lệnh đã chạy quá thời gian chờ cho phép ({timeout}s)."
    except Exception as e:
        return f"❌ Lỗi khi thực thi lệnh: {str(e)}"