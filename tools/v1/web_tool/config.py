from __future__ import annotations

from tools.v1._shared.contracts import tool_result_schema

WEB_TOOL_NAME = "web_tool"
WEB_TOOL_VERSION = "2.0.0"

MAX_URL_CHARS = 8192
MAX_HOST_CHARS = 253
MAX_DNS_ADDRESSES = 16
DNS_TIMEOUT_SECONDS = 2.0
MAX_REDIRECTS = 5

MAX_QUERY_CHARS = 2048
SEARCH_RESULTS_DEFAULT = 5
SEARCH_RESULTS_MIN = 1
SEARCH_RESULTS_MAX = 25

READ_TIMEOUT_DEFAULT = 10.0
READ_TIMEOUT_MIN = 1.0
READ_TIMEOUT_MAX = 60.0

MAX_STATIC_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_SEARCH_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_RENDERED_HTML_CHARS = 5_000_000

MAX_CONTENT_CHARS_DEFAULT = 100_000
MAX_CONTENT_CHARS_MIN = 1
MAX_CONTENT_CHARS_MAX = 500_000

MAX_BATCH_URLS = 20
WEB_CONCURRENCY_DEFAULT = 5
WEB_CONCURRENCY_MIN = 1
WEB_CONCURRENCY_MAX = 8

MAX_WAIT_SELECTOR_CHARS = 1024
MAX_TITLE_CHARS = 4096
MAX_SEARCH_TITLE_CHARS = 1024
MAX_SEARCH_SNIPPET_CHARS = 4096

MAX_RETRIES = 3
MAX_RETRY_AFTER_SECONDS = 5.0

MAX_PROXY_COUNT = 32
MAX_PROXY_URL_CHARS = 2048
MAX_PROXY_TEST_CONCURRENCY = 8

MAX_TABLES = 20
MAX_TABLE_ROWS = 200
MAX_TABLE_COLUMNS = 50
MAX_TABLE_CELLS_TOTAL = 10_000
MAX_TABLE_CELL_CHARS = 2_000

MAX_CHARTS = 20
MAX_CHART_DATASETS = 20
MAX_CHART_POINTS_TOTAL = 5_000
MAX_CHART_LABEL_CHARS = 512

DEFAULT_IMPERSONATE_PROFILES = [
    "chrome120",
    "chrome119",
    "edge120",
    "edge101",
    "safari17_0",
]

_SEARCH_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_QUERY_CHARS,
        },
        "max_results": {
            "type": "integer",
            "minimum": SEARCH_RESULTS_MIN,
            "maximum": SEARCH_RESULTS_MAX,
        },
        "timeout": {
            "type": "number",
            "minimum": READ_TIMEOUT_MIN,
            "maximum": READ_TIMEOUT_MAX,
        },
    },
    "required": ["query"],
}

_READ_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "url": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_URL_CHARS,
        },
        "force_js": {"type": "boolean"},
        "wait_selector": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_WAIT_SELECTOR_CHARS,
        },
        "timeout": {
            "type": "number",
            "minimum": READ_TIMEOUT_MIN,
            "maximum": READ_TIMEOUT_MAX,
        },
        "max_chars": {
            "type": "integer",
            "minimum": MAX_CONTENT_CHARS_MIN,
            "maximum": MAX_CONTENT_CHARS_MAX,
        },
        "clean_noise": {"type": "boolean"},
        "deduplicate": {"type": "boolean"},
    },
    "required": ["url"],
}

_READ_MANY_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "urls": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_BATCH_URLS,
            "items": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_URL_CHARS,
            },
        },
        "force_js": {"type": "boolean"},
        "wait_selector": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_WAIT_SELECTOR_CHARS,
        },
        "timeout": {
            "type": "number",
            "minimum": READ_TIMEOUT_MIN,
            "maximum": READ_TIMEOUT_MAX,
        },
        "max_chars": {
            "type": "integer",
            "minimum": MAX_CONTENT_CHARS_MIN,
            "maximum": MAX_CONTENT_CHARS_MAX,
        },
        "clean_noise": {"type": "boolean"},
        "deduplicate": {"type": "boolean"},
    },
    "required": ["urls"],
}

