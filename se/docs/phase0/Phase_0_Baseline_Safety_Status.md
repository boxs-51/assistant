# Phase 0 — Baseline / Safety

## 1. Mục tiêu

Phase 0 theo roadmap có nhiệm vụ tạo một baseline có thể kiểm chứng trước khi tiếp tục refactor:

- Biết hệ thống hiện tại import và khởi động được đến đâu.
- Có test bảo vệ cho provider contract.
- Có E2E cho chat, embeddings, models, files và authentication.
- Có configuration/feature flags để refactor hoặc cutover an toàn.
- Có baseline metric: latency, error rate, streaming success, fallback, models, embeddings, files và auth.

Phase 0 không phải phase tạo feature mới; đây là phase tạo “điểm xuất phát có thể đo được”.

---

## 2. Trạng thái hiện tại

| Hạng mục | Trạng thái |
|---|---|
| Cấu trúc source hiện hữu có thể được phân tích/compile | Hoàn thành |
| Test architecture riêng cho các phase sau | Đã có một phần |
| `tests/architecture/test_imports.py` theo roadmap | Chưa có |
| Provider contract test theo roadmap | Chưa có |
| E2E legacy chat | Chưa có |
| E2E legacy embeddings | Chưa có |
| E2E legacy models | Chưa có |
| E2E legacy files | Chưa có |
| E2E auth | Chưa có |
| Feature flag `provider_runtime_enabled` | Chưa thấy triển khai đúng roadmap |
| Feature flag `execution_runtime_enabled` | Chưa thấy triển khai đúng roadmap |
| Feature flag `context_runtime_enabled` | Chưa thấy triển khai đúng roadmap |
| `new_chat_path_percentage` | Chưa thấy triển khai đúng roadmap |
| Baseline p50/p95/error rate | Chưa có evidence trong source archive |
| Baseline streaming success | Chưa có evidence |
| Baseline fallback success | Chưa có evidence |
| Baseline models/embeddings/files/auth | Chưa có evidence |

### Kết luận

**Phase 0 chưa hoàn thành.**

Đây là phase có khoảng cách rõ nhất giữa roadmap và source hiện tại. Codebase đã chuyển sang triển khai trực tiếp Phase 1–4, nhưng bộ “safety net” mà Phase 0 yêu cầu chưa được xây dựng đầy đủ.

---

## 3. Evidence từ source

Trong archive hiện có các test:

```text
tests/architecture/test_phase1.py
tests/architecture/test_phase2.py
tests/architecture/test_phase3.py
tests/architecture/test_phase4_execution.py
tests/architecture/test_phase4_multi_agent.py
```

Nhưng không có:

```text
tests/architecture/test_imports.py
tests/contracts/test_provider_contract.py

tests/e2e/test_legacy_chat.py
tests/e2e/test_legacy_embeddings.py
tests/e2e/test_legacy_models.py
tests/e2e/test_legacy_files.py
tests/e2e/test_auth.py
```

Điều này cho thấy test hiện tại đang ưu tiên **architecture phase verification**, không phải baseline/E2E regression suite theo thiết kế ban đầu.

---

## 4. Kiểm tra kỹ thuật được thực hiện trên archive

Toàn bộ các file Python trong archive có thể compile bằng `py_compile` mà không phát hiện lỗi syntax:

```text
Python files được kiểm tra: 254
Syntax errors: 0
```

Tuy nhiên, chạy toàn bộ `pytest -q` trong môi trường phân tích không thể hoàn tất vì môi trường thiếu dependency runtime:

```text
ModuleNotFoundError: No module named 'structlog'
```

Do đó:

- Không được xem kết quả hiện tại là “test suite pass”.
- Source có test nhưng chưa có bằng chứng execution đầy đủ trong môi trường này.
- Cần chạy lại test trong đúng environment/project dependency của developer trước khi đánh dấu Phase 0 hoàn thành.

---

## 5. Rủi ro nếu tiếp tục mà không hoàn tất Phase 0

### 5.1. Không có regression baseline

Có thể refactor một behavior cũ mà không phát hiện:

```text
chat
embeddings
models
files
auth
```

bị thay đổi.

### 5.2. Không đo được parity giữa Legacy Router và Runtime mới

Đặc biệt quan trọng ở Phase 3 vì source hiện tại có:

```text
LegacyModelRouter
LegacyModelRouterFacade
ProviderRuntime
```

song song.

### 5.3. Không có số liệu để quyết định cutover

Roadmap định hướng:

```text
5%
→ 25%
→ 50%
→ 100%
```

nhưng archive chưa có evidence về cơ chế percentage rollout hoặc metric so sánh.

---

## 6. Những gì Phase 0 thực sự đã đạt được

Dù chưa hoàn chỉnh, Phase 0 đã có một phần nền tảng gián tiếp:

- Source được tổ chức thành nhiều subsystem rõ ràng.
- Có test architecture theo từng phase.
- Có Pydantic domain schemas.
- Có provider abstraction và provider discovery.
- Có observability/logging/tracing components.
- Có circuit breaker, retry và routing policy.
- Có legacy compatibility path giúp giảm rủi ro khi chuyển architecture.

Nói cách khác:

```text
Safety infrastructure: một phần có
Baseline verification: thiếu
E2E regression protection: thiếu
Rollout control: thiếu
```

---

## 7. Definition of Done còn thiếu

Phase 0 chỉ nên được đánh dấu hoàn thành khi có ít nhất:

```text
tests/architecture/test_imports.py
tests/contracts/test_provider_contract.py
tests/e2e/test_legacy_chat.py
tests/e2e/test_legacy_embeddings.py
tests/e2e/test_legacy_models.py
tests/e2e/test_legacy_files.py
tests/e2e/test_auth.py
```

và có baseline được lưu:

```text
p50 latency
p95 latency
error rate
stream success
fallback success
model list success
embedding success
file operation success
auth success
```

---

## 8. Đề xuất kết thúc Phase 0

Không cần làm lại architecture. Chỉ cần bổ sung lớp kiểm chứng:

```text
Phase 0 completion
        |
        +-- Import/architecture tests
        +-- Provider contract tests
        +-- Legacy E2E
        +-- Runtime startup smoke test
        +-- Baseline metrics
        +-- Feature flags
```

Sau khi hoàn tất, Phase 1–3 có thể được đánh giá lại bằng dữ liệu thực tế thay vì chỉ static inspection.

---

## 9. Kết luận

**Current status: `IMPLEMENTATION COMPLETE / LIVE VERIFICATION PENDING`**

Đã bổ sung bộ Phase 0 implementation vào source working tree:

```text
src/infrastructure/feature_flags/
tests/smoke/test_imports.py
tests/smoke/test_feature_flags.py
tests/contracts/test_provider_contract.py
tests/e2e/test_legacy_chat.py
tests/e2e/test_legacy_embeddings.py
tests/e2e/test_legacy_models.py
tests/e2e/test_legacy_files.py
tests/e2e/test_auth.py
tools/phase0_baseline.py
PHASE_0_RUNBOOK.md
```

Đồng thời đã bổ sung request latency/success/failure/in-flight metrics và đưa `runtime_flags` vào `ConfigSchema`. Rollout chat mặc định ở `0%` để không tự động chuyển traffic sang path mới.

Phần còn lại phụ thuộc environment chạy thật: test suite cần đầy đủ dependency (`structlog` hiện không có trong sandbox), Gateway phải được khởi động và E2E phải chạy với provider/API credentials thực tế. Vì vậy chưa thể trung thực đánh dấu `PRODUCTION VERIFIED` chỉ từ sandbox này.
