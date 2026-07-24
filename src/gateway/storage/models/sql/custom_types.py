from sqlalchemy.types import TypeDecorator, String
import uuid
class CUID(TypeDecorator):
    """
    Lưu trữ các giá trị ID dưới dạng String trong database.
    Đây là một lớp trừu tượng để có thể dễ dàng thay đổi kiểu dữ liệu ID trong tương lai.
    """
    impl = String

    def __init__(self, length=255, *args, **kwargs):
        super().__init__(length=length, *args, **kwargs)

def default_uuid_str():
    return str(uuid.uuid4())