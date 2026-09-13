from typing import Any, Dict, List

from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for
from pydantic import BaseModel, ConfigDict, Field

class InvalidCapabilitySchemaError(ValueError):
    """Ngoại lệ riêng cho JSON Schema không hợp lệ."""
    pass


def validate_input_schema(schema: Dict[str, Any]) -> None:
    """Fail closed for invalid capability input JSON Schemas."""
    schema_dict = dict(schema)
    
    try:
        validator_cls = validator_for(schema_dict)
        validator_cls.check_schema(schema_dict)
    except SchemaError as e:
        # Lấy vị trí thuộc tính bị lỗi trong schema (nếu có)
        path = " -> ".join(str(p) for p in e.path) if e.path else "root"
        
        # Tạo error message tùy chỉnh
        custom_message = (
            f"[Capability Error] JSON Schema không hợp lệ tại '{path}': {e.message}"
        )
        raise InvalidCapabilitySchemaError(custom_message) from e


class CapabilityDefinition(BaseModel):
    """Provider-neutral, declarative description of an executable capability.

    ``parameters`` remains accepted as a backwards-compatible alias for
    ``input_schema`` because the current repository still contains legacy
    ToolRegistry/provider integrations.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        extra="forbid",
    )

    id: str | None = None
    version: str = "1.0"
    name: str
    description: str

    input_schema: Dict[str, Any] = Field(
        default_factory=dict,
        alias="parameters",
    )
    output_schema: Dict[str, Any] = Field(default_factory=dict)

    source: str = "BUILTIN"
    execution_kind: str = "PYTHON"

    require_auth: bool = False
    required_scopes: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @property
    def capability_id(self) -> str:
        return self.id or self.name

    @property
    def parameters(self) -> Dict[str, Any]:
        """Legacy read-only view used by existing provider adapters."""
        return self.input_schema