from pathlib import Path
from typing import List, Union


def glob_search(
    pattern: str, root_dir: str = ".", recursive: bool = True
) -> Union[List[str], str]:
    """Tìm kiếm đường dẫn tệp tin và thư mục dựa theo glob pattern.

    Args:
        pattern (str): Mẫu đúp/khớp tên (ví dụ: '*.py', '**/*.json',
          'src/*.cpp').
        root_dir (str): Thư mục gốc để tìm kiếm. Mặc định '.'.
        recursive (bool): Tìm kiếm đệ quy trong các thư mục con (mặc định:
          True).

    Returns:
        Union[List[str], str]: Danh sách chuỗi đường dẫn tương đối hoặc thông
        báo lỗi.
    """
    base_path = Path(root_dir)
    if not base_path.exists():
        return f"Lỗi: Thư mục gốc '{root_dir}' không tồn tại."
    if not base_path.is_dir():
        return f"Lỗi: '{root_dir}' không phải là thư mục."

    try:
        if recursive and not pattern.startswith("**"):
            matches = list(base_path.rglob(pattern))
        else:
            matches = list(base_path.glob(pattern))

        return [str(p) for p in matches]
    except Exception as e:
        return f"Lỗi khi tìm kiếm glob với pattern '{pattern}': {str(e)}"