import argparse
import os
import re
import sys


def apply_custom_patch(patch_path: str) -> None:
    """Đọc file patch định dạng custom và tự động tạo các file/thư mục tương ứng."""
    # 1. Kiểm tra file patch có tồn tại hay không
    if not os.path.exists(patch_path):
        print(f"❌ Lỗi: Không tìm thấy file patch tại: {patch_path}")
        sys.exit(1)

    with open(patch_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 2. Tách nội dung theo từng file dựa trên từ khóa "*** Add File:"
    blocks = re.split(r"\n\*\*\* Add File:\s*", content)

    if len(blocks) <= 1:
        print(
            "⚠️ Cảnh báo: Không tìm thấy khối '*** Add File:' nào trong file patch."
        )
        return

    created_count = 0

    # 3. Duyệt qua từng khối file và trích xuất nội dung
    for block in blocks[1:]:
        lines = block.splitlines()
        filepath = lines[0].strip()
        file_content = []

        for line in lines[1:]:
            if line.startswith("*** End Patch"):
                break
            # Loại bỏ ký tự '+' hoặc khoảng trắng thừa ở đầu dòng diff
            if line.startswith("+") or line.startswith(" "):
                file_content.append(line[1:])
            elif line == "":
                file_content.append("")

        # Tạo thư mục cha nếu chưa có
        dir_name = os.path.dirname(filepath)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)

        # Ghi nội dung vào file mục tiêu
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(file_content))

        print(f"✅ Đã tạo/cập nhật: {filepath}")
        created_count += 1

    print(
        f"\n🎉 Hoàn tất! Đã áp dụng thành công {created_count} file từ patch."
    )


if __name__ == "__main__":
    # Cấu hình bộ đọc tham số dòng lệnh
    parser = argparse.ArgumentParser(
        description="Công cụ áp dụng file patch định dạng custom cho dự án."
    )
    
    parser.add_argument(
        "patch_path",
        type=str,
        help="Đường dẫn tương đối hoặc tuyệt đối tới file .patch cần áp dụng",
    )

    args = parser.parse_args()
    apply_custom_patch(args.patch_path)