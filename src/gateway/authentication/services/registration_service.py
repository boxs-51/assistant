import structlog
import secrets

from ....event_bus.bus import EventBus
from ....schemas.event import BaseEvent
from ....schemas.auth import UserCreateSchema, TokenSchema
from ..exceptions import InvalidCredentialsError, OTPCooldownError, OTPInvalidError
from ....storage.core.unit_of_work import SqlAlchemyUnitOfWork
from .otp_service import OTPStorageService
from .token_service import TokenService
from .. import password as PwdHelper
from typing import Callable

logger = structlog.get_logger(__name__)

class RegistrationService:
    def __init__(
        self,
        uow_factory: Callable[[], SqlAlchemyUnitOfWork],
        otp_storage: OTPStorageService,
        token_service: TokenService,
        event_bus: EventBus
    ):
        self.uow_factory = uow_factory
        self.otp_storage = otp_storage
        self.token_service = token_service
        self.event_bus = event_bus

    def _generate_otp(self) -> str:
        """Sinh chuỗi số ngẫu nhiên an toàn bảo mật gồm 6 chữ số."""
        return "".join(secrets.choice("0123456789") for _ in range(6))

    async def initiate_registration(self, user_data: UserCreateSchema) -> dict:
        """Giai đoạn 1: Bắt đầu đăng ký hoặc gửi lại OTP."""
        async with self.uow_factory() as uow:
            existing_user = await uow.users.get_by_email(user_data.email)
            if existing_user:
                raise InvalidCredentialsError("Email already registered")

        remaining_cooldown = await self.otp_storage.check_cooldown(user_data.email)
        if remaining_cooldown > 0:
            raise OTPCooldownError(remaining_seconds=remaining_cooldown)

        otp = self._generate_otp()
        hashed_password = PwdHelper.get_password_hash(user_data.password)
        pending_user_payload = {
            "email": user_data.email,
            "hashed_password": hashed_password,
            "name": user_data.name
        }

        await self.otp_storage.save_pending_registration(user_data.email, pending_user_payload, otp)
        logger.info("OTP Sent Successfully", email=user_data.email, code=otp)

        return {"status": "success", "message": "OTP has been sent.", "cooldown_seconds": self.otp_storage.cooldown_ttl}

    async def confirm_registration(self, email: str, otp: str) -> TokenSchema:
        """Giai đoạn 2: Xác thực OTP và hoàn tất đăng ký."""
        user_payload = await self.otp_storage.verify_and_get_data(email, otp)
        if not user_payload:
            raise OTPInvalidError("Invalid or expired OTP code.")

        async with self.uow_factory() as uow:
            new_user = await uow.users.create(email=user_payload["email"], hashed_password=user_payload["hashed_password"], name=user_payload.get("name"))
            org_name = f"{new_user.name or user_payload['email']}'s Organization"
            new_org = await uow.organizations.create(name=org_name, owner_id=new_user.id)
            await uow.members.create(organization_id=new_org.id, user_id=new_user.id, role="admin")
            await uow.commit()

            # Phát sự kiện user.created sau khi đã commit thành công
            # user_created_event = BaseEvent(
            #     event_name="user.created",
            #     payload={
            #         "user_id": new_user.id,
            #         "email": new_user.email,
            #         "organization_id": new_org.id,
            #     }
            # )
            # await self.event_bus.publish(user_created_event)

            return await self.token_service.create_user_tokens(new_user.id, new_user.email)