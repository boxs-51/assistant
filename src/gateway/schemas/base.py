from pydantic import BaseModel, ConfigDict 

# =================================================================
# 1. CHUẨN HÓA GATEWAY BASE MODEL (Cấu hình dùng chung)
# =================================================================

class GatewayBaseModel(BaseModel):
    """Base model dùng chung cho toàn bộ hệ thống gateway.
    Cung cấp các cấu hình chuẩn hóa về serialize, bảo mật và hiệu năng.
    """
    model_config = ConfigDict(
        populate_by_name=True,       # Cho phép map cả alias lẫn name gốc
        arbitrary_types_allowed=True,# Hỗ trợ các kiểu dữ liệu phức tạp khác
        str_strip_whitespace=True,   # Tự động strip khoảng trắng của string
        validate_assignment=True,    # Re-validate khi gán lại giá trị trường
    )