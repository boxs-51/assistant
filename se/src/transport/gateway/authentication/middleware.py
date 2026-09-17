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

        container = connection.app.state.container
        auth_manager: AuthenticationManager = container.require("auth_manager")
        auth_config = container.config.auth

        if self._is_public(connection.url.path):
            # Public means credentials are optional, not ignored. This lets a
            # login request carry its current guest identity for session claim.
            if auth_manager.has_credentials(connection):
                try:
                    connection.state.identity = await auth_manager.authenticate(
                        connection
                    )
                except (AuthenticationError, InvalidCredentialsError) as error:
                    return JSONResponse(
                        status_code=401,
                        content={"detail": error.detail},
                    )
            return await call_next(connection)

        try:
            issued_guest = None
            if auth_manager.has_credentials(connection):
                identity = await auth_manager.authenticate(connection)
            elif not auth_config.enable:
                issued_guest = await container.require(
                    "guest_session_service"
                ).create_guest()
                identity = issued_guest.identity
            else:
                raise InvalidCredentialsError(
                    "Missing or malformed Authorization header"
                )
            if identity.auth_type == "guest" and not (
                auth_config.allow_guest or not auth_config.enable
            ):
                raise InvalidCredentialsError("Guest access is disabled.")
            connection.state.identity = identity
            # Gắn thông tin identity vào log context để dễ dàng truy vết
            structlog.contextvars.bind_contextvars(identity=identity.model_dump(exclude_none=True))
        except (AuthenticationError, InvalidCredentialsError) as e:
            logger.warning("Authentication failed", error=e.detail, path=connection.url.path)
            return JSONResponse(status_code=401, content={"detail": e.detail})

        response = await call_next(connection)
        if issued_guest is not None:
            response.set_cookie(
                "guest_access_token",
                issued_guest.token.access_token,
                max_age=issued_guest.token.expires_in,
                httponly=True,
                secure=connection.url.scheme == "https",
                samesite="lax",
            )
        return response
