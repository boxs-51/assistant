from typing import Dict, Any

class RequestChats():

    def adapt_chat_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Chuyển đổi request body từ chuẩn Gateway sang chuẩn OpenAI.
        """
        adapted_request = request.copy()

        # OpenAI sử dụng 'model' trực tiếp, không cần dịch tên model ở đây
        # Logic dịch tên model đã được xử lý ở BaseProvider.prepare_request

        # Xử lý trường 'stream' nếu có
        if "stream" in adapted_request and adapted_request["stream"] is False:
            # OpenAI mặc định stream là false nếu không có, nhưng nếu người dùng gửi explicit false
            # thì không cần thay đổi gì. Nếu gửi true thì cũng không cần thay đổi.
            pass
        
        # Xử lý các trường khác nếu cần thiết để tương thích hoàn toàn với OpenAI API
        # Ví dụ:
        # - 'max_tokens' -> 'max_tokens' (tương thích)
        # - 'temperature' -> 'temperature' (tương thích)
        # - 'top_p' -> 'top_p' (tương thích)
        # - 'stop' -> 'stop' (tương thích)
        # - 'presence_penalty' -> 'presence_penalty' (tương thích)
        # - 'frequency_penalty' -> 'frequency_penalty' (tương thích)
        # - 'seed' -> 'seed' (tương thích)

        return adapted_request
