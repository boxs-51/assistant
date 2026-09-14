import base64
import json
import mimetypes
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List

import structlog

from ..schemas.enums import ToolType
from ..schemas.message import MessageContentPart
from ..schemas.tool import FunctionCall, GatewayToolCall, GatewayToolDefinition

logger = structlog.get_logger(__name__)

def build_tools_schema(registry) -> List[GatewayToolDefinition]:
    """Tạo danh sách GatewayToolDefinition từ registry."""
    gateway_tools = []
    for name, tool_data in registry.tools.items():
        meta = tool_data.get("metadata", {})
        is_mcp = tool_data.get("is_mcp", False)
        tool_type = ToolType.MCP if is_mcp else ToolType.WORKFLOW

        tool_def = GatewayToolDefinition(
            name=name,
            description=meta.get("description", f"Tool {name}"),
            parameters=meta.get("parameters", {}),
            tool_type=tool_type,
            source_server=meta.get("source_server", None),
        )
        gateway_tools.append(tool_def)

    return gateway_tools


def merge_tool_call_delta(acc_tool_calls: list, deltas: list):
    """Gộp các delta chunks của tool call trong quá trình streaming."""
    for delta in deltas:
        index = getattr(delta, "index", 0) or 0
        while len(acc_tool_calls) <= index:
            acc_tool_calls.append(
                {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                }
            )

        target = acc_tool_calls[index]

        delta_id = getattr(delta, "id", None)
        if delta_id and not target["id"]:
            target["id"] = delta_id

        function_delta = getattr(delta, "function", None)
        if function_delta:
            delta_name = getattr(function_delta, "name", None)
            delta_arguments = getattr(function_delta, "arguments", None)

            if delta_name:
                target["function"]["name"] += delta_name
            if delta_arguments:
                target["function"]["arguments"] += delta_arguments


def parse_text_action_fallback(text_content: str) -> List[GatewayToolCall]:
    """Trích xuất tool call từ văn bản dạng Action: tool_name(arg=val) khi LLM không dùng function call chuẩn."""
    if not text_content:
        return []

    if isinstance(text_content, list):
        extracted_texts = []
        for item in text_content:
            if isinstance(item, str):
                extracted_texts.append(item)
            elif hasattr(item, 'text'):
                extracted_texts.append(item.text)
            elif isinstance(item, dict) and 'text' in item:
                extracted_texts.append(item['text'])
            else:
                extracted_texts.append(str(item))
        text_content = "\n".join([t for t in extracted_texts if t is not None])
        
    tool_calls = []
    pattern = r"Action:\s*([a-zA-Z0-9_]+)\((.*?)\)"
    matches = re.findall(pattern, text_content, re.DOTALL)

    for name, raw_args in matches:
        args_dict = {}
        arg_matches = re.findall(
            r"([a-zA-Z0-9_]+)\s*=\s*['\"]([^'\"]*)['\"]",
            raw_args,
        )
        for k, v in arg_matches:
            args_dict[k] = v

        tool_calls.append(
            GatewayToolCall(
                id=f"fallback_{name}_{uuid.uuid4().hex[:12]}",
                type="function",
                function=FunctionCall(
                    name=name,
                    arguments=json.dumps(args_dict),
                ),
            )
        )

    return tool_calls


def process_attached_files(attached_files: List[Any]) -> List[MessageContentPart]:
    """Chuyển đổi các file đính kèm thành MessageContentPart (Data URI / Base64)."""
    parts = []
    for item in attached_files:
        if not item:
            continue

        if isinstance(item, dict):
            mime_type = item.get("mime_type", "application/octet-stream")
            b64_data = item.get("b64_data", "")
            data_uri = item.get("data_uri") or f"data:{mime_type};base64,{b64_data}"
            filename = item.get("filename", "attached_file")

            if mime_type.startswith("image/"):
                parts.append(
                    MessageContentPart(
                        type="image_url",
                        image_url={"url": data_uri},
                    )
                )
            else:
                parts.append(
                    MessageContentPart(
                        type="file",
                        file_data={
                            "filename": filename,
                            "mime_type": mime_type,
                            "data": b64_data,
                        },
                    )
                )
            continue

        if not isinstance(item, str):
            continue

        item_str = item.strip()

        if item_str.startswith(("data:", "http://", "https://")):
            parts.append(
                MessageContentPart(
                    type="image_url",
                    image_url={"url": item_str},
                )
            )

        elif os.path.exists(item_str) and os.path.isfile(item_str):
            try:
                mime_type, _ = mimetypes.guess_type(item_str)
                if not mime_type:
                    mime_type = "application/octet-stream"

                with open(item_str, "rb") as f:
                    file_bytes = f.read()
                    b64_data = base64.b64encode(file_bytes).decode("utf-8")

                data_uri = f"data:{mime_type};base64,{b64_data}"

                if mime_type.startswith("image/"):
                    parts.append(
                        MessageContentPart(
                            type="image_url",
                            image_url={"url": data_uri},
                        )
                    )
                else:
                    parts.append(
                        MessageContentPart(
                            type="file",
                            file_data={
                                "filename": Path(item_str).name,
                                "mime_type": mime_type,
                                "data": b64_data,
                            },
                        )
                    )
            except Exception as e:
                logger.error(
                    "Failed to read local file",
                    file_path=item_str,
                    error=str(e),
                )

        else:
            data_uri = f"data:image/jpeg;base64,{item_str}"
            parts.append(
                MessageContentPart(
                    type="image_url",
                    image_url={"url": data_uri},
                )
            )

    return parts