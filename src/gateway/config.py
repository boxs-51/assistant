from .config.core import ConfigurationRegistry
from .config.schemas import ConfigSchema

"""
Đây là điểm truy cập trung tâm cho cấu hình trong toàn bộ ứng dụng.

Nó hoạt động như một proxy trỏ đến đối tượng cấu hình thực tế được lưu trữ
trong ConfigurationRegistry. Điều này cho phép chúng ta thay đổi cách tải cấu hình
mà không cần thay đổi cách các module khác truy cập nó.

Cách sử dụng:
from ..config import settings
print(settings.gateway.port)
"""

# Tạo một proxy object để các module khác có thể import `settings`
# mà không gây ra lỗi circular import.
settings: ConfigSchema = ConfigurationRegistry.get_config()