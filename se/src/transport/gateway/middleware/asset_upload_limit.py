from __future__ import annotations

from starlette.middleware.body_limit import RequestBodyLimitMiddleware


class AssetUploadBodyLimitMiddleware:
    """Path-scope Starlette's raw body limiter to canonical asset uploads."""

    DEFAULT_MULTIPART_OVERHEAD_BYTES = 64 * 1024

    def __init__(
        self,
        app,
        *,
        max_upload_bytes: int,
        multipart_overhead_bytes: int = DEFAULT_MULTIPART_OVERHEAD_BYTES,
    ) -> None:
        if max_upload_bytes <= 0:
            raise ValueError("max_upload_bytes must be positive")
        if multipart_overhead_bytes < 0:
            raise ValueError("multipart_overhead_bytes cannot be negative")
        self.app = app
        self.max_upload_bytes = int(max_upload_bytes)
        self.multipart_overhead_bytes = int(multipart_overhead_bytes)
        self.max_request_bytes = (
            self.max_upload_bytes + self.multipart_overhead_bytes
        )
        self.limited_app = RequestBodyLimitMiddleware(
            app,
            max_body_size=self.max_request_bytes,
        )

    @staticmethod
    def _is_asset_upload(scope) -> bool:
        if scope.get("type") != "http":
            return False
        if str(scope.get("method", "")).upper() != "POST":
            return False
        path = str(scope.get("path", "")).rstrip("/")
        return path == "/v1/assets"

    async def __call__(self, scope, receive, send) -> None:
        if self._is_asset_upload(scope):
            await self.limited_app(scope, receive, send)
            return
        await self.app(scope, receive, send)
