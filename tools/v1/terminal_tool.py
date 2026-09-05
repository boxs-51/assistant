import subprocess
from typing import Optional


def run_terminal_command(
    command: str, timeout: int = 30, cwd: Optional[str] = None
) -> str:
    """Thực thi câu lệnh terminal/shell đồng bộ và chờ kết quả.

    Args:
        command (str): Câu lệnh cần thực thi (ví dụ: 'dir', 'ls -la', 'python --version').
        timeout (int): Thời gian chờ tối đa (giây). Mặc định 30 giây.
        cwd (str, optional): Thư mục làm việc khi chạy lệnh.

    Returns:
        str: Chuỗi kết quả gồm Exit Code, STDOUT và STDERR.
    """
    if not command or not command.strip():
        return "Lỗi: Lệnh terminal không được để trống."

    try:
        process = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )

        output = [f"Exit Code: {process.returncode}"]
        if process.stdout:
            output.append(f"--- STDOUT ---\n{process.stdout.strip()}")
        if process.stderr:
            output.append(f"--- STDERR ---\n{process.stderr.strip()}")

        return "\n".join(output)

    except subprocess.TimeoutExpired:
        return f"Lỗi: Lệnh '{command}' vượt quá thời gian chờ ({timeout}s)."
    except Exception as e:
        return f"Lỗi khi thực thi lệnh: {str(e)}"


def launch_app(command: str, cwd: Optional[str] = None) -> str:
    """Mở ứng dụng GUI hoặc chạy lệnh bất đồng bộ ngầm không chặn luồng (non-blocking).

    Args:
        command (str): Câu lệnh hoặc tên ứng dụng (ví dụ: 'notepad', 'calc', 'code .').
        cwd (str, optional): Thư mục làm việc khi mở ứng dụng.

    Returns:
        str: Thông báo trạng thái khởi chạy.
    """
    if not command or not command.strip():
        return "Lỗi: Lệnh không được để trống."

    try:
        subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=cwd,
        )
        return f"Thành công: Đã khởi chạy '{command}' ngầm."
    except Exception as e:
        return f"Lỗi khi khởi chạy ứng dụng: {str(e)}"