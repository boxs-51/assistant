from pathlib import Path
from typing import List, Optional, Union, Dict, Any

TOOL_METADATA = {
    "name": "find_by_glob",
    "description": "Tìm kiếm danh sách đường dẫn tệp tin và thư mục dựa trên mẫu glob pattern.",
    "base_risk": "LOW",
    "effects": ["READ"],
    "danger_patterns": [],
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Mẫu khớp tên glob pattern (ví dụ: '*.py', '**/*.json', 'src/*.cpp').",
            },
            "root_dir": {
                "type": "string",
                "description": "Đường dẫn thư mục gốc để bắt đầu tìm kiếm (Mặc định: '.').",
            },
            "recursive": {
                "type": "boolean",
                "description": "Tìm kiếm đệ quy trong các thư mục con (Mặc định: true).",
            },
            "max_results": {
                "type": "integer",
                "description": "Số lượng đường dẫn trả về tối đa để tránh quá tải bộ nhớ (Mặc định: 500).",
            },
        },
        "required": ["pattern"],
    },
}


class GlobSearchTool:
    """Class quản lý tìm kiếm tệp tin và thư mục theo Glob pattern."""

    def __init__(self, default_max_results: int = 500):
        self.default_max_results = default_max_results

    def find(
        self,
        pattern: str,
        root_dir: str = ".",
        recursive: bool = True,
        max_results: Optional[int] = None,
    ) -> Union[List[str], str]:
        """Tìm kiếm đường dẫn tệp tin và thư mục dựa theo glob pattern (Có giới hạn số lượng)."""
        if not pattern or not pattern.strip():
            return "Lỗi: Mẫu tìm kiếm (pattern) không được để trống."

        base_path = Path(root_dir)
        if not base_path.exists():
            return f"Lỗi: Thư mục gốc '{root_dir}' không tồn tại."
        if not base_path.is_dir():
            return f"Lỗi: '{root_dir}' không phải là thư mục."

        limit = max_results if (max_results is not None and max_results > 0) else self.default_max_results

        try:
            # Chuẩn hóa pattern nếu tìm kiếm đệ quy
            if recursive and not pattern.startswith("**"):
                search_pattern = f"**/{pattern}"
            else:
                search_pattern = pattern

            matches = []
            # Duyệt từng đường dẫn và dừng sớm nếu chạm giới hạn max_results
            for path in base_path.glob(search_pattern):
                matches.append(path.as_posix())
                if len(matches) >= limit:
                    break

            if not matches:
                return f"Thông báo: Không tìm thấy tệp hoặc thư mục nào phù hợp với mẫu '{pattern}' trong '{root_dir}'."

            # Sắp xếp kết quả theo thứ tự alphabet
            matches.sort()

            # Bổ sung cảnh báo nếu số kết quả chạm ngưỡng giới hạn
            if len(matches) >= limit:
                matches.append(f"... [CẢNH BÁO: Đã đạt giới hạn tối đa {limit} kết quả].")

            return matches

        except Exception as e:
            return f"Lỗi khi tìm kiếm glob với pattern '{pattern}': {str(e)}"

    def execute(
        self,
        pattern: str,
        root_dir: str = ".",
        recursive: bool = True,
        max_results: Optional[int] = None,
        **kwargs  # Bắt tham số thừa từ ToolExecutor
    ) -> Union[List[str], str]:
        """Hàm điều hướng chung cho Tool Executor."""
        return self.find(
            pattern=pattern,
            root_dir=root_dir,
            recursive=recursive,
            max_results=max_results,
        )


_default_glob_tool = GlobSearchTool()


def run(
    pattern: str,
    root_dir: str = ".",
    recursive: bool = True,
    max_results: Optional[int] = None,
    **kwargs
) -> Union[List[str], str]:
    """Hàm entrypoint chuẩn tương thích hoàn toàn với LocalToolManager & ToolExecutor."""
    return _default_glob_tool.execute(
        pattern=pattern,
        root_dir=root_dir,
        recursive=recursive,
        max_results=max_results,
        **kwargs
    )
