from authlib.integrations.starlette_client import OAuth
from typing import Dict, Any
import structlog

from ....infrastructure.config import settings


logger = structlog.get_logger(__name__)

def create_oauth_client() -> OAuth:
    """
    Khởi tạo và cấu hình các client OAuth từ settings.
    """
    oauth = OAuth()
    config = settings.oauth
    # Cấu hình Google OAuth Client
    if config and config.google:
        google_settings = config.google
        if google_settings.client_id and google_settings.client_secret:
            oauth.register(
                name='google',
                client_id=google_settings.client_id,
                client_secret=google_settings.client_secret,
                server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
                client_kwargs={
                    'scope': 'openid email profile'
                }
            )
            logger.info("Google OAuth client registered.")

    # Cấu hình GitHub OAuth Client (ví dụ)
    if config and config.github:
        github_settings = config.github
        if github_settings.client_id and github_settings.client_secret:
            oauth.register(
                name='github',
                client_id=github_settings.client_id,
                client_secret=github_settings.client_secret,
                access_token_url='https://github.com/login/oauth/access_token',
                authorize_url='https://github.com/login/oauth/authorize',
                api_base_url='https://api.github.com/',
                client_kwargs={'scope': 'user:email'}
            )
            logger.info("GitHub OAuth client registered.")
            
    return oauth