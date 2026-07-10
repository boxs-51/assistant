from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import RedirectResponse, JSONResponse, Response
import structlog
from urllib.parse import urlencode
from typing import List
from ..schemas.auth import (
    UserCreateSchema, LoginRequestSchema,
    TokenSchema,
    OAuthUserInfoSchema, RefreshRequestSchema,
    AccessTokenSchema, APIKeyCreateSchema, 
    APIKeyResponseSchema, APIKeyInfoSchema,
    UserMeSchema, VerifyOTPRequest
)
from ..authentication.exceptions import InvalidCredentialsError, OTPCooldownError, OTPInvalidError
# from ..authentication.services.services import AuthenticationService
from ..authentication.services.otp_service import OTPStorageService
from ..authentication.services.api_key_service import APIKeyService
from ..authentication.services.token_service import TokenService
from ..authentication.services.registration_service import RegistrationService
from ..authentication.services.login_service import LoginService
from ..authentication.services.oauth_service import OAuthService
from ..authentication.services.user_service import UserService
from ..storage.core.unit_of_work import SqlAlchemyUnitOfWork
from ..authentication.services.authentication_facade import AuthenticationFacade
from ..storage.core.manager import StorageEngine
from ..config import settings
from ..authentication.dependency import get_current_identity
from ..schemas.identity import Identity

router = APIRouter(prefix="/auth", tags=["Authentication"])
logger = structlog.get_logger(__name__)


def get_otp_service(request: Request) -> OTPStorageService:
    """FastAPI Dependency để lấy OTPStorageService."""
    storage: StorageEngine = request.app.state.storage
    redis_driver = storage.drivers.get("redis")
    uow_factory = get_uow_factory(request)
    return OTPStorageService(redis_driver._client if redis_driver else None, uow_factory)

def get_uow_factory(request: Request):
    """FastAPI Dependency để lấy Unit of Work Factory."""
    storage: StorageEngine = request.app.state.storage
    db_driver = storage.drivers.get("sqlite") # Hoặc postgres
    return lambda: SqlAlchemyUnitOfWork(db_driver)

def get_auth_facade(request: Request) -> AuthenticationFacade:
    """FastAPI Dependency để lấy AuthenticationFacade."""
    storage: StorageEngine = request.app.state.storage
    
    # Khởi tạo các service con
    otp_service = get_otp_service(request)
    uow_factory = get_uow_factory(request)
    session_repo = storage.repositories.get("sessions")
    token_service = TokenService(
        uow_factory=uow_factory, 
        session_repo=session_repo)
    registration_service = RegistrationService(uow_factory, otp_service, token_service)
    login_service = LoginService(uow_factory, token_service)
    oauth_service = OAuthService(uow_factory, token_service)
    user_service = UserService(uow_factory)

    # Khởi tạo Facade
    return AuthenticationFacade(
        registration_service=registration_service,
        login_service=login_service,
        oauth_service=oauth_service,
        token_service=token_service,
        user_service=user_service,
    )

def get_api_key_service(request: Request) -> APIKeyService:
    """FastAPI Dependency để lấy APIKeyService."""
    storage: StorageEngine = request.app.state.storage
    uow_factory = get_uow_factory(request)

    return APIKeyService(uow_factory=uow_factory)

@router.post("/register/initiate")
async def register_or_resend_otp(
    user_data: UserCreateSchema,
    auth_facade: AuthenticationFacade = Depends(get_auth_facade)
):
    """
    Endpoint xử lý Đăng ký ban đầu VÀ Gửi lại mã OTP (Resend).
    """
    try:
        result = await auth_facade.initiate_registration(user_data)
        return result
    except OTPCooldownError as cooldown_err:
        # Báo cáo cụ thể số giây còn lại cho phía Client cấu hình UI chặn nút bấm (Disable Button)
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "error": "otp_cooldown_active",
                "message": str(cooldown_err),
                "cooldown_remaining": cooldown_err.remaining_seconds
            }
        )
    except InvalidCredentialsError as cred_err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(cred_err))

@router.post("/register/verify", response_model=TokenSchema)
async def verify_otp_and_complete(
    payload: VerifyOTPRequest,
    auth_facade: AuthenticationFacade = Depends(get_auth_facade)
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
            detail={"error": "invalid_or_expired_otp", "message": str(otp_err)}
        )
    
@router.post("/login", response_model=TokenSchema)
async def login_for_access_token(
    login_data: LoginRequestSchema,
    auth_facade: AuthenticationFacade = Depends(get_auth_facade)
):
    """Endpoint để đăng nhập và nhận token."""
    try:
        tokens = await auth_facade.login(login_data)
        return tokens
    except InvalidCredentialsError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=e.detail, headers={"WWW-Authenticate": "Bearer"})

@router.post("/refresh", response_model=AccessTokenSchema)
async def refresh_token(
    refresh_data: RefreshRequestSchema,
    auth_facade: AuthenticationFacade = Depends(get_auth_facade)
):
    """
    Làm mới access token bằng refresh token.
    """
    try:
        new_token = await auth_facade.refresh_access_token(refresh_data.refresh_token)
        return new_token
    except InvalidCredentialsError as e:
        # Nếu refresh token không hợp lệ, yêu cầu đăng nhập lại
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=e.detail)

