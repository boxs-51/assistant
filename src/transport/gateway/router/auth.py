import asyncio
from typing import List
from urllib.parse import urlencode

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse

from ....application.container import ApplicationContainer
from ....domain.schemas.auth import (
    AccessTokenSchema,
    APIKeyCreateSchema,
    APIKeyInfoSchema,
    APIKeyResponseSchema,
    LoginRequestSchema,
    OAuthUserInfoSchema,
    RefreshRequestSchema,
    TokenSchema,
    UserCreateSchema,
    UserMeSchema,
    VerifyOTPRequest,
)
from ....domain.schemas.event import BaseEvent
from ....domain.schemas.identity import Identity
from ....infrastructure.config import settings
from ....infrastructure.storage.core.manager import StorageEngine
from ....infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork
from ..authentication.dependency import get_current_identity
from ..authentication.exceptions import (
    InvalidCredentialsError,
    OTPCooldownError,
    OTPInvalidError,
)
from ..authentication.services.api_key_service import APIKeyService
from ..authentication.services.authentication_facade import AuthenticationFacade
from ..authentication.services.login_service import LoginService
from ..authentication.services.oauth_service import OAuthService
from ..authentication.services.otp_service import OTPStorageService
from ..authentication.services.registration_service import RegistrationService
from ..authentication.services.token_service import TokenService
from ..authentication.services.user_service import UserService
from ..dependencies import get_container

router = APIRouter(prefix="/auth", tags=["Authentication"])
logger = structlog.get_logger(__name__)


def get_uow_factory(container: ApplicationContainer = Depends(get_container)):
    """FastAPI Dependency để lấy Unit of Work Factory."""
    storage: StorageEngine = container.storage
    db_driver = storage.drivers.get("sqlite")
    return lambda: SqlAlchemyUnitOfWork(db_driver)


def get_otp_service(
    container: ApplicationContainer = Depends(get_container),
    uow_factory=Depends(get_uow_factory),
) -> OTPStorageService:
    """FastAPI Dependency để lấy OTPStorageService."""
    storage: StorageEngine = container.storage
    redis_driver = storage.drivers.get("redis")
    return OTPStorageService(redis_driver._client if redis_driver else None, uow_factory)


def get_auth_facade(
    container: ApplicationContainer = Depends(get_container),
    uow_factory=Depends(get_uow_factory),
    otp_service: OTPStorageService = Depends(get_otp_service),
) -> AuthenticationFacade:
    """FastAPI Dependency để lấy AuthenticationFacade."""
    storage: StorageEngine = container.storage
    session_repo = storage.repositories.get("sessions")

    token_service = TokenService(
        uow_factory=uow_factory,
        session_repo=session_repo,
    )
    event_bus = container.eventing_manager.bus

    registration_service = RegistrationService(uow_factory, otp_service, token_service, event_bus)
    login_service = LoginService(uow_factory, token_service)
    oauth_service = OAuthService(uow_factory, token_service, event_bus)
    user_service = UserService(uow_factory)

    return AuthenticationFacade(
        registration_service=registration_service,
        login_service=login_service,
        oauth_service=oauth_service,
        token_service=token_service,
        user_service=user_service,
    )


def get_api_key_service(
    uow_factory=Depends(get_uow_factory),
) -> APIKeyService:
    """FastAPI Dependency để lấy APIKeyService."""
    return APIKeyService(uow_factory=uow_factory)


@router.post("/register/initiate")
async def register_or_resend_otp(
    user_data: UserCreateSchema,
    auth_facade: AuthenticationFacade = Depends(get_auth_facade),
):
    """
    Endpoint xử lý Đăng ký ban đầu VÀ Gửi lại mã OTP (Resend).
    """
    try:
        result = await auth_facade.initiate_registration(user_data)
        return result
    except OTPCooldownError as cooldown_err:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "error": "otp_cooldown_active",
                "message": str(cooldown_err),
                "cooldown_remaining": cooldown_err.remaining_seconds,
            },
        )
    except InvalidCredentialsError as cred_err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(cred_err))


@router.post("/register/verify", response_model=TokenSchema)
async def verify_otp_and_complete(
    payload: VerifyOTPRequest,
    auth_facade: AuthenticationFacade = Depends(get_auth_facade),
):
    """
    Endpoint nhận OTP từ Client để xác thực hoàn tất đăng ký.
    """
    try:
        tokens = await auth_facade.confirm_registration(payload.email, payload.otp)
        return tokens
    except OTPInvalidError as otp_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_or_expired_otp", "message": str(otp_err)},
        )


@router.post("/login", response_model=TokenSchema)
async def login_for_access_token(
    login_data: LoginRequestSchema,
    auth_facade: AuthenticationFacade = Depends(get_auth_facade),
    container: ApplicationContainer = Depends(get_container),
):
    """Endpoint để đăng nhập và nhận token."""
    event_bus = container.eventing_manager.bus
    try:
        tokens = await auth_facade.login(login_data)
        return tokens
    except InvalidCredentialsError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=e.detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.post("/refresh", response_model=AccessTokenSchema)
async def refresh_token(
    refresh_data: RefreshRequestSchema,
    auth_facade: AuthenticationFacade = Depends(get_auth_facade),
    container: ApplicationContainer = Depends(get_container),
):
    """
    Làm mới access token bằng refresh token.
    """
    event_bus = container.eventing_manager.bus
    try:
        new_token = await auth_facade.refresh_access_token(refresh_data.refresh_token)
        return new_token
    except InvalidCredentialsError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=e.detail)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    refresh_data: RefreshRequestSchema,
    auth_facade: AuthenticationFacade = Depends(get_auth_facade),
    container: ApplicationContainer = Depends(get_container),
):
    """
    Đăng xuất và thu hồi refresh token.
    Client nên xóa access token và refresh token ở phía của mình sau khi gọi endpoint này.
    """
    event_bus = container.eventing_manager.bus
    try:
        await auth_facade.logout(refresh_data.refresh_token)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        logger.error("Error during logout", error=str(e), exc_info=True)
        return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/oauth/login/{provider}")
