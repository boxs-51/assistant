import structlog
from abc import ABC, abstractmethod
from typing import List, TYPE_CHECKING
from sqlalchemy.ext.asyncio import AsyncSession

from ..interfaces.database import DatabaseDriver
from ..repositories.user_data.users import UserRepository
from ..repositories.user_data.organizations import OrganizationRepository
from ..repositories.user_data.members import MemberRepository
from ..repositories.user_data.oauth_accounts import OAuthAccountRepository
from ..repositories.user_data.pending_registrations import PendingRegistrationRepository
from ..repositories.user_data.api_keys import APIKeyRepository
from ..repositories.user_data.applications import ApplicationRepository
from ..repositories.chat_data.projects import ProjectRepository
from ..repositories.chat_data.sessions import SessionRepository
from ..repositories.chat_data.attachments import AttachmentRepository

from .events import StorageEventFactory
from ...authentication.permission import PermissionHelper

if TYPE_CHECKING:
    from ...event_bus.bus import EventBus

logger = structlog.get_logger(__name__)

class AbstractUnitOfWork(ABC):
    users: UserRepository
    organizations: OrganizationRepository
    members: MemberRepository
    oauth_accounts: OAuthAccountRepository
    pending_registrations: PendingRegistrationRepository
    api_keys: APIKeyRepository
    applications: ApplicationRepository
    projects: ProjectRepository
    sessions: SessionRepository
    attachments: AttachmentRepository
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
    def __init__(self, db_driver: DatabaseDriver, event_bus: "EventBus"):
        self._session_ctx = None
        self._session_factory = db_driver.get_session
        self._event_bus = event_bus

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
        self.projects = ProjectRepository(self.session)
        self.sessions = SessionRepository(self.session)
        self.attachments = AttachmentRepository(self.session)
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
        """
        Commit transaction và phát các sự kiện tương ứng.
        """
        # Tạo sự kiện từ các thay đổi trong session TRƯỚC khi commit
        storage_events = StorageEventFactory.create_events_from_session(self.session)
        
        await self.session.commit()
        
        # Sau khi commit thành công, publish các sự kiện
        # for event in storage_events:
        #     self._event_bus.publish(event) # Fire-and-forget

    async def rollback(self):
        await self.session.rollback()