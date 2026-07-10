import structlog
from abc import ABC, abstractmethod
from sqlalchemy.ext.asyncio import AsyncSession

from ..interfaces.database import DatabaseDriver
from ..repositories.users import UserRepository
from ..repositories.organizations import OrganizationRepository
from ..repositories.members import MemberRepository
from ..repositories.oauth_accounts import OAuthAccountRepository
from ..repositories.pending_registrations import PendingRegistrationRepository
from ..repositories.api_keys import APIKeyRepository
from ..repositories.applications import ApplicationRepository
from ..repositories.conversations import ConversationRepository
from ...authentication.permission import PermissionHelper

logger = structlog.get_logger(__name__)

class AbstractUnitOfWork(ABC):
    users: UserRepository
    organizations: OrganizationRepository
    members: MemberRepository
    oauth_accounts: OAuthAccountRepository
    pending_registrations: PendingRegistrationRepository
    api_keys: APIKeyRepository
    applications: ApplicationRepository
    conversations: ConversationRepository
    permissions: PermissionHelper

    async def __aenter__(self):
        raise NotImplementedError

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        raise NotImplementedError

    @abstractmethod
    async def commit(self):
        raise NotImplementedError

    @abstractmethod
    async def rollback(self):
        raise NotImplementedError

class SqlAlchemyUnitOfWork(AbstractUnitOfWork):
    def __init__(self, db_driver: DatabaseDriver):
        self._session_ctx = None
        self._session_factory = db_driver.get_session

    async def __aenter__(self) -> "SqlAlchemyUnitOfWork":
        self._session_ctx: AsyncSession = self._session_factory()
        self.session = await self._session_ctx.__aenter__()

        self.users = UserRepository(self.session)
        self.organizations = OrganizationRepository(self.session)
        self.members = MemberRepository(self.session)
        self.oauth_accounts = OAuthAccountRepository(self.session)
        self.pending_registrations = PendingRegistrationRepository(self.session)
        self.api_keys = APIKeyRepository(self.session)
        self.applications = ApplicationRepository(self.session)
        self.conversations = ConversationRepository(self.session)
        self.permissions = PermissionHelper(self.session)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type:
                logger.error("Transaction failed, rolling back.", exc_info=(exc_type, exc_val, exc_tb))
                await self.rollback()
        finally:
            if self._session_ctx:
                await self._session_ctx.__aexit__(exc_type, exc_val, exc_tb)

    async def commit(self):
        await self.session.commit()

    async def rollback(self):
        await self.session.rollback()