@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    refresh_data: RefreshRequestSchema,
    auth_facade: AuthenticationFacade = Depends(get_auth_facade)
):
    """
    Đăng xuất và thu hồi refresh token.
    Client nên xóa access token và refresh token ở phía của mình sau khi gọi endpoint này.
    """
    try:
        await auth_facade.logout(refresh_data.refresh_token)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        logger.error("Error during logout", error=str(e), exc_info=True)
        # Trả về thành công ngay cả khi có lỗi để tránh tiết lộ thông tin về sự tồn tại của token.
        return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.get("/oauth/login/{provider}")
async def oauth_login_redirect(provider: str, request: Request):
    """
    Bắt đầu luồng đăng nhập OAuth bằng cách redirect người dùng đến trang của provider.
    """
    if provider not in request.app.state.oauth._clients:
        raise HTTPException(status_code=404, detail=f"OAuth provider '{provider}' not configured.")

    # Lấy redirect_uri từ URL của request hiện tại
    redirect_uri = request.url_for('oauth_callback', provider=provider)
    
    # Tạo URL xác thực và redirect
    return await request.app.state.oauth.create_client(provider).authorize_redirect(request, redirect_uri)

@router.get("/oauth/callback/{provider}", name="oauth_callback")
async def oauth_callback(
    provider: str,
    request: Request,
    auth_facade: AuthenticationFacade = Depends(get_auth_facade)
):
    """
    Xử lý callback từ OAuth provider sau khi người dùng xác thực.
    """

    try:
        token = await request.app.state.oauth.create_client(provider).authorize_access_token(request)
        user_info_resp = await request.app.state.oauth.create_client(provider).userinfo(token=token)

        # Chuẩn hóa thông tin người dùng từ các provider khác nhau
        if provider == 'github':
            # GitHub có thể không trả về email chính trong userinfo, cần gọi API riêng
            emails = await request.app.state.oauth.create_client(provider).get('user/emails', token=token)
            primary_email = next((email['email'] for email in emails.json() if email['primary']), None)
            if not primary_email:
                raise HTTPException(status_code=400, detail="Could not find primary email from GitHub.")
            email = primary_email
            provider_user_id = str(user_info_resp.get('id'))
        else: # Mặc định cho Google và các provider tuân thủ OpenID Connect
            email = user_info_resp.get('email')
            provider_user_id = user_info_resp.get('sub')

        user_schema = OAuthUserInfoSchema(
            email=email, 
            provider=user_info_resp.get('provider'),
            provider_user_id=provider_user_id, 
            name=user_info_resp.get('name'),
            profile_url=user_info_resp.get('picture_url')
        )
        
        tokens = await auth_facade.handle_oauth_callback(provider, user_schema)

        # Chuyển hướng người dùng về frontend với token trong query params.
        # Frontend sẽ đọc các token này từ URL, lưu vào local storage/cookie và xóa chúng khỏi URL.
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
    auth_facade: AuthenticationFacade = Depends(get_auth_facade)
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
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An internal error occurred during OAuth processing.")

@router.post("/api-keys", response_model=APIKeyResponseSchema, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    key_data: APIKeyCreateSchema,
    identity: Identity = Depends(get_current_identity),
    api_key_service: APIKeyService = Depends(get_api_key_service)
):
    """
    Tạo một API key mới cho người dùng đã được xác thực (qua JWT).
    """
    if identity.auth_type != 'jwt':
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="API keys can only be created by authenticated users.")
    
    try:
        return await api_key_service.create_api_key(key_data, identity)
    except Exception as e:
        logger.error("Failed to create API key", error=str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="An internal error occurred while creating the API key.")

@router.get("/api-keys", response_model=List[APIKeyInfoSchema])
async def list_api_keys(
    identity: Identity = Depends(get_current_identity),
    api_key_service: APIKeyService = Depends(get_api_key_service)
):
    """
    Liệt kê tất cả các API key của người dùng đã xác thực.
    """
    if identity.auth_type != 'jwt':
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This action requires user authentication.")
    
    keys = await api_key_service.list_api_keys(identity)
    return keys

@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: str,
    identity: Identity = Depends(get_current_identity),
    api_key_service: APIKeyService = Depends(get_api_key_service)
):
    """
    Thu hồi (vô hiệu hóa) một API key.
    """
    if identity.auth_type != 'jwt':
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This action requires user authentication.")

    success = await api_key_service.revoke_api_key(key_id, identity)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found or you do not have permission to revoke it.")
    
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.get("/me", response_model=UserMeSchema)
async def get_current_user(
    identity: Identity = Depends(get_current_identity),
    auth_facade: AuthenticationFacade = Depends(get_auth_facade)
):
    """
    Lấy thông tin của người dùng đã được xác thực (qua JWT).
    """
    if identity.auth_type != 'jwt':
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This action requires user authentication via JWT.")
    
    try:
        user_info = await auth_facade.get_current_user_info(identity)
        return user_info
    except InvalidCredentialsError as e:
        # Nếu user không được tìm thấy trong DB vì một lý do nào đó
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=e.detail)