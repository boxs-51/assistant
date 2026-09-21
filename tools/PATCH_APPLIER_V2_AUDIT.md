# Audit toàn diện `patch_applier.py` và thiết kế `patch_applier_v2.py`

## 1. Kết luận

Bản cũ có nhiều trường hợp `--check` có thể báo hợp lệ nhưng apply vẫn sửa sai vị trí hoặc làm thay đổi source ngoài ý định. Nguyên nhân chính không nằm ở một bug đơn lẻ mà ở mô hình áp patch: bỏ qua tọa độ hunk, fallback fuzzy quá rộng, không phát hiện match mơ hồ, ADD/DELETE không xác minh state đầy đủ, và commit nhiều file không có rollback thực sự.

Bản `patch_applier_v2.py` đổi mặc định sang **fail-closed**: nếu không chứng minh được vị trí hoặc state đủ an toàn thì từ chối apply thay vì đoán.

## 2. Các lỗi/rủi ro chính của bản cũ

1. **Bỏ qua tọa độ unified hunk** `@@ -old,count +new,count @@`; header chỉ dùng như marker bắt đầu hunk. Vì vậy tool không dùng vị trí do patch cung cấp để disambiguate.
2. **Fuzzy bằng `strip()` xóa cả indentation đầu dòng**. Với Python/YAML, hai block khác scope có thể bị coi là giống nhau.
3. **Blank-tolerant trả về index lùi** (`idx - start_offset`) nhưng vẫn replace theo độ dài `old_lines` ban đầu; nếu blank thực tế không tồn tại, có thể ăn vào dòng trước context.
4. **Không phát hiện nhiều match giống nhau**. `find_sequence*()` chỉ trả match đầu tiên nên tool có thể sửa nhầm occurrence.
5. **Pure insertion không có old context** được chèn tại `search_idx`, không nhất thiết là vị trí `@@` yêu cầu.
6. **ADD không kiểm tra file đã tồn tại**; `--check` có thể OK rồi apply overwrite file hiện hữu.
7. **DELETE không verify nội dung**; chỉ cần path tồn tại là xóa, kể cả source đã thay đổi sau khi patch được tạo.
8. **Reverse ADD -> DELETE không verify nội dung file hiện tại**, nên có nguy cơ xóa file mà người dùng đã sửa sau khi patch tạo nó.
9. **Reverse DELETE** cố tái dựng file từ hunk nhưng không bảo đảm patch chứa đủ toàn bộ nội dung để restore.
10. **Nhiều action cùng một filepath** được prepare độc lập từ disk state ban đầu; action sau có thể overwrite kết quả action trước.
11. **“Atomic” chỉ ở bước planning**. Giai đoạn write/delete chạy tuần tự; lỗi I/O giữa chừng có thể để repo ở trạng thái nửa patch.
12. **Write trực tiếp bằng `open(..., "w")`** có thể truncate file trước khi write hoàn tất.
13. **Không chống path traversal / absolute path / symlink escape**; patch độc hại có thể ghi ra ngoài repo root.
14. **Không chống TOCTOU**: file có thể bị process khác sửa sau planning nhưng trước commit.
15. **CRLF/LF**: chỉ cần file chứa CRLF là toàn file có thể bị normalize sang CRLF; mixed newline không được nhận diện.
16. **Không xử lý đúng `\ No newline at end of file`** khi trạng thái final newline thay đổi.
17. **Unified path có quote/space**, rename/binary/mode-only diff chưa được xử lý rõ ràng; có trường hợp bị parse sai hoặc bị bỏ qua ngầm.
18. **`.rej` có thể bị overwrite** nếu cùng một file có nhiều action partial.
19. **UTF-8 lỗi** chỉ phát hiện lúc open; không có thông báo rõ policy encoding.
20. **Không preserve mode cho file ADD 100755**.

## 3. Mô hình an toàn của v2

### Parser

- Hỗ trợ:
  - `*** Add File:`
  - `*** Update File:`
  - `*** Delete File:`
  - unified diff
  - git diff text
- Parse và kiểm tra `old_start/old_count/new_start/new_count`.
- Validate số dòng thực tế của hunk với header.
- Không nhầm cặp dòng `--- ...` / `+++ ...` nằm trong nội dung hunk thành file header mới.
- Binary patch, rename/copy, mode-change existing file: **reject explicit** thay vì silent-ignore.

### Matching

- Mặc định **exact match**.
- Hunk có tọa độ: ưu tiên exact tại vị trí dự kiến; nếu source lệch thì relocation theo khoảng cách, giới hạn `--max-offset`.
- Hunk custom không có tọa độ: nếu có nhiều occurrence phù hợp thì **fail ambiguity**.
- Không còn `strip()` đầu dòng.
- `--fuzzy-trailing-space` chỉ bỏ space/tab **cuối dòng**, là opt-in.
- Pure insertion không có tọa độ bị từ chối thay vì chèn tại vị trí đoán.

### ADD

- File chưa tồn tại: tạo mới.
- File đã tồn tại: mặc định lỗi.
- `--force`: cho phép overwrite có cảnh báo/PARTIAL.
- Reverse ADD -> DELETE: verify file vẫn đúng nội dung patch từng tạo; nếu đã sửa thì từ chối xóa.

### UPDATE

