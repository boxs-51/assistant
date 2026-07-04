from urllib.parse import urljoin

from .api import ApiType
class EndpointBuilder:
    """
    Xây dựng URL một cách an toàn từ base_url và các template.
    Tránh các lỗi nối chuỗi thủ công.
    """
    def __init__(self, base_url: str):
        # Đảm bảo base_url luôn kết thúc bằng một dấu /
        self.base_url = base_url.rstrip('/') + '/'

    def build(self, api_type: ApiType | str, **kwargs) -> str:
        """
        Tạo URL đầy đủ từ một template và các tham số.
        Ví dụ: template = "v1beta/models/{model}:generateContent"
        Ví dụ: api_type = ApiType.CHAT_COMPLETIONS hoặc "v1beta/models/{model}:generateContent"
        """
        template = api_type.value if isinstance(api_type, ApiType) else api_type
        path = template.format(**kwargs).lstrip('/')
        return urljoin(self.base_url, path)