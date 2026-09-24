from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware
from fastapi.middleware.cors import CORSMiddleware

from .observability import observability_middleware
from .asset_upload_limit import AssetUploadBodyLimitMiddleware
from ..authentication.middleware import AuthenticationMiddleware
from ....infrastructure.config.schemas import (
    AssetStorageSettings,
    AuthenticationSettings,
)


def create_middleware_stack(
    app: FastAPI,
    auth_config: AuthenticationSettings,
    asset_config: AssetStorageSettings,
):
    """
    Hàm tập trung để khởi tạo và đăng ký tất cả các middleware cho ứng dụng.
    Thứ tự đăng ký middleware là rất quan trọng.
    """
    # 1. Middleware giám sát và thu thập metrics (chạy đầu tiên để bao bọc tất cả)
    app.middleware("http")(observability_middleware)

    # 2. Middleware xác thực (chạy trước CORS để không block các request OPTIONS)
    # Nó sẽ bỏ qua các public paths được định nghĩa.
    app.add_middleware(
        AuthenticationMiddleware,
        public_paths=auth_config.public_paths,
    )

    # 3. Middleware quản lý session cho luồng OAuth
    app.add_middleware(
        SessionMiddleware,
        secret_key=auth_config.session_secret_key.get_secret_value(),
        session_cookie="oauth_session",
        max_age=600  # 10 phút
    )

    # 4. CAS upload ingress bound. CORS is registered after this block so
    # CORS remains outermost and can decorate limiter-generated 413 responses.
    app.add_middleware(
        AssetUploadBodyLimitMiddleware,
        max_upload_bytes=asset_config.max_upload_bytes,
    )

    # 5. Middleware xử lý Cross-Origin Resource Sharing (CORS)
    ALLOWED_ORIGINS = ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
