from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware
from fastapi.middleware.cors import CORSMiddleware

from .observability import observability_middleware
from ..authentication.middleware import AuthenticationMiddleware
from ....infrastructure.config import settings


def create_middleware_stack(app: FastAPI):
    """
    Hàm tập trung để khởi tạo và đăng ký tất cả các middleware cho ứng dụng.
    Thứ tự đăng ký middleware là rất quan trọng.
    """
    # 1. Middleware giám sát và thu thập metrics (chạy đầu tiên để bao bọc tất cả)
    app.middleware("http")(observability_middleware)

    # 2. Middleware xác thực (chạy trước CORS để không block các request OPTIONS)
    # Nó sẽ bỏ qua các public paths được định nghĩa.
    PUBLIC_PATHS = ["/docs", "/openapi.json", "/health*", "/ready", "/metrics", "/stats", "/auth/*"]
    app.add_middleware(
        AuthenticationMiddleware,
        public_paths=PUBLIC_PATHS
    )

    # 3. Middleware quản lý session cho luồng OAuth
    SESSION_SECRET_KEY = "change-this-in-production"
    app.add_middleware(
        SessionMiddleware,
        secret_key=SESSION_SECRET_KEY,
        session_cookie="oauth_session",
        max_age=600  # 10 phút
    )

    # 4. Middleware xử lý Cross-Origin Resource Sharing (CORS)
    ALLOWED_ORIGINS = ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )