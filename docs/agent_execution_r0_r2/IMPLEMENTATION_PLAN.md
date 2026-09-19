# Kế hoạch triển khai R0–R2

Ngày thực hiện: 2026-09-19  
Nguồn: `docs/AGENT_EXECUTION_CONTINUATION_BRANCHING_ROADMAP_V2.md`

## Phạm vi

- R0: đóng băng hợp đồng kiến trúc và các ma trận quyết định.
- R1: chuẩn hóa trạng thái execution thành `WAITING + wait_reason`, đồng thời đọc được payload cũ.
- R2: `AgentRuntime` sở hữu vòng đời execution bền vững; mọi chuyển trạng thái do runtime thực hiện bằng revision/CAS.
- Không triển khai lineage R3, ngân sách R4, TaskBudget R5, reconciliation R6–R7 hoặc FORK R8+.

## Trình tự

1. Đối chiếu contract roadmap với schema, state machine, runtime, coordinator, persistence và CL parser.
2. Viết tài liệu contract freeze R0.
3. Thêm enum/reason/invariant và lớp tương thích R1; migrate dữ liệu cũ.
4. Thêm revision, CAS repository/store, create/load guard và terminal update R2.
5. Loại split identity giữa coordinator và `AgentRuntime` trên đường chạy production.
6. Thêm test contract, duplicate/stale/concurrent resume và regression test.
7. Xuất tài liệu hoàn thành riêng cho R0, R1 và R2.

## Rủi ro và kiểm soát

| Rủi ro | Kiểm soát |
|---|---|
| CL cũ chỉ hiểu `WAITING_FOR_CONNECTION` | SE tiếp tục phát wire form cũ trong giai đoạn chuyển tiếp; CL mới normalize cả hai dạng. |
| Hai worker resume cùng execution | `UPDATE ... WHERE revision = expected_revision`; chỉ một CAS thành công. |
| Coordinator và runtime tạo hai execution ID | Coordinator cấp ID và truyền chính ID đó vào runtime; runtime là nơi tạo durable record. |
| Terminal execution bị chạy lại | State machine cấm terminal → `RUNNING`; startup guard từ chối record không phải `WAITING`. |
| Migration làm mất ý nghĩa trạng thái cũ | Migration ánh xạ trạng thái cũ sang `WAITING` và ghi `wait_reason`. |

## Exit gate

- R0: tất cả quyết định bắt buộc có giá trị duy nhất và được ghi thành ma trận.
- R1: runtime nội bộ chỉ dùng `WAITING`; `WAITING` luôn có reason; CL đọc legacy/canonical.
- R2: durable record tồn tại trước iteration 1; duplicate/stale CAS bị từ chối; hai resume chỉ một thắng; terminal update nguyên tử.

