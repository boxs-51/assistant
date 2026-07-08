from typing import Dict, Any, Optional
from ....schemas import ModelInfo, ContextLimits, PricingInfo
import datetime

def _parse_iso_to_timestamp(iso_str: Optional[str]) -> Optional[int]:
    """Hàm trợ giúp convert ISO datetime string từ Google API sang Unix timestamp."""
    if not iso_str:
        return None
    try:
        # Xử lý ký tự 'Z' của UTC để tương thích với các phiên bản Python cũ/mới
        clean_str = iso_str.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(clean_str)
        return int(dt.timestamp())
    except Exception:
        return None