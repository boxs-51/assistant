import structlog
from typing import Optional
from sqlalchemy import select, delete, func
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session
from ...interfaces.repository import BaseRepository
from ...interfaces.database import DatabaseDriver
from ...models.sql.user_data.pending_registration import PendingRegistration

logger = structlog.get_logger(__name__)

class PendingRegistrationRepository(BaseRepository):
    """
    Repository chịu trách nhiệm cho các thao tác trên đối tượng PendingRegistration.
    Được tách ra từ UserRepository để tuân thủ Single Responsibility Principle.
    """
    def __init__(self, session: Session):
        self.session = session

    async def create_or_update(self, email: str, payload: str, expires_in_seconds: int) -> PendingRegistration:
        """
        Lưu thông tin đăng ký tạm thời vào Database.
        Nếu đã tồn tại, sẽ cập nhật thay vì tạo mới (UPSERT).
        """
        # PostgreSQL columns are TIMESTAMP WITHOUT TIME ZONE containing UTC.
        # Bind one UTC-naive instant for both persisted timestamp fields.
        utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
        expires_at = utc_now + timedelta(seconds=expires_in_seconds)

        # Tìm và xóa bản ghi cũ nếu có
        existing = await self.session.get(PendingRegistration, email)
        if existing:
            await self.session.delete(existing)
            await self.session.flush() # Đảm bảo lệnh delete được thực thi trước khi add

        # Tạo bản ghi mới
        new_pending = PendingRegistration(
            email=email,
            payload=payload,
            expires_at=expires_at,
            created_at=utc_now
        )
        self.session.add(new_pending)
        await self.session.flush()
        logger.info("Saved pending registration to session", email=email)
        return new_pending

    async def get_by_email(self, email: str) -> Optional[PendingRegistration]:
        """Lấy một bản ghi đăng ký tạm bằng email."""
        return await self.session.get(PendingRegistration, email)

    async def get_remaining_cooldown(self, email: str, cooldown_ttl: int) -> int:
        """
        Tính toán thời gian cooldown gửi lại OTP dựa trên bản ghi database.
        """
        pending = await self.session.get(PendingRegistration, email)
        if not pending:
            return 0
            
        # Cooldown tính dựa trên: thời gian tạo + thời gian cooldown cấu hình
        created_at = pending.created_at
        if created_at.tzinfo is None:
            # The persisted UTC-naive timestamp represents a UTC instant.
            created_at = created_at.replace(tzinfo=timezone.utc)
        else:
            created_at = created_at.astimezone(timezone.utc)
        elapsed_time = (datetime.now(timezone.utc) - created_at).total_seconds()
        remaining = cooldown_ttl - int(elapsed_time)
        return max(0, remaining)

    async def get_and_delete(self, email: str) -> Optional[PendingRegistration]:
        """
        Consume one row atomically using DELETE ... RETURNING.

        The returned row is provisional until the caller's UOW commits.
        Do not expose an ORM object previously read by SELECT: after a
        concurrent DELETE that object may still hold stale OTP payload.
        PostgreSQL evaluates expiry in RETURNING after acquiring the
        target row lock (including after waiting for another writer).
        """
        dialect = self.session.get_bind().dialect.name
        if dialect not in ("postgresql", "sqlite"):
            raise RuntimeError(
                "Atomic OTP consumption is unsupported on this database dialect"
            )

        columns = (
            PendingRegistration.email,
            PendingRegistration.payload,
            PendingRegistration.expires_at,
            PendingRegistration.created_at,
        )
        if dialect == "postgresql":
            # expires_at is TIMESTAMP WITHOUT TIME ZONE and stores UTC.
            # clock_timestamp(), unlike transaction_timestamp()/now(),
            # advances while the DELETE waits on another transaction.
            live_at_delete = (
                PendingRegistration.expires_at
                > func.timezone("UTC", func.clock_timestamp())
            ).label("live_at_delete")
            columns = (*columns, live_at_delete)

        stmt = (
            delete(PendingRegistration)
            .where(PendingRegistration.email == email)
            .returning(*columns)
            .execution_options(synchronize_session=False)
        )
        row = (await self.session.execute(stmt)).mappings().one_or_none()
        if row is None:
            # A competing transaction already consumed it; no stale data.
            return None

        if dialect == "postgresql":
            is_live = bool(row["live_at_delete"])
        else:
            # SQLite has no PostgreSQL clock_timestamp(); it serializes
            # DELETE writers, so evaluate only AFTER the winning DELETE.
            expires_at = row["expires_at"]
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            is_live = expires_at > datetime.now(timezone.utc)

        if not is_live:
            # Expired rows are consumed for cleanup but never authenticate.
            logger.warning("Pending registration expired in database")
            return None

        # Fresh, transient DTO using the existing mapped record shape.
        # Never return a potentially stale session identity-map instance.
        return PendingRegistration(
            email=row["email"],
            payload=row["payload"],
            expires_at=row["expires_at"],
            created_at=row["created_at"],
        )

    async def cleanup_expired(self):
        """Xóa tất cả các bản ghi đã hết hạn."""
        utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
        stmt = delete(PendingRegistration).where(PendingRegistration.expires_at < utc_now)
        await self.session.execute(stmt)
        await self.session.flush()