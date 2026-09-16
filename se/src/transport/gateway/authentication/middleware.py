from starlette.requests import HTTPConnection
from fastapi.responses import JSONResponse
import structlog
from starlette.middleware.base import BaseHTTPMiddleware
import fnmatch

from .manager import AuthenticationManager
from .exceptions import AuthenticationError, InvalidCredentialsError

logger = structlog.get_logger(__name__)

class AuthenticationMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, public_paths: list[str]):
        super().__init__(app)
        self.public_paths = public_paths

    def _is_public(self, path: str) -> bool:
        """Kiểm tra xem một đường dẫn có khớp với bất kỳ mẫu công khai nào không."""
        for pattern in self.public_paths:
            if fnmatch.fnmatch(path, pattern):
                return True
        return False

    async def dispatch(self, connection: HTTPConnection, call_next):
        # Tự động bỏ qua tất cả các request OPTIONS (dành cho CORS preflight)
        # CORSMiddleware sẽ xử lý chúng sau.
        if connection.method == "OPTIONS":
            return await call_next(connection)

        if self._is_public(connection.url.path):
            return await call_next(connection)

        try:
            # ``app.state.container`` is the only application-state dependency boundary.
            container = connection.app.state.container
            auth_manager: AuthenticationManager = container.require("auth_manager")
            identity = await auth_manager.authenticate(connection)
            connection.state.identity = identity
            # Gắn thông tin identity vào log context để dễ dàng truy vết
            structlog.contextvars.bind_contextvars(identity=identity.model_dump(exclude_none=True))
        except (AuthenticationError, InvalidCredentialsError) as e:
            logger.warning("Authentication failed", error=e.detail, path=connection.url.path)
            return JSONResponse(status_code=401, content={"detail": e.detail})

        response = await call_next(connection)
        return response