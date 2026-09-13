import secrets
import hashlib
from typing import Tuple

API_KEY_PREFIX = "sk"
API_KEY_PREFIX_ADMIN = "ak"
API_KEY_NUM_BYTES = 24 # 24 bytes -> 32-char base64 string

def generate_api_key(is_admin: bool = False) -> Tuple[str, str, str]:
    """
    Tạo một API key mới (Hỗ trợ cả User Key và Admin Key).
    
    Returns:
        Tuple[str, str, str]: (full_key, prefix, hashed_key_body)
    """
    # Xác định tiền tố dựa trên vai trò
    key_prefix = API_KEY_PREFIX_ADMIN if is_admin else API_KEY_PREFIX
    
    key_body = secrets.token_urlsafe(API_KEY_NUM_BYTES)
    prefix = f"{key_prefix}_{secrets.token_hex(4)}"
    full_key = f"{prefix}_{key_body}"
    hashed_key_body = hashlib.sha256(key_body.encode()).hexdigest()
    
    return full_key, prefix, hashed_key_body