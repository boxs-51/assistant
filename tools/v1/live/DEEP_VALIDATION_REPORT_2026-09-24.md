# Kiểm thử sâu Tools V1 trên Windows — 2026-09-24

## Phạm vi và kết luận

Kiểm thử trên `E:\assistant`, `main@3c6ffa0`, Python 3.12.10, Windows 10. Đã chạy cả sáu nhóm File, Glob, Terminal, Web, Window, Desktop bằng runtime thật. Các ca live LOCAL, NETWORK, GUI đều `PASS`; 26/26 ca probe có mạng và 37/37 ca probe có GUI khớp kết quả mong đợi. Bộ pytest có **280 passed, 8 skipped, 4 failed, 159 subtests passed**. Bốn lỗi pytest đều là so sánh `\r\n` với `\n` trên Windows. Vì vậy chưa thể gọi toàn bộ bộ kiểm thử là xanh.

Đây là kết quả tại máy này và thời điểm này. Hai ca Web phụ thuộc mạng/nguồn công khai; GUI phụ thuộc phiên desktop đang mở. Không có thay đổi vào mã runtime của sáu tool.

## Cách chạy và bằng chứng

Các ca live dùng cờ opt-in của repo. Tệp tạm chỉ nằm dưới system Temp; tiến trình GUI và Terminal do harness sở hữu được đóng sau chạy. JSON trong repo đã giới hạn chuỗi dài và danh sách lớn bằng độ dài cùng mẫu đầu, để không nhét nhiều MB output vào tài liệu.

```powershell
# Hồi quy (chạy với quyền ghi thư mục tạm trên Windows)
E:\assistant\venv\Scripts\python.exe -m pytest -q tools/v1/test -p no:cacheprovider --basetemp E:\assistant\.tmp_tools_v1_pytest_elevated --tb=line

$env:RUN_TOOLS_V1_LIVE='1'
E:\assistant\venv\Scripts\python.exe -m tools.v1.live.local_scenarios
$env:RUN_TOOLS_V1_LIVE_NETWORK='1'
E:\assistant\venv\Scripts\python.exe -m tools.v1.live.network_scenarios
E:\assistant\venv\Scripts\python.exe -m tools.v1.live.deep_validation tools/v1/live/DEEP_VALIDATION_EVIDENCE_2026-09-24.json
Remove-Item Env:RUN_TOOLS_V1_LIVE_NETWORK
$env:RUN_TOOLS_V1_LIVE_GUI='1'
E:\assistant\venv\Scripts\python.exe -m tools.v1.live.gui_scenarios
E:\assistant\venv\Scripts\python.exe -m tools.v1.live.deep_validation tools/v1/live/DEEP_VALIDATION_GUI_EVIDENCE_2026-09-24.json
```

Lệnh probe từ chối ghi đè JSON đã tồn tại. Hai tệp bằng chứng chi tiết là [ca local/network](DEEP_VALIDATION_EVIDENCE_2026-09-24.json) và [ca GUI](DEEP_VALIDATION_GUI_EVIDENCE_2026-09-24.json). Script tái chạy là [deep_validation.py](deep_validation.py). JSON lưu phản hồi `ok`, `tool`, `action`, `data`, `error`, `meta` của từng lời gọi (giới hạn mẫu cho nội dung lớn).

Live harness sinh ba `evidence.json` ngoài repo:

| Nhóm | Kết quả | Bước đã chạy | Cleanup |
|---|---|---|---|
| LOCAL | PASS | Terminal `run`; File `write/read`; Glob `find`; Terminal `launch` | 1 PID sở hữu đã kết thúc; không còn PID sống/lỗi |
| NETWORK | PASS | Web `search`, `scrape` | Không có tiến trình sở hữu |
| GUI | PASS | Terminal `launch`; Window `find/focus/get_geometry/find/close`; Desktop `mouse_click/type_text` | 1 PID sở hữu đã kết thúc; không còn PID sống/lỗi |

Đường dẫn evidence live tại thời điểm chạy: `C:\Users\ADMIN\AppData\Local\Temp\tools-v1-live-c9eac3f9e3494eccbdf67e08b44f20f4-b0yph5o1\evidence.json` (LOCAL), `...\tools-v1-live-b4d4034af15a4fd4bee433b204261448-banhjbin\evidence.json` (NETWORK), `...\tools-v1-live-936b432ada0b40f78078b38c9d582e2c-x5pnnu3o\evidence.json` (GUI). Các thư mục Temp có thể được hệ thống dọn sau này; hai JSON trong repo là bằng chứng được giữ lại.

