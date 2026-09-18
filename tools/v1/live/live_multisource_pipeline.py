import os
import re
import sys
import unittest
import datetime

# Tự động chuyển thư mục làm việc về E:\assistant nếu tồn tại
TARGET_DIR = r"E:\assistant"
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


class TestAdvancedMultiSourceOperations(unittest.TestCase):
    # Cờ kiểm soát trạng thái Pipeline (Nếu True -> Ngắt toàn bộ các bước sau)
    _pipeline_failed = False
    
    # -------------------------------------------------------------------------
    # CẤU HÌNH TÌM KIẾM CHUYÊN SÂU & ĐA NGUỒN
    # -------------------------------------------------------------------------
    TARGET_SEARCH_QUERY = "du bao thoi tiet tai khanh hoa, co con bao nao khong"
    
    # Số lượng nguồn tối thiểu cần thu thập thành công
    MIN_REQUIRED_SOURCES = 3
    MAX_SEARCH_RESULTS = 15

    # Khai báo đường dẫn lưu trữ đầu ra
    LOG_DIR = os.path.join(os.getcwd(), "logs", "test_web_tool_mutisoure")
    LOG_FILE = os.path.join(LOG_DIR, "pipeline_execution.log")
    LOG_FILE_DETAILED = os.path.join(LOG_DIR, "pipeline_execution_detailed.log")  # File log đầy đủ chi tiết
    FILE_WEB_RAW = os.path.join(LOG_DIR, "multisource_content_raw.txt")
    FILE_WEB_MODIFIED = os.path.join(LOG_DIR, "multisource_content_modified.txt")
    FILE_SOURCES_SUMMARY = os.path.join(LOG_DIR, "sources_summary.txt")

    # Dữ liệu bộ nhớ đệm
    temp_sources_data = []

    @classmethod
    def setUpClass(cls):
        """Khởi tạo thư mục log và các file log chính/chi tiết."""
        os.makedirs(cls.LOG_DIR, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Khởi tạo File Log chính (Ngắn gọn)
        with open(cls.LOG_FILE, "w", encoding="utf-8") as f:
            f.write(f"=== KHỞI TẠO PIPELINE SYSTEM TEST [MULTI-SOURCE] [{timestamp}] ===\n")
            f.write(f"Chủ đề tìm kiếm: '{cls.TARGET_SEARCH_QUERY}'\n")
            f.write(f"Thư mục làm việc: {os.getcwd()}\n\n")

        # Khởi tạo File Log Đầy đủ Chi tiết (Detailed Log)
        with open(cls.LOG_FILE_DETAILED, "w", encoding="utf-8") as f_det:
            f_det.write(f"======================================================================\n")
            f_det.write(f"=== BÁO CÁO LOG ĐẦY ĐỦ CHI TIẾT PIPELINE MULTI-SOURCE [{timestamp}] ===\n")
            f_det.write(f"======================================================================\n")
            f_det.write(f"Môi trường thực thi Python: {sys.executable}\n")
            f_det.write(f"Hệ điều hành: {sys.platform}\n")
            f_det.write(f"Chủ đề tìm kiếm: '{cls.TARGET_SEARCH_QUERY}'\n")
            f_det.write(f"Thư mục làm việc: {os.getcwd()}\n")
            f_det.write(f"Thư mục lưu Logs: {cls.LOG_DIR}\n")
            f_det.write("======================================================================\n\n")

    def log(self, message: str, detailed_info: str = None):
        """
        Ghi log đồng thời ra terminal, file log chính và file log chi tiết.
        :param message: Thông điệp log hiển thị ngắn gọn.
        :param detailed_info: Dữ liệu chi tiết đầy đủ (raw response, JSON, toàn bộ văn bản cào...)
        """
        print(message)
        
        # 1. Ghi log tóm tắt vào LOG_FILE
        with open(self.LOG_FILE, "a", encoding="utf-8") as f:
            f.write(message + "\n")

        # 2. Ghi log đầy đủ chi tiết vào LOG_FILE_DETAILED
        time_str = datetime.datetime.now().strftime("[%H:%M:%S]")
        with open(self.LOG_FILE_DETAILED, "a", encoding="utf-8") as f_det:
            f_det.write(f"{time_str} {message}\n")
            if detailed_info is not None:
                f_det.write("------------------- [BẮT ĐẦU DỮ LIỆU CHI TIẾT] -------------------\n")
                f_det.write(str(detailed_info).strip() + "\n")
                f_det.write("------------------- [KẾT THÚC DỮ LIỆU CHI TIẾT] -------------------\n\n")

    def setUp(self):
        """Kiểm tra cờ thất bại trước mỗi bước, ngắt ngay nếu bước trước lỗi."""
        if TestAdvancedMultiSourceOperations._pipeline_failed:
            self.skipTest("[PIPELINE ABORTED] Đã dừng do bước trước đó gặp lỗi.")

    @classmethod
    def tearDownClass(cls):
        """Tổng kết trạng thái sau khi chạy xong pipeline."""
        print("\n" + "=" * 60)
        summary_msg = ""
        if cls._pipeline_failed:
            summary_msg = "❌ PIPELINE THẤT BẠI: Quá trình test đã bị ngắt ngay khi gặp lỗi."
        else:
            summary_msg = "✅ PIPELINE THÀNH CÔNG: Đã thu thập và xử lý dữ liệu đa nguồn cho chủ đề cụ thể!"
        
        print(summary_msg)
        print(f"📄 Log tóm tắt: {cls.LOG_FILE}")
        print(f"📄 Log đầy đủ chi tiết: {cls.LOG_FILE_DETAILED}")
        print("=" * 60)

        # Ghi tổng kết vào cả 2 file log
        with open(cls.LOG_FILE, "a", encoding="utf-8") as f:
            f.write("\n" + summary_msg + "\n")
            
        with open(cls.LOG_FILE_DETAILED, "a", encoding="utf-8") as f_det:
            f_det.write("\n" + "=" * 70 + "\n")
            f_det.write(f"TỔNG KẾT: {summary_msg}\n")
            f_det.write(f"Thời điểm hoàn tất: {datetime.datetime.now()}\n")
            f_det.write("=" * 70 + "\n")

    def _mark_failed_and_stop(self, step_name: str, message: str, raw_error: str = None):
        """Đánh dấu lỗi, ghi log ngắt pipeline và dừng unittest."""
        error_msg = f"❌ [{step_name}] LỖI NGẮT PIPELINE: {message}"
        self.log(error_msg, detailed_info=raw_error or message)
        TestAdvancedMultiSourceOperations._pipeline_failed = True
        self.fail(error_msg)

    # =========================================================================
    # PIPELINE CÁC BƯỚC THỰC THI THU THẬP ĐA NGUỒN (MULTI-SOURCE)
    # =========================================================================

    def test_01_terminal_tool_environment_check(self):
        """Bước 1: Kiểm tra môi trường hệ thống bằng terminal_tool (action='run')."""
        self.log("\n[1/5] 💻 BƯỚC 1: Kiểm tra hệ thống bằng 'terminal_tool'...")
        
        try:
            res = terminal_tool_run(action="run", command="dir", cwd=os.getcwd(), timeout=15)
            output_text = str(res)
            
            if not output_text or "error" in output_text.lower():
                self._mark_failed_and_stop("Step 1", f"terminal_tool thực thi thất bại", raw_error=output_text)

            self.log(
                "      ✓ Thành công: terminal_tool hoạt động bình thường, đã sẵn sàng môi trường.",
                detailed_info=f"Kết quả thực thi lệnh 'dir':\n{output_text}"
            )
        except Exception as e:
            self._mark_failed_and_stop("Step 1", f"Ngoại lệ tại terminal_tool: {str(e)}", raw_error=str(e))

    def test_02_multisource_search_and_scrape(self):
        """Bước 2: Tìm kiếm theo chủ đề cụ thể và cào dữ liệu HÀNG LOẠT bằng 'scrape_many'."""
        self.log(f"\n[2/5] 🌐 BƯỚC 2: Thu thập dữ liệu ĐA NGUỒN đồng thời bằng 'scrape_many' cho chủ đề '{self.TARGET_SEARCH_QUERY}'...")
        
        try:
            # 1. Tìm kiếm danh sách nguồn liên quan chủ đề
            self.log(f"      -> Thực thi web_tool search (query='{self.TARGET_SEARCH_QUERY}', max_results={self.MAX_SEARCH_RESULTS})...")
            search_res = web_tool_run(action="search", query=self.TARGET_SEARCH_QUERY, max_results=self.MAX_SEARCH_RESULTS)
            
            if not search_res:
                self._mark_failed_and_stop("Step 2", "web_tool search không trả về kết quả.")

            # Ghi lại kết quả tìm kiếm thô vào log chi tiết
            self.log("      -> Đã nhận phản hồi tìm kiếm từ web_tool.", detailed_info=f"RAW Search Results:\n{search_res}")

            # Trích xuất danh sách tất cả các URL
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
                self._mark_failed_and_stop("Step 2", f"Không trích xuất được URL từ tìm kiếm", raw_error=str(search_res))

            # 2. Bộ lọc URL thông minh
            EXCLUDE_DOMAINS = [
                "youtube.com", "youtu.be", "facebook.com", "twitter.com", "x.com",
                "linkedin.com", "instagram.com", "tiktok.com", "reddit.com", "pinterest.com"
            ]
            EXCLUDE_EXTENSIONS = [".pdf", ".zip", ".exe", ".mp4", ".png", ".jpg"]

            candidate_urls = []
            seen_domains = set()

            for url in raw_urls:
                url_lower = url.lower()
                if any(domain in url_lower for domain in EXCLUDE_DOMAINS):
                    continue
                if any(url_lower.endswith(ext) for ext in EXCLUDE_EXTENSIONS):
                    continue

                domain_match = re.search(r'https?://([^/]+)', url_lower)
                if domain_match:
                    domain = domain_match.group(1)
                    if domain in seen_domains:
                        continue
                    seen_domains.add(domain)

                candidate_urls.append(url)

            self.log(
                f"      -> Lọc được {len(candidate_urls)} URL từ các tên miền độc lập khác nhau.",
                detailed_info=f"Danh sách URL đã lọc:\n" + "\n".join(candidate_urls)
            )

            if len(candidate_urls) < self.MIN_REQUIRED_SOURCES:
                self.log(f"      ⚠️ Số lượng URL độc lập ít hơn {self.MIN_REQUIRED_SOURCES}, nới lỏng bộ lọc trùng domain...")
                candidate_urls = [u for u in raw_urls if not any(d in u.lower() for d in EXCLUDE_DOMAINS)]

            # 3. Tiến hành cào dữ liệu ĐỒNG THỜI bằng action='scrape_many'
            self.log(f"      -> Thực thi web_tool action='scrape_many' cho {len(candidate_urls)} URL...")
            scrape_res = web_tool_run(
                action="scrape_many",
                urls=candidate_urls,
                force_js=True,
                max_chars=30000,
                timeout=15,
                output_format="markdown"
            )

            self.log("      -> Đã nhận phản hồi từ web_tool action='scrape_many'.", detailed_info=f"RAW Scrape Many Results:\n{scrape_res}")

            # PARSE KẾT QUẢ NẾU RUN TRẢ VỀ DẠNG CHUỖI (STR/JSON)
            if isinstance(scrape_res, str):
                try:
                    import json
                    scrape_res = json.loads(scrape_res)
                except Exception:
                    # Nếu không phải JSON, thử parse dạng dict literal hoặc giữ nguyên
                    try:
                        import ast
                        scrape_res = ast.literal_eval(scrape_res)
                    except Exception:
                        pass

            collected_sources = []
            error_keywords = ["lỗi:", "error:", "không thể trích xuất", "failed to fetch", "access denied", "403 forbidden"]

            # Xử lý theo dict (URL làm Key, Content làm Value)
            if isinstance(scrape_res, dict):
                for target_url, content_data in scrape_res.items():
                    res_str = ""
                    if isinstance(content_data, dict):
                        res_str = str(content_data.get("content") or content_data.get("text") or content_data.get("result") or "").strip()
                    else:
                        res_str = str(content_data).strip()

                    if not res_str or any(kw in res_str.lower() for kw in error_keywords) or len(res_str) < 200:
                        self.log(f"         ⚠️ Bỏ qua nguồn lỗi/ngắn ({target_url}): {res_str[:60]}...")
                        continue

                    collected_sources.append({
                        "source_id": len(collected_sources) + 1,
                        "url": target_url,
                        "content": res_str
                    })
                    self.log(f"         ✓ Thu thập thành công Nguồn #{len(collected_sources)} (Độ dài: {len(res_str)} ký tự) - [{target_url}]")

                    if len(collected_sources) >= self.MIN_REQUIRED_SOURCES:
                        break

            # Xử lý theo list các nguồn
            elif isinstance(scrape_res, list):
                for idx, item in enumerate(scrape_res):
                    target_url = candidate_urls[idx] if idx < len(candidate_urls) else f"source_{idx+1}"
                    res_str = ""

                    if isinstance(item, dict):
                        target_url = item.get("url") or item.get("link") or target_url
                        res_str = str(item.get("content") or item.get("text") or item.get("result") or "").strip()
                    else:
                        res_str = str(item).strip()

                    if not res_str or any(kw in res_str.lower() for kw in error_keywords) or len(res_str) < 200:
                        self.log(f"         ⚠️ Bỏ qua nguồn lỗi/ngắn ({target_url}): {res_str[:60]}...")
                        continue

                    collected_sources.append({
                        "source_id": len(collected_sources) + 1,
                        "url": target_url,
                        "content": res_str
                    })
                    self.log(f"         ✓ Thu thập thành công Nguồn #{len(collected_sources)} (Độ dài: {len(res_str)} ký tự) - [{target_url}]")

                    if len(collected_sources) >= self.MIN_REQUIRED_SOURCES:
                        break
            
            # Xử lý trường hợp scrape_res là 1 chuỗi văn bản đơn thuần (không parse được sang dict/list)
            elif isinstance(scrape_res, str) and len(scrape_res.strip()) > 200:
                collected_sources.append({
                    "source_id": 1,
                    "url": candidate_urls[0] if candidate_urls else "raw_response",
                    "content": scrape_res.strip()
                })
                self.log(f"         ✓ Thu thập thành công Dữ liệu dạng Chuỗi thô (Độ dài: {len(scrape_res)} ký tự)")

            if len(collected_sources) < 1:
                self._mark_failed_and_stop("Step 2", "Không thu thập được bất kỳ nguồn dữ liệu hợp lệ nào từ 'scrape_many'!")

            # Lưu vào dữ liệu tạm thời
            TestAdvancedMultiSourceOperations.temp_sources_data = collected_sources
            self.log(f"      ✓ BƯỚC 2 HOÀN THÀNH: Đã tổng hợp thành công {len(collected_sources)} nguồn dữ liệu qua 'scrape_many'.")

        except Exception as e:
            self._mark_failed_and_stop("Step 2", f"Ngoại lệ tại Step 2: {str(e)}", raw_error=str(e))

    def test_03_file_tool_aggregate_and_save(self):
        """Bước 3: Tổng hợp dữ liệu đa nguồn và ghi vào file bằng file_tool."""
        self.log("\n[3/5] 📁 BƯỚC 3: Tổng hợp và lưu trữ dữ liệu đa nguồn bằng 'file_tool'...")
        
        sources = getattr(TestAdvancedMultiSourceOperations, 'temp_sources_data', [])
        if not sources:
            self._mark_failed_and_stop("Step 3", "Không có dữ liệu đa nguồn từ Bước 2!")

        try:
            # 1. Tạo file tóm tắt danh sách các nguồn
            summary_content = f"=== TỔNG HỢP NGUỒN TÌM KIẾM CHỦ ĐỀ: '{self.TARGET_SEARCH_QUERY}' ===\n"
            summary_content += f"Thời gian thực hiện: {datetime.datetime.now()}\n"
            summary_content += f"Tổng số nguồn thu thập: {len(sources)}\n\n"

            raw_combined_content = f"=== DỮ LIỆU TỔNG HỢP ĐA NGUỒN CHỦ ĐỀ: '{self.TARGET_SEARCH_QUERY}' ===\n\n"

            for src in sources:
                url = src['url'].rstrip(")").strip()
                summary_content += f"[Nguồn #{src['source_id']}] {url}\n"
                
                raw_combined_content += f"==========================================================\n"
                raw_combined_content += f"SOURCE_ID: #{src['source_id']}\n"
                raw_combined_content += f"SOURCE_URL: {url}\n"
                raw_combined_content += f"==========================================================\n"
                raw_combined_content += src['content'] + "\n\n"

            # Đảm bảo có chuỗi mốc kiểm tra cho Bước 4
            raw_combined_content += "\n[METADATA_CHECK] Target reference domain: httpbin.org\n"

            # 2. Ghi file tóm tắt nguồn
            self.log(f"      -> Ghi danh sách nguồn vào: {self.FILE_SOURCES_SUMMARY}")
            file_tool_run(action="write", file_paths=[self.FILE_SOURCES_SUMMARY], content=summary_content, mode="w")

            # 3. Ghi file dữ liệu tổng hợp thô & file phục vụ chỉnh sửa
            self.log(f"      -> Ghi dữ liệu tổng hợp đa nguồn vào file RAW: {self.FILE_WEB_RAW}")
            file_tool_run(action="write", file_paths=[self.FILE_WEB_RAW], content=raw_combined_content, mode="w")

            self.log(f"      -> Ghi dữ liệu vào file MODIFIED: {self.FILE_WEB_MODIFIED}")
            file_tool_run(action="write", file_paths=[self.FILE_WEB_MODIFIED], content=raw_combined_content, mode="w")

            # 4. Đọc kiểm tra lại file đã ghi
            read_res = file_tool_run(action="read", file_paths=[self.FILE_WEB_RAW])
            read_res_str = str(read_res)
            
            if not read_res or len(read_res_str) == 0:
                self._mark_failed_and_stop("Step 3", "file_tool đọc file đa nguồn thất bại hoặc file rỗng.")

            self.log(
                "      ✓ Thành công: Đã ghi và xác minh dữ liệu tổng hợp đa nguồn vào hệ thống file.",
                detailed_info=f"Tóm tắt file tổng hợp ({len(read_res_str)} ký tự):\n{read_res_str[:1500]}...\n[Đã xem trước 1500 ký tự đầu tiên]"
            )

        except Exception as e:
            self._mark_failed_and_stop("Step 3", f"Ngoại lệ tại file_tool ở Step 3: {str(e)}", raw_error=str(e))

    def test_04_file_tool_edit_and_glob_search(self):
        """Bước 4: Xử lý chỉnh sửa văn bản hàng loạt & Kiểm tra file bằng find_by_glob."""
        self.log("\n[4/5] 📝 BƯỚC 4: Chỉnh sửa dữ liệu tổng hợp & Tìm kiếm file bằng 'find_by_glob'...")
        
        try:
            # 1. Tìm kiếm từ khóa chuẩn hóa trong file tổng hợp
            self.log("      -> Tìm kiếm chuỗi đánh dấu trong file đa nguồn bằng file_tool (action='search')...")
            search_in_file = file_tool_run(action="search", file_paths=[self.FILE_WEB_MODIFIED], queries=["httpbin.org"])
            self.log("      -> Kết quả tìm kiếm từ khóa trong file.", detailed_info=f"Search Result:\n{search_in_file}")

            # 2. Thực hiện thay thế/chuẩn hóa nội dung trong file tổng hợp
            self.log("      -> Chuẩn hóa/Thay thế nội dung trong file bằng file_tool (action='replace')...")
            replace_res = file_tool_run(
                action="replace",
                file_paths=[self.FILE_WEB_MODIFIED],
                queries=["httpbin.org"],
                replacements=["PROCESSED_MULTISOURCE_TARGET_2026"]
            )
            self.log("      -> Kết quả thực thi thay thế.", detailed_info=f"Replace Result:\n{replace_res}")

            # 3. Đọc lại để kiểm tra thay thế
            updated_content = str(file_tool_run(action="read", file_paths=[self.FILE_WEB_MODIFIED]))
            if "PROCESSED_MULTISOURCE_TARGET_2026" not in updated_content:
                self._mark_failed_and_stop("Step 4", "Nội dung file đa nguồn chưa được thay thế thành công!", raw_error=updated_content[-1000:])

            # 4. Quét danh sách file log và dữ liệu đã lưu bằng find_by_glob
            self.log("      -> Tìm kiếm danh sách file kết quả bằng 'find_by_glob'...")
            glob_res = glob_tool_run(pattern="*.txt", root_dir=self.LOG_DIR, recursive=False)
            glob_str = str(glob_res)
            
            if "multisource_content_raw.txt" not in glob_str or "sources_summary.txt" not in glob_str:
                self._mark_failed_and_stop("Step 4", f"find_by_glob không quét thấy đủ các file dữ liệu đa nguồn", raw_error=glob_str)

            self.log(
                "      ✓ Thành công: Đã hoàn tất chỉnh sửa dữ liệu đa nguồn và quét mẫu glob thành công.",
                detailed_info=f"Kết quả quét Glob đầy đủ:\n{glob_str}"
            )

        except Exception as e:
            self._mark_failed_and_stop("Step 4", f"Ngoại lệ tại Step 4: {str(e)}", raw_error=str(e))

    def test_05_terminal_launch_and_final_verification(self):
        """Bước 5: Chạy tác vụ bất đồng bộ bằng terminal_tool (action='launch') & Xác minh tổng thể."""
        self.log("\n[5/5] 🚀 BƯỚC 5: Thử nghiệm 'launch' tác vụ hậu xử lý & Kiểm tra toàn vẹn...")
        
        try:
            self.log("      -> Thực thi lệnh bất đồng bộ ngầm bằng action='launch'...")
            launch_cmd = "cmd.exe /c echo MultiSource Pipeline Completed > NUL" if os.name == 'nt' else "echo MultiSource Pipeline Completed"
            launch_res = terminal_tool_run(action="launch", command=launch_cmd)
            self.log("      -> Kết quả thực thi launch.", detailed_info=f"Launch Response:\n{launch_res}")

            # Kiểm tra sự tồn tại của tất cả các file đầu ra quan trọng
            required_files = [
                self.LOG_FILE,
                self.LOG_FILE_DETAILED,
                self.FILE_SOURCES_SUMMARY,
                self.FILE_WEB_RAW,
                self.FILE_WEB_MODIFIED
            ]
            
            files_verification_info = []
            for fpath in required_files:
                if not os.path.exists(fpath):
                    self._mark_failed_and_stop("Step 5", f"Thiếu file đầu ra bắt buộc: {fpath}")
                
                size = os.path.getsize(fpath)
                mtime = datetime.datetime.fromtimestamp(os.path.getmtime(fpath))
                files_verification_info.append(f"File: {os.path.basename(fpath)} | Path: {fpath} | Size: {size} bytes | Last Modified: {mtime}")

            self.log(
                "      ✓ Thành công: Đã xác minh đầy đủ toàn bộ file dữ liệu đa nguồn và file log.",
                detailed_info="Thông tin chi tiết các file kiểm tra:\n" + "\n".join(files_verification_info)
            )

        except Exception as e:
            self._mark_failed_and_stop("Step 5", f"Ngoại lệ tại Step 5: {str(e)}", raw_error=str(e))


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2, failfast=True)
    suite = unittest.TestLoader().loadTestsFromTestCase(TestAdvancedMultiSourceOperations)
    runner.run(suite)