async def oauth_login_redirect(
    provider: str,
    request: Request,
    container: ApplicationContainer = Depends(get_container),
):
    """
    Bắt đầu luồng đăng nhập OAuth bằng cách redirect người dùng đến trang của provider.
    """
    oauth = container.get("oauth")
    if not oauth or provider not in oauth._clients:
        raise HTTPException(status_code=404, detail=f"OAuth provider '{provider}' not configured.")

    redirect_uri = request.url_for("oauth_callback", provider=provider)
    return await oauth.create_client(provider).authorize_redirect(request, redirect_uri)


@router.get("/oauth/callback/{provider}", name="oauth_callback")
async def oauth_callback(
    provider: str,
    request: Request,
    auth_facade: AuthenticationFacade = Depends(get_auth_facade),
    container: ApplicationContainer = Depends(get_container),
):
    """
    Xử lý callback từ OAuth provider sau khi người dùng xác thực.
    """
    oauth = container.get("oauth")
    oauth_client = oauth.create_client(provider) if oauth else None
    if not oauth_client:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"OAuth client for provider '{provider}' is not configured.",
        )

    try:
        token = await oauth_client.authorize_access_token(request)
        user_info_resp = await oauth_client.userinfo(token=token)

        if provider == "github":
            emails = await oauth_client.get("user/emails", token=token)
            primary_email = next((email["email"] for email in emails.json() if email["primary"]), None)
            if not primary_email:
                raise HTTPException(status_code=400, detail="Could not find primary email from GitHub.")
            email = primary_email
            provider_user_id = str(user_info_resp.get("id"))
        else:
            email = user_info_resp.get("email")
            provider_user_id = user_info_resp.get("sub")

        user_schema = OAuthUserInfoSchema(
            email=email,
            provider=user_info_resp.get("provider"),
            provider_user_id=provider_user_id,
            name=user_info_resp.get("name"),
            profile_url=user_info_resp.get("picture_url"),
        )

        tokens = await auth_facade.handle_oauth_callback(provider, user_schema)

        redirect_url = settings.frontend.oauth_callback_url
        if not redirect_url:
            logger.warning("FRONTEND_OAUTH_CALLBACK_URL is not set. Returning tokens as JSON.")
            return JSONResponse(content=tokens.model_dump())

        return RedirectResponse(f"{redirect_url}?{urlencode(tokens.model_dump())}")
    except Exception as e:
        logger.error("Error during OAuth callback", provider=provider, error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="An internal error occurred during OAuth callback.")


@router.post("/oauth/{provider}", response_model=TokenSchema)
async def oauth_login(
    provider: str,
    user_info: OAuthUserInfoSchema,
    auth_facade: AuthenticationFacade = Depends(get_auth_facade),
):
    """
    Endpoint để xử lý callback sau khi người dùng xác thực thành công với OAuth provider.
    Client (frontend) sẽ chịu trách nhiệm thực hiện luồng OAuth 2.0 với provider,
    lấy thông tin người dùng và gửi đến endpoint này.
    """
    try:
        tokens = await auth_facade.handle_oauth_callback(provider, user_info)
        return tokens
    except Exception as e:
        logger.error("Error during OAuth callback handling", provider=provider, error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred during OAuth processing.",
        )


@router.post("/api-keys", response_model=APIKeyResponseSchema, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    key_data: APIKeyCreateSchema,
    identity: Identity = Depends(get_current_identity),
    api_key_service: APIKeyService = Depends(get_api_key_service),
    container: ApplicationContainer = Depends(get_container),
):
    """
    Tạo một API key mới cho người dùng đã được xác thực (qua JWT).
    """
    event_bus = container.eventing_manager.bus
    if identity.auth_type != "jwt":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API keys can only be created by authenticated users.",
        )

    try:
        response = await api_key_service.create_api_key(key_data, identity)
        return response
    except Exception as e:
        logger.error("Failed to create API key", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="An internal error occurred while creating the API key.")


@router.get("/api-keys", response_model=List[APIKeyInfoSchema])
async def list_api_keys(
    identity: Identity = Depends(get_current_identity),
    api_key_service: APIKeyService = Depends(get_api_key_service),
):
    """
    Liệt kê tất cả các API key của người dùng đã xác thực.
    """
    if identity.auth_type != "jwt":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This action requires user authentication.")

    keys = await api_key_service.list_api_keys(identity)
    return keys


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: str,
    identity: Identity = Depends(get_current_identity),
    api_key_service: APIKeyService = Depends(get_api_key_service),
    container: ApplicationContainer = Depends(get_container),
):
    """
    Thu hồi (vô hiệu hóa) một API key.
    """
    event_bus = container.eventing_manager.bus
    if identity.auth_type != "jwt":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This action requires user authentication.")

    success = await api_key_service.revoke_api_key(key_id, identity)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found or you do not have permission to revoke it.",
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserMeSchema)
async def get_current_user(
    identity: Identity = Depends(get_current_identity),
    auth_facade: AuthenticationFacade = Depends(get_auth_facade),
):
    """
    Lấy thông tin của người dùng đã được xác thực (qua JWT).
    """
    if identity.auth_type != "jwt":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires user authentication via JWT.",
        )

    try:
        user_info = await auth_facade.get_current_user_info(identity)
        return user_info
    except InvalidCredentialsError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=e.detail)