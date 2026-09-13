from passlib.context import CryptContext

# Sử dụng Argon2, một thuật toán hashing hiện đại và an toàn
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Xác minh mật khẩu."""
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    """Tạo hash cho mật khẩu."""
    return pwd_context.hash(password)