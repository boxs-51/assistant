"""Pure standalone contracts for Tools V1.

This package intentionally performs no discovery, execution, I/O, browser,
subprocess, GUI, SE or CL initialization at import time.
"""

from .contracts import (
    JSONValue,
    TOOL_RESULT_REQUIRED_KEYS,
    ToolErrorPayload,
    ToolResult,
    ToolResultMeta,
    failure_result,
    success_result,
    tool_result_schema,
)
from .errors import (
    GENERIC_ERROR_CODES,
    ToolContractError,
    ToolFoundationError,
    ToolJsonSafetyError,
    ToolLimitConfigError,
    ToolMetadataError,
    validate_error_code,
)
from .limits import FloatLimitSpec, IntLimitSpec, resolve_float_limit, resolve_int_limit
from .metadata import ToolCapabilityExport, ToolManifestV2, validate_tool_manifest_v2
from .validation import REDACTED, assert_json_safe, redact_sensitive, redact_url_credentials

__all__ = [
    "FloatLimitSpec",
    "GENERIC_ERROR_CODES",
    "IntLimitSpec",
    "JSONValue",
    "REDACTED",
    "TOOL_RESULT_REQUIRED_KEYS",
    "ToolCapabilityExport",
    "ToolContractError",
    "ToolErrorPayload",
    "ToolFoundationError",
    "ToolJsonSafetyError",
    "ToolLimitConfigError",
    "ToolManifestV2",
    "ToolMetadataError",
    "ToolResult",
    "ToolResultMeta",
    "assert_json_safe",
    "failure_result",
    "redact_sensitive",
    "redact_url_credentials",
    "resolve_float_limit",
    "resolve_int_limit",
    "success_result",
    "tool_result_schema",
    "validate_error_code",
    "validate_tool_manifest_v2",
]