- Apply tuần tự trên **virtual staged filesystem**, nên 2 action liên tiếp cùng filepath thấy kết quả của nhau.
- Hunk fail ở mode mặc định => toàn transaction không commit.
- `--force` => áp hunk tốt, reject hunk lỗi, tạo `.rej`.

### DELETE

- Unified DELETE có hunk: phải apply được toàn bộ hunk và kết quả phải rỗng mới cho xóa.
- Custom DELETE không có expected content: mặc định từ chối vì unverifiable.
- `--force` mới cho unverified delete theo path.
- Reverse standard DELETE chỉ được thực hiện nếu patch chứa đủ dữ liệu để tái tạo file.

### Filesystem safety

- `--root` giới hạn mọi path vào project root.
- Reject absolute path, UNC, drive path, `..`, direct symlink.
- Snapshot bytes trước planning; verify lại ngay trước commit để phát hiện external change/TOCTOU.
- Write bằng temp file + `os.replace()`.
- Existing file được move sang backup trước replace/delete.
- Nếu commit lỗi giữa chừng: rollback các file đã commit.
- Nếu rollback bản thân thất bại: `.bak` còn lại được giữ, không bị xóa trong `finally`.

### Newline / encoding / mode

- UTF-8 / UTF-8 BOM; encoding khác bị từ chối để tránh phá file.
- Preserve CRLF hoặc LF với file thống nhất.
- Mixed newline mặc định reject; chỉ normalize khi user bật `--allow-mixed-newlines`.
- Hỗ trợ `\ No newline at end of file` cả apply và reverse.
- Hỗ trợ `new file mode 100755`.

## 4. Ma trận tình huống sử dụng

| Trường hợp | V2 mặc định |
|---|---|
| UPDATE exact, unique | Apply |
| UPDATE context xuất hiện nhiều nơi, không có tọa độ | Reject ambiguity |
| UPDATE lệch indentation | Reject |
| Chỉ lệch trailing spaces | Reject; bật `--fuzzy-trailing-space` nếu muốn |
| Unified hunk lệch vị trí <= `--max-offset` và nearest unique | Relocate + apply |
| Unified hunk lệch quá xa | Reject |
| Pure insertion có unified coordinate | Apply đúng coordinate |
| Pure insertion custom không coordinate | Reject |
| ADD path chưa tồn tại | Create |
| ADD path đã tồn tại | Reject |
| ADD existing + `--force` | Overwrite, PARTIAL/warning |
| Reverse ADD nhưng file đã bị sửa | Reject, không xóa |
| DELETE unified source đúng | Delete |
| DELETE unified source đã drift | Reject, không xóa |
| Custom DELETE không expected content | Reject |
| Custom DELETE + `--force` | Delete, PARTIAL/warning |
| 2 UPDATE action cùng file | Apply tuần tự trên staged state |
| Action 2 fail, default mode | Không commit action 1 |
| Commit file 2 lỗi sau file 1 | Rollback file 1 |
| File đổi sau planning | Reject trước commit |
| Patch path `../...` / absolute / UNC | Reject |
| Symlink target | Reject |
| CRLF source | Preserve CRLF |
| Mixed newline | Reject mặc định |
| UTF-8 BOM | Preserve encoding policy |
| New file `100755` | Preserve executable mode |
| Binary/rename/copy/mode-only | Reject rõ ràng |
| Reverse DELETE thiếu dữ liệu gốc | Reject |

## 5. Regression đã chạy

24/24 case pass:

- exact custom UPDATE
- ambiguity reject
- indentation safety
- opt-in trailing-space fuzzy
- ADD existing reject trong cả check/apply
- ADD + reverse guard
- DELETE source mismatch reject
- DELETE success + reverse
- multiple actions same file staged sequentially
- path traversal reject
- pure insertion coordinate
- reverse UPDATE
- CRLF preserve
- mixed newline reject
- check không mutate
- force partial + `.rej`
- hunk content giống `---/+++` header
- unverified custom DELETE policy
- source race detection
- transaction rollback khi commit fail giữa 2 file
- `100755` new-file mode
- final newline remove + reverse
- final newline add
- binary reject
- rename reject

## 6. CLI khuyến nghị

```powershell
# Kiểm tra sạch trước
py tools\patch_applier_v2.py --check --root . docs\change.patch

# Apply
py tools\patch_applier_v2.py --root . docs\change.patch

# Reverse
py tools\patch_applier_v2.py -R --root . docs\change.patch

# Chỉ khi patch lệch trailing spaces
py tools\patch_applier_v2.py --check --fuzzy-trailing-space --root . docs\change.patch

# Best-effort / partial + .rej (rủi ro cao hơn)
py tools\patch_applier_v2.py --force --root . docs\change.patch
```

## 7. Giới hạn cố ý

- Không apply binary patch.
- Không rename/copy file trong một action; hãy biểu diễn rõ thành delete/add nếu cần.
- Không chmod existing file bằng mode-only diff.
- Không tự đoán encoding ngoài UTF-8/UTF-8 BOM.
- Không tự bỏ indentation để “cố match”.
- Custom insertion-only hunk không có tọa độ/context bị từ chối.

Các giới hạn này là chủ ý: công cụ patch dùng cho source code nên **false negative an toàn hơn false positive làm hỏng repo**.
