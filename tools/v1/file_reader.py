from pathlib import Path


def read_file(file_path: str, encoding: str = "utf-8") -> str:
    """Đọc nội dung của tệp tin văn bản.

    Args:
        file_path (str): Đường dẫn tới tệp tin cần đọc.
        encoding (str): Bảng mã (mặc định: 'utf-8').

    Returns:
        str: Nội dung tệp tin hoặc thông báo lỗi nếu có sự cố.
    """
    path = Path(file_path)
    if not path.exists():
        return f"Lỗi: File '{file_path}' không tồn tại."
    if not path.is_file():
        return f"Lỗi: Đường dẫn '{file_path}' là thư mục, không phải file."

    try:
        return path.read_text(encoding=encoding)
    except Exception as e:
        return f"Lỗi khi đọc file '{file_path}': {str(e)}"