## Phản hồi thực tế theo tool

`PASS` ở bảng dưới nghĩa là phản hồi khớp với điều kiện kiểm tra của ca đó. Với đầu vào lỗi có chủ đích, `ok=false` là hành vi mong đợi. Các trường không liệt kê ở đây có trong JSON; mọi phản hồi đều có `meta.version="2.0.0"`.

### File tool

| Ca / đầu vào | Phản hồi quan sát | Đánh giá |
|---|---|---|
| `write` chuỗi `alpha\nbeta\nalpha\n` | `ok=true`, `created=true`, `changed=true`, `input_bytes=17`, `final_size_bytes=17`, SHA-256 trả về | PASS |
| `read` dòng 2, một dòng | `ok=true`, `content="beta\n"`, `returned_line_count=1`, `next_start_line=3`, `eof=false` | PASS |
| `search` regex `a[a-z]+a` | `ok=true`, `total_match_count=2`, `returned_count=2`; vị trí dòng 1 và 3 | PASS |
| `replace` `alpha` thành `gamma` | `ok=true`, `replacement_count=2`; đọc lại cho `gamma\nbeta\ngamma\n`, `eof=true` | PASS |
| Đọc file không tồn tại | `ok=false`, `error.code=FILE_NOT_FOUND`, `retryable=false` | PASS |
| Regex `[` lỗi | `ok=false`, `error.code=FILE_REGEX_INVALID`, `details.query_index=0` | PASS |
| `search` 80 đường dẫn | `ok=false`, `INVALID_ARGUMENT`: `file_paths must contain between 1 and 32 paths` | PASS: giới hạn được áp dụng |
| `search` 32 file × 10 lần xuất hiện | `ok=true`, `total_match_count=320`, `returned_count=160` (giới hạn 5/file), `meta.truncated=true` | PASS |
| `write` một dòng 900.000 byte | `ok=true`, `input_bytes=900000`, `final_size_bytes=900000` | PASS |
| `read` dòng 900.000 ký tự với `max_chars=4096` | `ok=false`, `OUTPUT_LIMIT_EXCEEDED`: không cắt giữa một dòng | PASS: chặn trả lời không trọn dòng |
| `write` 300 dòng, tổng 900.000 byte; `read max_chars=4096` | `ok=true`, `size_bytes=900000`, trả đúng 3.000 ký tự/1 dòng, `next_start_line=2`, `eof=false`, `meta.truncated=true` | PASS |

### Glob tool

| Ca / đầu vào | Phản hồi quan sát | Đánh giá |
|---|---|---|
| 600 file `.txt`, `max_results=25` | `ok=true`, `returned_count=25`, `meta.truncated=true`; mẫu đầu `item-0000.txt`… | PASS |
| Cùng 600 file, `max_results=1000` | `ok=true`, `returned_count=600`, `meta.truncated=false` | PASS |
| `max_results=5001` | `ok=false`, `INVALID_ARGUMENT`: `max_results must be within [1, 5000]` | PASS |

### Terminal tool

| Ca / đầu vào | Phản hồi quan sát | Đánh giá |
|---|---|---|
| Python in `out`/`err`, thoát mã 7 | `ok=true`, `data.exit_code=7`, `stdout="out\r\n"`, `stderr="err\r\n"`, mỗi stream 5 byte | PASS; `ok` phản ánh lệnh được chạy và thu kết quả, không đồng nghĩa exit code 0 |
| Python ngủ 3 giây, timeout 1 giây | `ok=false`, `TERMINAL_TIMEOUT`, `timeout_seconds=1`, thời gian quan sát khoảng 1,05 giây | PASS |
| Python in 5 MB stdout | `ok=false`, `TERMINAL_OUTPUT_LIMIT`, `overflow_stream=stdout`, giới hạn stdout 4.194.304 byte/tổng 8.388.608 byte; `stdout_bytes_observed` khoảng 4,65 MB trước cleanup | PASS |
| `cwd` không tồn tại | `ok=false`, `TERMINAL_CWD_NOT_FOUND` | PASS |
| Live `launch` | `ok=true`, `started=true`, PID được trả; harness xác nhận cleanup không còn PID sống | PASS |

### Web tool

