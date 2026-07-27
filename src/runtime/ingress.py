import hmac
import hashlib
from typing import Any, Dict, Optional
from ..domain.schemas.runtime.runtime import RuntimeCommand

class IngressRuntime:
    def __init__(self, idempotency_store: Dict[str, Any]):
        # Trong thực tế, idempotency_store sẽ là một driver kết nối tới Redis
        self.idempotency_store = idempotency_store

    async def process_raw_request(
        self, 
        idempotency_key: Optional[str],
        user_id: str,
        session_id: str,
        request_type: str,
        payload: Dict[str, Any]
    ) -> RuntimeCommand:
        """
        Validate, kiểm tra trùng lặp và chuyển đổi Request thành Command.
        """
        # 1. Kiểm tra Idempotency nếu có Key (Chống trùng lặp từ Client retry)
        if idempotency_key:
            cached_result = self.idempotency_store.get(f"idemp:{user_id}:{idempotency_key}")
            if cached_result:
                return cached_result  # Trả về kết quả đã xử lý trước đó

        # 2. Xây dựng Command đối tượng
        command = RuntimeCommand(
            command_type=f"Execute{request_type.capitalize()}",
            session_id=session_id,
            user_id=user_id,
            payload=payload
        )
        
        return command

    def verify_webhook_signature(self, raw_body: bytes, signature: str, secret: str) -> bool:
        """Đảm bảo an toàn bảo mật cho các webhook tầng biên."""
        computed_sig = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(computed_sig, signature)