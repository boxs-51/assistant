import os
import sys
import json
import unittest
import datetime

# Tự động chuyển thư mục làm việc về E:\client_name nếu tồn tại
TARGET_DIR = r"E:\client_name"
if os.path.exists(TARGET_DIR):
    os.chdir(TARGET_DIR)

# Import các công cụ chính từ hệ thống
try:
    from tools.terminal_tool import run as terminal_tool_run
    from tools.file_tool import run as file_tool_run
    from tools.find_by_glob import run as glob_tool_run
except ImportError:
    try:
        from terminal_tool import run as terminal_tool_run
        from file_tool import run as file_tool_run
        from find_by_glob import run as glob_tool_run
    except ImportError as e:
        print(f"⚠️ Lỗi Import Tool: {e}. Vui lòng kiểm tra lại PYTHONPATH hoặc thư mục chứa tool.")


class TestTerminalAndModularPythonOperations(unittest.TestCase):
    # Cờ kiểm soát trạng thái Pipeline (Nếu True -> Ngắt toàn bộ các bước sau)
    _pipeline_failed = False

    # -------------------------------------------------------------------------
    # CẤU HÌNH MÔI TRƯỜNG VIRTUAL ENV & WORKSPACE
    # -------------------------------------------------------------------------
    PRIMARY_VENV_PYTHON = r"D:\client_name\.venv\Scripts\python.exe"
    
    # Thư mục chứa dự án Python đa module phục vụ test
    LOG_DIR = os.path.join(os.getcwd(), "logs", "test_terminel")
    LOG_FILE = os.path.join(LOG_DIR, "terminal_pipeline.log")
    WORKSPACE_DIR = os.path.join(LOG_DIR, "python_workspace")
    
    # Các file module Python sẽ tạo
    FILE_MODULE_CORE = os.path.join(WORKSPACE_DIR, "core_engine.py")
    FILE_MODULE_UTILS = os.path.join(WORKSPACE_DIR, "utils_formatter.py")
    FILE_MAIN_ENTRY = os.path.join(WORKSPACE_DIR, "main_app.py")
    FILE_EXEC_OUTPUT = os.path.join(WORKSPACE_DIR, "execution_result.json")

    @classmethod
    def setUpClass(cls):
        """Khởi tạo thư mục log và workspace."""
        os.makedirs(cls.LOG_DIR, exist_ok=True)
        os.makedirs(cls.WORKSPACE_DIR, exist_ok=True)
        
        # Xác định đường dẫn Python Executable phù hợp
        if os.path.exists(cls.PRIMARY_VENV_PYTHON):
            cls.python_bin = cls.PRIMARY_VENV_PYTHON
        else:
            cls.python_bin = sys.executable  # Fallback về Python hiện tại nếu venv chưa cài
            
        with open(cls.LOG_FILE, "w", encoding="utf-8") as f:
            f.write(f"=== KHỞI TẠO TEST TERMINAL & MODULAR PYTHON [{datetime.datetime.now()}] ===\n")
            f.write(f"Python Binary sử dụng: {cls.python_bin}\n")
            f.write(f"Thư mục làm việc Workspace: {cls.WORKSPACE_DIR}\n\n")

    def log(self, message: str):
        """Ghi log đồng thời ra màn hình terminal và file log."""
        print(message)
        with open(self.LOG_FILE, "a", encoding="utf-8") as f:
            f.write(message + "\n")

    def setUp(self):
        """Kiểm tra cờ thất bại trước mỗi bước."""
        if TestTerminalAndModularPythonOperations._pipeline_failed:
            self.skipTest("[PIPELINE ABORTED] Đã dừng do bước trước đó gặp lỗi.")

    @classmethod
    def tearDownClass(cls):
        """Tổng kết trạng thái test."""
        print("\n" + "=" * 60)
        if cls._pipeline_failed:
            print("❌ PIPELINE THẤT BẠI: Quá trình kiểm tra Terminal/Python bị gián đoạn.")
        else:
            print("✅ PIPELINE THÀNH CÔNG: Thực thi hoàn hảo các thao tác Python đa module & Terminal!")
        print(f"📄 Log quá trình: {cls.LOG_FILE}")
        print("=" * 60)

    def _mark_failed_and_stop(self, step_name: str, message: str):
        """Đánh dấu lỗi và ngắt pipeline."""
        error_msg = f"❌ [{step_name}] LỖI NGẮT PIPELINE: {message}"
        self.log(error_msg)
        TestTerminalAndModularPythonOperations._pipeline_failed = True
        self.fail(error_msg)

    # =========================================================================
    # CÁC BƯỚC KIỂM TRA CHUYÊN SÂU
    # =========================================================================

    def test_01_verify_venv_and_terminal_environment(self):
        """Bước 1: Kiểm tra môi trường Virtual Environment và thư mục làm việc qua terminal_tool."""
        self.log("\n[1/5] 💻 BƯỚC 1: Kiểm tra VENV Python & Thư mục làm việc qua 'terminal_tool'...")
        
        try:
            # Kiểm tra phiên bản Python thông qua file python.exe của Virtual Environment
            cmd = f'"{self.python_bin}" --version'
            self.log(f"      -> Chạy câu lệnh: {cmd}")
            
            res = terminal_tool_run(action="run", command=cmd, cwd=self.WORKSPACE_DIR, timeout=15)
            res_str = str(res)
            
            if "Python 3." not in res_str and "python" not in res_str.lower():
                self._mark_failed_and_stop("Step 1", f"Không thể xác minh Python VENV. Kết quả: {res_str}")

            self.log(f"      ✓ Thành công: VENV Python phản hồi chính xác ({res_str.strip()}).")

        except Exception as e:
            self._mark_failed_and_stop("Step 1", f"Ngoại lệ tại Step 1: {str(e)}")

    def test_02_create_modular_python_project(self):
        """Bước 2: Tạo dự án Python đa module (Core Engine + Utils + Main App) bằng file_tool."""
        self.log("\n[2/5] 📝 BƯỚC 2: Khởi tạo các module Python nguồn bằng 'file_tool'...")
        
        try:
            # 1. Module 1: Core Engine (Xử lý tính toán/dữ liệu)
            core_code = '''# Core Engine Module
import math

class DataProcessor:
    def __init__(self, name):
        self.name = name

    def compute_metrics(self, numbers):
        total = sum(numbers)
        avg = total / len(numbers) if numbers else 0
        squared_diff = sum((x - avg) ** 2 for x in numbers)
        std_dev = math.sqrt(squared_diff / len(numbers)) if numbers else 0
        return {"sum": total, "average": avg, "std_dev": round(std_dev, 4)}
'''
            # 2. Module 2: Utils Formatter (Định dạng đầu ra JSON/Text)
            utils_code = '''# Utils Formatter Module
import json
import datetime

def format_output(source_name, metrics):
    payload = {
        "timestamp": str(datetime.datetime.now()),
        "source": source_name,
        "results": metrics,
        "status": "SUCCESS"
    }
    return json.dumps(payload, indent=2)
'''
            # 3. Module 3: Main App (Import cả 2 module trên và thực thi)
            main_code = f'''# Main Application Entry Point
import os
import sys

# Đảm bảo import được các module cùng thư mục
sys.path.insert(0, os.path.dirname(__file__))

from core_engine import DataProcessor
from utils_formatter import format_output

def main():
    dataset = [12, 45, 67, 23, 89, 100, 34, 56]
    processor = DataProcessor("Terminal_Module_Test")
    metrics = processor.compute_metrics(dataset)
    
    formatted_json = format_output(processor.name, metrics)
    print("=== EXECUTION STDOUT START ===")
    print(formatted_json)
    print("=== EXECUTION STDOUT END ===")
    
    # Ghi xuất kết quả ra file JSON
    out_file = r"{self.FILE_EXEC_OUTPUT}"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(formatted_json)

if __name__ == "__main__":
    main()
'''
            # Ghi các file bằng file_tool
            file_tool_run(action="write", file_paths=[self.FILE_MODULE_CORE], content=core_code, mode="w")
            file_tool_run(action="write", file_paths=[self.FILE_MODULE_UTILS], content=utils_code, mode="w")
            file_tool_run(action="write", file_paths=[self.FILE_MAIN_ENTRY], content=main_code, mode="w")

            self.log("      ✓ Thành công: Đã tạo đủ 3 file Python (core_engine.py, utils_formatter.py, main_app.py).")

        except Exception as e:
            self._mark_failed_and_stop("Step 2", f"Ngoại lệ tại Step 2: {str(e)}")

    def test_03_execute_modular_python_via_terminal(self):
        """Bước 3: Thực thi chương trình Python đa module từ terminal_tool trong thư mục làm việc chỉ định."""
        self.log("\n[3/5] 🚀 BƯỚC 3: Thực thi chương trình đa module bằng VENV Python qua 'terminal_tool'...")
        
        try:
            # Lệnh thực thi file main_app.py thông qua Python VENV
            cmd = f'"{self.python_bin}" "{self.FILE_MAIN_ENTRY}"'
            self.log(f"      -> Chạy câu lệnh tại cwd='{self.WORKSPACE_DIR}'")
            self.log(f"      -> Lệnh: {cmd}")
            
            exec_res = terminal_tool_run(action="run", command=cmd, cwd=self.WORKSPACE_DIR, timeout=20)
            res_text = str(exec_res)

            # Kiểm tra STDOUT trả về
            if "=== EXECUTION STDOUT START ===" not in res_text or "Terminal_Module_Test" not in res_text:
                self._mark_failed_and_stop("Step 3", f"Thực thi Python thất bại hoặc kết quả STDOUT không hợp lệ:\n{res_text}")

            self.log("      ✓ Thành công: Terminal đã thực thi thành công chương trình Python qua nhiều module!")
            self.log(f"      -> Đầu ra thu được:\n{res_text[:300]}...")

        except Exception as e:
            self._mark_failed_and_stop("Step 3", f"Ngoại lệ tại Step 3: {str(e)}")

    def test_04_verify_output_artifact_and_data(self):
        """Bước 4: Kiểm tra file JSON được sinh ra từ quá trình thực thi chương trình Python."""
        self.log("\n[4/5] 🔍 BƯỚC 4: Kiểm tra dữ liệu đầu ra JSON sinh ra từ script Python...")
        
        try:
            if not os.path.exists(self.FILE_EXEC_OUTPUT):
                self._mark_failed_and_stop("Step 4", f"Không tìm thấy file kết quả JSON: {self.FILE_EXEC_OUTPUT}")

            # Đọc và parse dữ liệu JSON bằng file_tool
            read_res = file_tool_run(action="read", file_paths=[self.FILE_EXEC_OUTPUT])
            raw_json_str = str(read_res)

            # Parse kiểm tra cấu trúc JSON
            data = json.loads(raw_json_str)
            if data.get("status") != "SUCCESS" or "results" not in data:
                self._mark_failed_and_stop("Step 4", f"Cấu trúc JSON không chính xác: {data}")

            self.log(f"      ✓ Thành công: File JSON kết quả hợp lệ. Tổng = {data['results']['sum']}, Trung bình = {data['results']['average']}")

        except Exception as e:
            self._mark_failed_and_stop("Step 4", f"Ngoại lệ tại Step 4: {str(e)}")

    def test_05_launch_background_python_task(self):
        """Bước 5: Thử nghiệm chạy tác vụ ngầm (action='launch') cho script Python."""
        self.log("\n[5/5] ⚡ BƯỚC 5: Thử nghiệm thực thi bất đồng bộ ngầm bằng action='launch'...")
        
        try:
            # Chạy một lệnh Python ngắn ngầm không đợi kết quả
            bg_cmd = f'"{self.python_bin}" -c "import time; time.sleep(1); print(\'Background Task Done\')"'
            self.log(f"      -> Mở lệnh ngầm: {bg_cmd}")
            
            terminal_tool_run(action="launch", command=bg_cmd, cwd=self.WORKSPACE_DIR)

            # Quét kiểm tra toàn bộ file trong thư mục workspace bằng find_by_glob
            glob_res = glob_tool_run(pattern="*.py", root_dir=self.WORKSPACE_DIR, recursive=False)
            glob_str = str(glob_res)

            if "main_app.py" not in glob_str or "core_engine.py" not in glob_str:
                self._mark_failed_and_stop("Step 5", f"find_by_glob không tìm thấy đủ các file script Python: {glob_res}")

            self.log("      ✓ Thành công: Lệnh bất đồng bộ khởi chạy thành công & hoàn tất xác minh toàn bộ file.")

        except Exception as e:
            self._mark_failed_and_stop("Step 5", f"Ngoại lệ tại Step 5: {str(e)}")


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2, failfast=True)
    suite = unittest.TestLoader().loadTestsFromTestCase(TestTerminalAndModularPythonOperations)
    runner.run(suite)