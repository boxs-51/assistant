from typing import Any
from .config_loader.core import ConfigurationRegistry
from .config_loader.schemas import ConfigSchema

"""
Đây là điểm truy cập trung tâm cho cấu hình trong toàn bộ ứng dụng.

Nó hoạt động như một proxy trỏ đến đối tượng cấu hình thực tế được lưu trữ
trong ConfigurationRegistry. Điều này cho phép chúng ta thay đổi cách tải cấu hình
mà không cần thay đổi cách các module khác truy cập nó.

Cách sử dụng:
from ..config import settings
print(settings.gateway.port)
"""

class _SettingsProxy:
    """
    Một proxy lười biếng cho đối tượng cấu hình.
    Nó trì hoãn việc gọi `ConfigurationRegistry.get_config()` cho đến khi
    một thuộc tính của cấu hình được truy cập lần đầu tiên.
    Điều này ngăn ngừa lỗi khởi tạo tại thời điểm import.
    """
    def __getattr__(self, name: str) -> Any:
        # Lấy cấu hình thực tế khi một thuộc tính được truy cập lần đầu.
        # Sau đó, thay thế proxy bằng đối tượng cấu hình thực tế
        # cho các truy cập trong tương lai để có hiệu suất tốt hơn.
        config = ConfigurationRegistry.get_config()
        globals()['settings'] = config
        return getattr(config, name)

# Khởi tạo proxy. Các module khác sẽ import đối tượng này.
settings: ConfigSchema = _SettingsProxy() # type: ignore