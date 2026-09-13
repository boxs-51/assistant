import mimetypes
from pathlib import Path
from fastapi import UploadFile
from urllib.parse import urlparse

class FileHelper:
    """Lớp tiện ích để xử lý các hoạt động liên quan đến tệp."""

    CUSTOM_MIME_MAP = {
        ".py": "text/x-python",
        ".md": "text/markdown",
    }

    @classmethod
    def detect_mime_type(cls, file_path: any) -> str:
        """
        Xác định mimeType của tệp dựa trên đuôi tệp, tương thích cả str, Path và FastAPI UploadFile.
        """
        filename_str = ""
        if hasattr(file_path, "filename"):
            filename_str = file_path.filename
        elif isinstance(file_path, Path):
            filename_str = file_path.name
        else:
            filename_str = str(file_path)

        # Cải tiến: Nếu là URL, chỉ lấy phần path để xác định extension
        try:
            parsed_url = urlparse(filename_str)
            filename = parsed_url.path if parsed_url.scheme and parsed_url.netloc else filename_str
        except Exception:
            filename = filename_str

        # Trích xuất suffix từ tên file
        file_extension = Path(filename).suffix.lower()

        # Ưu tiên bản đồ tùy chỉnh
        if file_extension in cls.CUSTOM_MIME_MAP:
            return cls.CUSTOM_MIME_MAP[file_extension]

        # Sử dụng thư viện mimetypes của Python
        mime_type, _ = mimetypes.guess_type(filename)

        # Fallback nếu không xác định được
        return mime_type or "application/octet-stream"