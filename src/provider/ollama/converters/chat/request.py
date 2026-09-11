from typing import Dict, Any, List, Optional
from pathlib import Path
import base64
import structlog

logger = structlog.get_logger(__name__)

MAX_TEXT_LENGTH = 100_000


class RequestChats:
    def _extract_base64_image(self, part: Dict[str, Any]) -> Optional[str]:
        """Tách chuỗi Base64 từ các định dạng image khác nhau (OpenAI URL, Gateway Attachment)."""
        # Form 1: OpenAI Image URL format (data:image/...;base64,...)
        if part.get("type") == "image_url":
            url = part.get("image_url", {}).get("url", "")
            if ";base64," in url:
                return url.split(";base64,")[1]

        # Form 2: Gateway Media Content format
        if part.get("type") == "image":
            img_obj = part.get("image", {})
            attachment = img_obj.get("attachment", {})
            if attachment.get("base64_data"):
                return attachment.get("base64_data")
            
            # File local
            path_str = attachment.get("path") or attachment.get("uri") or ""
            if path_str and Path(path_str).is_file():
                try:
                    with open(path_str, "rb") as f:
                        return base64.b64encode(f.read()).decode("utf-8")
                except Exception as e:
                    logger.warning("Không thể đọc file ảnh local", path=path_str, error=str(e))

        return None

    def adapt_chat_request(self, request: Dict[str, Any], stream: bool = False) -> Dict[str, Any]:
        """
        Chuyển đổi request body từ chuẩn Gateway/OpenAI sang REST Payload chuẩn Ollama API (/api/chat).
        """
        ollama_messages = []

        for msg in request.get("messages", []):
            role = msg.get("role")
            content = msg.get("content", "")
            images: List[str] = []
            text_parts: List[str] = []

            # Process Content (String or List Parts)
            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, str):
                        text_parts.append(part)
                    elif isinstance(part, dict):
                        part_type = part.get("type", "")
                        if part_type == "text":
                            text_parts.append(part.get("text", ""))
                        else:
                            b64_img = self._extract_base64_image(part)
                            if b64_img:
                                images.append(b64_img)

            full_text = "\n".join(text_parts)
            if len(full_text) > MAX_TEXT_LENGTH:
                full_text = full_text[:MAX_TEXT_LENGTH] + "\n...[Nội dung bị cắt bớt]..."

            msg_obj: Dict[str, Any] = {
                "role": role,
                "content": full_text
            }

            if images:
                msg_obj["images"] = images

            if role == "assistant" and "tool_calls" in msg:
                msg_obj["tool_calls"] = msg["tool_calls"]

            ollama_messages.append(msg_obj)

        adapted_request: Dict[str, Any] = {
            "model": request.get("model"),
            "messages": ollama_messages,
            "stream": stream
        }

        # Map Config -> Ollama Options
        config = request.get("config", {})
        options: Dict[str, Any] = {}

        if "temperature" in config and config["temperature"] is not None:
            options["temperature"] = config["temperature"]
        if "top_p" in config and config["top_p"] is not None:
            options["top_p"] = config["top_p"]
        if "max_tokens" in config and config["max_tokens"] is not None:
            options["num_predict"] = config["max_tokens"]  # Ollama đổi max_tokens thành num_predict
        if "presence_penalty" in config and config["presence_penalty"] is not None:
            options["presence_penalty"] = config["presence_penalty"]
        if "frequency_penalty" in config and config["frequency_penalty"] is not None:
            options["frequency_penalty"] = config["frequency_penalty"]
        if "stop" in config and config["stop"] is not None:
            options["stop"] = config["stop"]

        if options:
            adapted_request["options"] = options

        # Map Structured Output Format
        if config.get("response_format") in ["json", "json_object"]:
            adapted_request["format"] = "json"

        # Map Tools / Function Calling
        tools = request.get("tools")
        if tools and isinstance(tools, list):
            formatted_tools = []
            for t in tools:
                if hasattr(t, "model_dump"):
                    formatted_tools.append(t.model_dump())
                elif isinstance(t, dict):
                    formatted_tools.append(t)
            adapted_request["tools"] = formatted_tools

        return adapted_request