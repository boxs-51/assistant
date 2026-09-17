import secrets
from typing import Callable

import structlog

from .....infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork
from .....transport.gateway.authentication import password as PwdHelper
from ..exceptions import OTPInvalidError
from .otp_service import OTPStorageService

logger = structlog.get_logger(__name__)


class PasswordResetService:
    def __init__(
        self,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        otp_storage: OTPStorageService,
    ):
        self.uow_factory = uow_factory
        self.otp_storage = otp_storage

    @staticmethod
    def _generate_otp() -> str:
        return "".join(secrets.choice("0123456789") for _ in range(6))

    async def initiate(self, email: str) -> dict:
        """Return a uniform response so an unknown email cannot be enumerated."""
        async with self.uow_factory() as uow:
            user = await uow.users.get_by_email(email)

        if user:
            remaining = await self.otp_storage.check_password_reset_cooldown(email)
            if remaining <= 0:
                otp = self._generate_otp()
                await self.otp_storage.save_pending_password_reset(
                    email,
                    {"user_id": str(user.id), "email": user.email},
                    otp,
                )
                # Authentication secrets must never be emitted to application logs.
                logger.info("Password reset OTP issued", email=email)

        return {
            "status": "success",
            "message": "If the account exists, a reset code has been sent.",
            "cooldown_seconds": self.otp_storage.cooldown_ttl,
        }

    async def confirm(self, email: str, otp: str, new_password: str) -> dict:
        reset_data = await self.otp_storage.verify_password_reset(email, otp)
        if not reset_data:
            raise OTPInvalidError("Invalid or expired reset code.")

        async with self.uow_factory() as uow:
            updated = await uow.users.set_password(
                reset_data["user_id"],
                PwdHelper.get_password_hash(new_password),
            )
            if not updated:
                raise OTPInvalidError("Invalid or expired reset code.")
            await uow.commit()

        return {"status": "success", "message": "Password has been reset."}
