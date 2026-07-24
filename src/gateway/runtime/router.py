import hashlib
from typing import List, Optional

class SessionRouter:
    """
    Điều phối phân bổ Session về đúng Gateway Instance (Consistent Hashing).
    Giúp duy trì Session Affinity cho các kết nối WebSocket/Voice dài hạn.
    """
    def __init__(self, replicas: int = 3):
        self.replicas = replicas  # Số lượng node ảo (virtual nodes) để phân phối đều
        self.ring: dict[int, str] = {}
        self._sorted_keys: List[int] = []

    def _hash(self, key: str) -> int:
        """Băm chuỗi key thành một số nguyên 32-bit."""
        return int(hashlib.md5(key.encode('utf-8')).hexdigest(), 16) & 0xFFFFFFFF

    def add_node(self, node: str):
        """Thêm một Gateway Instance vào vòng băm."""
        for i in range(self.replicas):
            virtual_key = self._hash(f"{node}-virtual-{i}")
            self.ring[virtual_key] = node
        self._sorted_keys = sorted(self.ring.keys())

    def remove_node(self, node: str):
        """Xóa một Gateway Instance khi node đó bị sập hoặc thu hẹp scale."""
        for i in range(self.replicas):
            virtual_key = self._hash(f"{node}-virtual-{i}")
            if virtual_key in self.ring:
                del self.ring[virtual_key]
        self._sorted_keys = sorted(self.ring.keys())

    def get_node(self, session_id: str) -> Optional[str]:
        """Tìm Node chịu trách nhiệm xử lý cho Session tương ứng."""
        if not self.ring:
            return None
            
        key = self._hash(session_id)
        # Sử dụng thuật toán tìm kiếm nhị phân để tìm node gần nhất trên vòng băm
        low, high = 0, len(self._sorted_keys) - 1
        
        while low <= high:
            mid = (low + high) // 2
            if self._sorted_keys[mid] >= key:
                high = mid - 1
            else:
                low = mid + 1
                
        # Nếu vượt quá node cuối cùng, quay lại node đầu tiên của vòng tròn
        idx = low if low < len(self._sorted_keys) else 0
        return self.ring[self._sorted_keys[idx]]