_PHYSICAL_PARAMETERS = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["search", "scrape", "scrape_many"],
        },
        "query": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_QUERY_CHARS,
        },
        "url": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_URL_CHARS,
        },
        "urls": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_BATCH_URLS,
            "items": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_URL_CHARS,
            },
        },
        "force_js": {"type": "boolean"},
        "wait_selector": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_WAIT_SELECTOR_CHARS,
        },
        "max_results": {
            "type": "integer",
            "minimum": SEARCH_RESULTS_MIN,
            "maximum": SEARCH_RESULTS_MAX,
        },
        "max_chars": {
            "type": "integer",
            "minimum": MAX_CONTENT_CHARS_MIN,
            "maximum": MAX_CONTENT_CHARS_MAX,
        },
        "timeout": {
            "type": "number",
            "minimum": READ_TIMEOUT_MIN,
            "maximum": READ_TIMEOUT_MAX,
        },
        "clean_noise": {"type": "boolean"},
        "deduplicate": {"type": "boolean"},
        "output_format": {
            "type": "string",
            "enum": ["markdown"],
            "description": "Compatibility-only. T6 returns structured ToolResult.",
        },
    },
    "required": ["action"],
}

TOOL_METADATA = {
    "manifest_version": "2.0",
    "name": WEB_TOOL_NAME,
    "version": WEB_TOOL_VERSION,
    "description": (
        "Tìm kiếm và đọc nội dung Web với URL/DNS/redirect safety, bounded "
        "network/browser resources và kết quả ToolResult có cấu trúc."
    ),
    "expose_root": False,
    "exports": [
        {
            "id": "web.search",
            "version": "1.0",
            "name": "web.search",
            "description": (
                "Tìm kiếm Web theo từ khóa và trả về danh sách kết quả có cấu trúc."
            ),
            "bind": {"action": "search"},
            "input_schema": _SEARCH_INPUT_SCHEMA,
            "output_schema": tool_result_schema({}),
            "kind": "TOOL",
            "execution_mode": "ONE_SHOT",
            "idempotency": "UNKNOWN",
            "effects": ["READ", "EXTERNAL_SIDE_EFFECT"],
            "base_risk": "MEDIUM",
            "required_scopes": [],
            "required_permissions": [],
            "danger_patterns": [],
        },
        {
            "id": "web.read",
            "version": "1.0",
            "name": "web.read",
            "description": (
                "Đọc một URL Web bằng pipeline T6 an toàn và trả về nội dung có cấu trúc."
            ),
            "bind": {"action": "scrape"},
            "input_schema": _READ_INPUT_SCHEMA,
            "output_schema": tool_result_schema({}),
            "kind": "TOOL",
            "execution_mode": "ONE_SHOT",
            "idempotency": "UNKNOWN",
            "effects": ["READ", "EXTERNAL_SIDE_EFFECT"],
            "base_risk": "MEDIUM",
            "required_scopes": [],
            "required_permissions": [],
            "danger_patterns": [],
        },
        {
            "id": "web.read_many",
            "version": "1.0",
            "name": "web.read_many",
            "description": (
                "Đọc một batch URL Web có giới hạn bằng cùng pipeline T6 an toàn."
            ),
            "bind": {"action": "scrape_many"},
            "input_schema": _READ_MANY_INPUT_SCHEMA,
            "output_schema": tool_result_schema({}),
            "kind": "TOOL",
            "execution_mode": "ONE_SHOT",
            "idempotency": "UNKNOWN",
            "effects": ["READ", "EXTERNAL_SIDE_EFFECT"],
            "base_risk": "MEDIUM",
            "required_scopes": [],
            "required_permissions": [],
            "danger_patterns": [],
        },
    ],
    # Legacy root compatibility fields remain for direct physical consumers.
    # They are not a second Metadata V2 authority.
    "base_risk": "MEDIUM",
    "effects": ["READ", "EXTERNAL_SIDE_EFFECT"],
    "danger_patterns": [],
    "parameters": _PHYSICAL_PARAMETERS,
}