| Ca / đầu vào | Phản hồi quan sát | Đánh giá |
|---|---|---|
| `search("IANA example domain", max_results=10)` | `ok=true`, `returned_count=10`, `provider=duckduckgo_html`; kết quả đầu là trang IANA reserved domains | PASS |
| `scrape_many` hai URL `example.com` và IANA reserved domains | `ok=true`, `requested_count=2`, `succeeded_count=2`, `failed_count=0`; cả hai kết quả HTTP 200; `example.com` trả 113 ký tự bằng phương thức `dynamic`, IANA trả 2.279 ký tự bằng `static` | PASS |
| `scrape(file:///etc/passwd)` | `ok=false`, `WEB_URL_BLOCKED`: chỉ cho HTTP/HTTPS | PASS |
| Action không tồn tại | `ok=false`, `INVALID_ARGUMENT` | PASS |
| Live search rồi scrape URL khám phá được | Hai bước `PASS` qua structured `ToolResult` | PASS |

### Window và Desktop

GUI probe chỉ thao tác cửa sổ Tkinter riêng do harness tạo. Phản hồi có trong JSON GUI, gồm cả những lần `find` trả danh sách rỗng khi cửa sổ đang khởi động (đây là phản hồi `ok=true`, không phải lỗi).

| Ca / action | Phản hồi quan sát | Đánh giá |
|---|---|---|
| Terminal `launch` GUI | `ok=true`, `started=true`, trả PID gốc | PASS |
| Window `find` trong giai đoạn chờ | Bốn lần `ok=true`, `returned_count=0`; lần sau `returned_count=1`, selector gồm handle và PID của cửa sổ sở hữu | PASS |
| Window `focus` | `ok=true`, `confirmed=true`, trả đúng selector | PASS |
| Window `get_geometry` | `ok=true`; overall 496×199, client area 480×160; đủ tọa độ để click bên trong | PASS |
| Desktop `mouse_click` | `ok=true`, `(x,y)=(368,231)`, `button=left`, `clicks=1` | PASS |
| Desktop `type_text` | `ok=true`, `character_count=37`, `method=pyautogui` | PASS |
| Window `find` sau khi gõ | `ok=true`, một cửa sổ cùng handle/PID, title có hậu tố `_TYPED` | PASS |
| Window `close` | `ok=true`, `closed=true`; harness cleanup không còn PID sống | PASS |
| Action sai ở cả Window và Desktop | Mỗi tool trả `ok=false`, `INVALID_ARGUMENT`, không có tác động GUI | PASS |

## Pytest: lỗi còn mở

Lượt đầu trong sandbox trả nhiều `WinError 5` ở thư mục tạm, kể cả `--basetemp` trong repo; pytest còn lỗi khi dọn temp. Đây không phải kết quả chức năng. Lượt chạy có quyền ghi temp cho kết quả đầy đủ: `4 failed, 280 passed, 8 skipped, 159 subtests passed in 13.65s`. Bốn failure:

1. `test_file_tool.py::TestFileToolV2::test_read_full_and_line_pagination`: kỳ vọng `\n`, nhận `\r\n`.
2. `test_terminal_tool.py::TestTerminalToolV2::test_execute_dispatch_and_standalone_entrypoint`: kỳ vọng `ok\n`, nhận `ok\r\n`.
3. `test_terminal_tool.py::TestTerminalToolV2::test_nonzero_exit_is_successful_execution_data`: kỳ vọng `bad\n`, nhận `bad\r\n`.
4. `test_terminal_tool.py::TestTerminalToolV2::test_run_exit_zero_and_exact_whitespace`: kỳ vọng `  out\n\n`, nhận `  out\r\n\r\n`.

Các lỗi này cho thấy bộ test đang giả định newline kiểu Unix khi chạy trên Windows. Probe trực tiếp xác nhận Terminal giữ `\r\n` thực tế trong stdout/stderr; chưa thay đổi mã tool hay test vì phạm vi yêu cầu là đánh giá. Muốn gate Windows xanh cần quyết định hợp đồng newline mong muốn và cập nhật test hoặc runtime tương ứng.

## Nhận định cuối

Các đường chạy chính của sáu tool hoạt động trên máy này, và các giới hạn đầu vào/output, timeout, cleanup phản hồi có cấu trúc đúng ở những ca đã thử. Hạng mục còn mở là bốn assertion newline của pytest. Dữ liệu mạng có thể khác ở lần chạy sau; script hiện yêu cầu đủ 10 kết quả search và hai trang scrape thành công, nên một lần chạy thất bại do mạng cần được phân loại lại qua `error.code`, không mặc định là lỗi logic của tool.
