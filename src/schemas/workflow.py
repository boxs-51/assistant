from pydantic import Field
from typing import List, Dict, Any

from .base import GatewayBaseModel


class WorkflowStep(GatewayBaseModel):
    """Định nghĩa một bước trong một workflow."""
    step_id: str = Field(..., description="ID định danh duy nhất cho bước này trong workflow, ví dụ: 'step_1_get_user_email'.")
    tool_name: str = Field(..., description="Tên của tool đã đăng ký để thực thi trong bước này.")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Các tham số cho tool. Có thể chứa các placeholder như '{{initial_input.user_id}}' hoặc '{{steps.step_1.output}}'.")


class WorkflowDefinition(GatewayBaseModel):
    """
    Định nghĩa một chuỗi các bước thực thi tool.
    Cấu trúc này sẽ được nhúng vào trong `parameters` của một tool có loại là WORKFLOW.
    """
    steps: List[WorkflowStep] = Field(..., description="Danh sách các bước thực thi tuần tự.")
    output_template: str = Field("{{steps.last.output}}", description="Template để định dạng kết quả cuối cùng của workflow.")