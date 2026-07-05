import mimetypes
from pathlib import Path

class FileHelper:
    """Lớp tiện ích để xử lý các hoạt động liên quan đến tệp."""

    # Bản đồ tùy chỉnh để ghi đè hoặc bổ sung cho thư viện mimetypes
    CUSTOM_MIME_MAP = {
        ".py": "text/x-python",
        ".md": "text/markdown",
    }

    @classmethod
    def detect_mime_type(cls, file_path: str | Path) -> str:
        """
        Xác định mimeType của tệp dựa trên đuôi tệp, sử dụng thư viện mimetypes
        và fallback về bản đồ tùy chỉnh.
        """
        p = Path(file_path)
        file_extension = p.suffix.lower()

        # Ưu tiên bản đồ tùy chỉnh
        if file_extension in cls.CUSTOM_MIME_MAP:
            return cls.CUSTOM_MIME_MAP[file_extension]

        # Sử dụng thư viện mimetypes của Python
        mime_type, _ = mimetypes.guess_type(p.name)

        # Fallback nếu không xác định được
        return mime_type or "application/octet-stream"