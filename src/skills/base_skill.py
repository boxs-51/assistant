from abc import ABC, abstractmethod

class BaseSkill(ABC):
    name: str
    description: str
    
    # Bạn có thể định nghĩa thêm các phương thức trừu tượng khác ở đây nếu cần