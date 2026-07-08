from typing import Dict, Any

class RequestChats():

    def adapt_chat_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Chuyển đổi request body từ chuẩn Gateway sang chuẩn Ollama.
        Trong trường hợp này, API /api/chat của Ollama khá tương thích.
        """
        # Chúng ta chỉ cần đảm bảo model được truyền đúng cách
        adapted_request = request.copy()
        # Không cần thay đổi nhiều, nhưng có thể thêm logic để loại bỏ các trường không được hỗ trợ
        return adapted_request