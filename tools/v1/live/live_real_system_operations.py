import os
import re
import sys
import unittest
import datetime

# Tự động chuyển thư mục làm việc về E:\client_name nếu tồn tại
TARGET_DIR = r"E:\client_name"
if os.path.exists(TARGET_DIR):
    os.chdir(TARGET_DIR)

# Import các công cụ chính từ hệ thống
try:
    from tools.v1.web_tool import run as web_tool_run
    from tools.v1.file_tool import run as file_tool_run
    from tools.v1.find_by_glob import run as glob_tool_run
    from tools.v1.terminal_tool import run as terminal_tool_run
except ImportError:
    try:
        from tools.v1.web_tool import run as web_tool_run
        from tools.v1.file_tool import run as file_tool_run
        from tools.v1.find_by_glob import run as glob_tool_run
        from tools.v1.terminal_tool import run as terminal_tool_run
    except ImportError as e:
        print(f"⚠️ Lỗi Import Tool: {e}. Vui lòng kiểm tra lại PYTHONPATH hoặc thư mục chứa tool.")


class TestRealSystemOperations(unittest.TestCase):
    # Cờ kiểm soát trạng thái Pipeline (Nếu True -> Ngắt toàn bộ các bước sau)
    _pipeline_failed = False
    
    # Khai báo đường dẫn lưu trữ đầu ra
    LOG_DIR = os.path.join(os.getcwd(), "logs","test_wed_tool")
    LOG_FILE = os.path.join(LOG_DIR, "pipeline_execution.log")
    FILE_WEB_RAW = os.path.join(LOG_DIR, "web_content_raw.txt")
    FILE_WEB_MODIFIED = os.path.join(LOG_DIR, "web_content_modified.txt")

    @classmethod
    def setUpClass(cls):
        """Khởi tạo thư mục log và file log chính."""
        os.makedirs(cls.LOG_DIR, exist_ok=True)
        with open(cls.LOG_FILE, "w", encoding="utf-8") as f:
            f.write(f"=== KHỞI TẠO PIPELINE SYSTEM TEST [{datetime.datetime.now()}] ===\n")
            f.write(f"Thư mục làm việc: {os.getcwd()}\n\n")

    def log(self, message: str):
        """Ghi log đồng thời ra màn hình terminal và file log."""
        print(message)
        with open(self.LOG_FILE, "a", encoding="utf-8") as f:
            f.write(message + "\n")

    def setUp(self):
        """Kiểm tra cờ thất bại trước mỗi bước, ngắt ngay nếu bước trước lỗi."""
        if TestRealSystemOperations._pipeline_failed:
            self.skipTest("[PIPELINE ABORTED] Đã dừng do bước trước đó gặp lỗi.")

    @classmethod
    def tearDownClass(cls):
        """Tổng kết trạng thái sau khi chạy xong pipeline."""
        print("\n" + "=" * 60)
        if cls._pipeline_failed:
            print("❌ PIPELINE THẤT BẠI: Quá trình test đã bị ngắt ngay khi gặp lỗi.")
        else:
            print("✅ PIPELINE THÀNH CÔNG: Tất cả 5 bước đã kiểm tra hoàn chỉnh 4 công cụ.")
        print(f"📄 Log quá trình: {cls.LOG_FILE}")
        print("=" * 60)

    def _mark_failed_and_stop(self, step_name: str, message: str):
        """Đánh dấu lỗi, ghi log ngắt pipeline và dừng unittest."""
        error_msg = f"❌ [{step_name}] LỖI NGẮT PIPELINE: {message}"
        self.log(error_msg)
        TestRealSystemOperations._pipeline_failed = True
        self.fail(error_msg)

    # =========================================================================
    # PIPELINE CÁC BƯỚC THỰC THI (TỰ ĐỘNG NGẮT NẾU XẢY RA LỖI)
    # =========================================================================

    def test_01_terminal_tool_environment_check(self):
        """Bước 1: Kiểm tra môi trường hệ thống bằng terminal_tool (action='run')."""
        self.log("\n[1/5] 💻 BƯỚC 1: Kiểm tra hệ thống bằng 'terminal_tool'...")
        
        try:
            res = terminal_tool_run(action="run", command="dir", cwd=os.getcwd(), timeout=15)
            output_text = str(res)
            if not output_text or "error" in output_text.lower():
                self._mark_failed_and_stop("Step 1", f"terminal_tool thực thi thất bại: {output_text}")

            self.log("      ✓ Thành công: terminal_tool hoạt động bình thường, đã lấy thông tin thư mục.")
        except Exception as e:
            self._mark_failed_and_stop("Step 1", f"Ngoại lệ tại terminal_tool: {str(e)}")

    def test_02_web_tool_search_and_scrape(self):
        """Bước 2: Tìm kiếm lượng lớn kết quả và lọc các URL chứa bài viết/tài liệu phù hợp để cào."""
        self.log("\n[2/5] 🌐 BƯỚC 2: Thực thi 'web_tool' (Search & Scrape với Bộ Lọc URL)...")
        
        try:
            # 1. Mở rộng tìm kiếm với max_results lớn hơn (10 kết quả)
            self.log("      -> Đang tìm kiếm từ khóa với action='search' (max_results=10)...")
            search_res = web_tool_run(action="search", query="tin tuc moi nhat", max_results=10)
            
            if not search_res:
                self._mark_failed_and_stop("Step 2", "web_tool search không trả về kết quả.")

            # Trích xuất danh sách tất cả các URL tìm được
            raw_urls = []
            if isinstance(search_res, list):
                for item in search_res:
                    if isinstance(item, dict):
                        u = item.get("url") or item.get("link") or item.get("href")
                        if u: raw_urls.append(u)
                    elif isinstance(item, str) and item.startswith("http"):
                        raw_urls.append(item)
            elif isinstance(search_res, dict):
                results = search_res.get("results", [])
                for item in results:
                    if isinstance(item, dict):
                        u = item.get("url") or item.get("link") or item.get("href")
                        if u: raw_urls.append(u)

            if not raw_urls:
                raw_urls = re.findall(r'https?://[^\s\'"<>]+', str(search_res))

            if not raw_urls:
                self._mark_failed_and_stop("Step 2", f"Không bóc tách được URL từ kết quả tìm kiếm: {search_res}")

            # 2. BỘ LỌC THÔNG MINH: Chỉ chọn URL chứa tài liệu/bài viết, loại bỏ Video/Mạng xã hội/File tĩnh
            EXCLUDE_DOMAINS = [
                "youtube.com", "youtu.be", "facebook.com", "twitter.com", "x.com",
                "linkedin.com", "instagram.com", "tiktok.com", "reddit.com", "pinterest.com"
            ]
            EXCLUDE_EXTENSIONS = [".pdf", ".zip", ".exe", ".mp4", ".png", ".jpg"]

            candidate_urls = []
            for url in raw_urls:
                url_lower = url.lower()
                # Bỏ qua domain mạng xã hội / video
                if any(domain in url_lower for domain in EXCLUDE_DOMAINS):
                    continue
                # Bỏ qua đuôi file tĩnh
                if any(url_lower.endswith(ext) for ext in EXCLUDE_EXTENSIONS):
                    continue
                candidate_urls.append(url)

            self.log(f"      -> Đã lọc {len(raw_urls)} URL gốc xuống còn {len(candidate_urls)} URL bài viết/tài liệu chất lượng.")

            if not candidate_urls:
                self._mark_failed_and_stop("Step 2", "Không có URL nào vượt qua bộ lọc tài liệu bài viết.")

            # 3. Thử cào dữ liệu qua danh sách URL đã lọc
            scrape_text = ""
            successful_url = None
            error_keywords = ["lỗi:", "error:", "không thể trích xuất", "failed to fetch"]

            for idx, target_url in enumerate(candidate_urls, 1):
                self.log(f"      -> [{idx}/{len(candidate_urls)}] Đang cào tài liệu từ URL: '{target_url}'...")
                try:
                    scrape_res = web_tool_run(action="scrape", url=target_url, force_js=True, max_chars=100000, timeout=15)
                    res_str = str(scrape_res).strip()
                    
                    if not res_str or any(kw in res_str.lower() for kw in error_keywords):
                        self.log(f"         ⚠️ URL bị từ chối hoặc lỗi ({res_str[:70]}...). Chuyển sang URL tiếp theo...")
                        continue

                    scrape_text = res_str
                    successful_url = target_url
                    break
                except AssertionError:
                    raise

            if not scrape_text or not successful_url:
                self._mark_failed_and_stop("Step 2", "Tất cả các URL bài viết đã lọc đều không thể cào dữ liệu.")

            # Chèn từ khóa cố định để đảm bảo Bước 4 (Replace) luôn chạy chính xác
            if "httpbin.org" not in scrape_text:
                scrape_text += "\nTarget reference domain: httpbin.org"

            TestRealSystemOperations.temp_web_data = scrape_text
            self.log(f"      ✓ Thành công: web_tool đã tải về thành công tài liệu từ '{successful_url}'.")

        except Exception as e:
            self._mark_failed_and_stop("Step 2", f"Ngoại lệ tại web_tool: {str(e)}")

    def test_03_file_tool_write_and_read(self):
        """Bước 3: Tạo và đọc các file dữ liệu Web bằng file_tool (action='write' & 'read')."""
        self.log("\n[3/5] 📁 BƯỚC 3: Tạo và quản lý file bằng 'file_tool'...")
        
        if not hasattr(TestRealSystemOperations, 'temp_web_data'):
            self._mark_failed_and_stop("Step 3", "Không có dữ liệu web từ Bước 2!")

        try:
            raw_content = TestRealSystemOperations.temp_web_data

            self.log(f"      -> Ghi file Web gốc: {self.FILE_WEB_RAW}")
            file_tool_run(action="write", file_paths=[self.FILE_WEB_RAW], content=raw_content, mode="w")

            self.log(f"      -> Ghi file Web phục vụ chỉnh sửa: {self.FILE_WEB_MODIFIED}")
            file_tool_run(action="write", file_paths=[self.FILE_WEB_MODIFIED], content=raw_content, mode="w")

            read_res = file_tool_run(action="read", file_paths=[self.FILE_WEB_RAW])
            if not read_res or len(str(read_res)) == 0:
                self._mark_failed_and_stop("Step 3", "file_tool đọc file thất bại hoặc file rỗng.")

            self.log("      ✓ Thành công: Đã tạo file Web gốc và file chỉnh sửa thành công.")

        except Exception as e:
            self._mark_failed_and_stop("Step 3", f"Ngoại lệ tại file_tool: {str(e)}")

    def test_04_file_tool_edit_and_glob_search(self):
        """Bước 4: Chỉnh sửa file Web (search/replace) & Tìm kiếm file bằng find_by_glob."""
        self.log("\n[4/5] 📝 BƯỚC 4: Chỉnh sửa nội dung file & quét file bằng 'find_by_glob'...")
        
        try:
            self.log("      -> Tìm kiếm chuỗi trong file bằng file_tool (action='search')...")
            search_in_file = file_tool_run(action="search", file_paths=[self.FILE_WEB_MODIFIED], queries=["httpbin.org"])

            self.log("      -> Thay thế nội dung trong file bằng file_tool (action='replace')...")
            file_tool_run(
                action="replace",
                file_paths=[self.FILE_WEB_MODIFIED],
                queries=["httpbin.org"],
                replacements=["MODIFIED_CLIENT_SYSTEM_TARGET"]
            )

            updated_content = str(file_tool_run(action="read", file_paths=[self.FILE_WEB_MODIFIED]))
            if "MODIFIED_CLIENT_SYSTEM_TARGET" not in updated_content:
                self._mark_failed_and_stop("Step 4", "Nội dung file chưa được thay thế thành công!")

            self.log("      -> Tìm kiếm danh sách file bằng 'find_by_glob'...")
            glob_res = glob_tool_run(pattern="*.txt", root_dir=self.LOG_DIR, recursive=False)
            
            glob_str = str(glob_res)
            if "web_content_raw.txt" not in glob_str or "web_content_modified.txt" not in glob_str:
                self._mark_failed_and_stop("Step 4", f"find_by_glob không tìm thấy đủ các file đã tạo: {glob_res}")

            self.log("      ✓ Thành công: Chỉnh sửa nội dung file và quét mẫu glob hoàn tất.")

        except Exception as e:
            self._mark_failed_and_stop("Step 4", f"Ngoại lệ tại Bước 4: {str(e)}")

    def test_05_terminal_launch_and_final_verification(self):
        """Bước 5: Chạy ứng dụng bất đồng bộ bằng terminal_tool (action='launch') & Tổng kiểm tra."""
        self.log("\n[5/5] 🚀 BƯỚC 5: Thử nghiệm 'launch' ngầm & Kiểm tra toàn vẹn...")
        
        try:
            self.log("      -> Chạy lệnh bất đồng bộ ngầm bằng action='launch'...")
            launch_cmd = "cmd.exe /c echo Pipeline completed > NUL" if os.name == 'nt' else "echo Pipeline completed"
            terminal_tool_run(action="launch", command=launch_cmd)

            required_files = [self.LOG_FILE, self.FILE_WEB_RAW, self.FILE_WEB_MODIFIED]
            for fpath in required_files:
                if not os.path.exists(fpath):
                    self._mark_failed_and_stop("Step 5", f"Thiếu file đầu ra bắt buộc: {fpath}")

            self.log("      ✓ Thành công: Đã xác minh đầy đủ file Log, file Web gốc và file Web chỉnh sửa.")

        except Exception as e:
            self._mark_failed_and_stop("Step 5", f"Ngoại lệ tại Bước 5: {str(e)}")


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2, failfast=True)
    suite = unittest.TestLoader().loadTestsFromTestCase(TestRealSystemOperations)
    runner.run(suite)