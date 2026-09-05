import os
import shutil
from tools.v1.file_reader import read_file
from tools.v1.file_writer import write_file
from tools.v1.glob_search import glob_search

# Dọn dẹp thư mục logs cũ trước khi chạy test
if os.path.exists("logs"):
    shutil.rmtree("logs")

print("=== 1. TEST GHI FILE MỚI ===")
print(
    write_file(
        "logs/app.log",
        "Log entry 1: System started\nLog entry 2: OK\n",
        confirm_overwrite=False,
    )
)

print("\n=== 2. TEST TÌM FILE (GLOB) ===")
found_files = glob_search("*.log", root_dir="logs")
print("Tệp tìm thấy:", found_files)

print("\n=== 3. TEST ĐỌC FILE ===")
if isinstance(found_files, list) and found_files:
    target_file = (
        os.path.join("logs", found_files[0])
        if not found_files[0].startswith("logs")
        else found_files[0]
    )
    content = read_file(target_file)
    print(f"Nội dung file '{target_file}':\n{content}")

print("=== 4. TEST SO SÁNH DIFF VÀ GHI ĐÈ FILE ===")
new_log_content = (
    "Log entry 1: System started\n"
    "Log entry 2: WARNING - High Memory Usage\n"
    "Log entry 3: System Shutting Down\n"
)
# Đặt confirm_overwrite=True để xem giao diện Diff trực quan trên Terminal
print(
    write_file(
        "logs/app.log",
        new_log_content,
        mode="w",
        confirm_overwrite=True,
    )
)

print("\n=== 5. TEST THỬ GHI LẠI NỘI DUNG TRÙNG KHỚP ===")
print(
    write_file(
        "logs/app.log",
        new_log_content,
        mode="w",
        confirm_overwrite=True,
    )
)