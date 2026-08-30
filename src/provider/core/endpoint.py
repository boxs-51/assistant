from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode, quote
from typing import Any, Dict, Optional, Union
from pydantic import AnyHttpUrl
from .api import ApiType


class EndpointBuilder:
    def __init__(self, base_url: Union[str, AnyHttpUrl], default_scheme: str = "https"):
        # Chuyển AnyHttpUrl (hoặc str) thành chuỗi str thuần túy
        base_str = str(base_url)

        # Thêm scheme nếu thiếu (áp dụng cho trường hợp truyền string không có http/https)
        if not urlparse(base_str).scheme:
            base_str = f"{default_scheme}://{base_str}"

        # Đảm bảo base_url luôn kết thúc bằng '/'
        self.base_url = base_str.rstrip('/') + '/'

    def build(
        self, 
        api_type: Union[ApiType, str], 
        params: Optional[Dict[str, Any]] = None,
        quote_path_vars: bool = False,
        **kwargs
    ) -> str:
        template = api_type.value if isinstance(api_type, ApiType) else api_type

        # Chuyển đổi các biến kwargs nếu có AnyHttpUrl truyền vào path
        formatted_kwargs = {}
        for k, v in kwargs.items():
            val_str = str(v)
            if quote_path_vars and isinstance(v, str):
                formatted_kwargs[k] = quote(val_str, safe='')
            else:
                formatted_kwargs[k] = val_str

        path_or_url = template.format(**formatted_kwargs)

        parsed_target = urlparse(path_or_url)
        if parsed_target.scheme and parsed_target.netloc:
            full_url = path_or_url
        else:
            clean_path = path_or_url.lstrip('/')
            full_url = urljoin(self.base_url, clean_path)

        if params:
            parsed_url = urlparse(full_url)
            existing_query = dict(parse_qsl(parsed_url.query))
            new_query = {k: str(v) for k, v in params.items() if v is not None}
            combined_query = {**existing_query, **new_query}

            full_url = urlunparse((
                parsed_url.scheme,
                parsed_url.netloc,
                parsed_url.path,
                parsed_url.params,
                urlencode(combined_query),
                parsed_url.fragment
            ))

        return full_url