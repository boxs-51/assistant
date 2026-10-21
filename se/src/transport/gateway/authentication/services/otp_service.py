import json
from datetime import datetime, timezone
from typing import Callable, Optional

import structlog

from .....infrastructure.storage.core.unit_of_work import SqlAlchemyUnitOfWork
from .....infrastructure.storage.interfaces.cache import (
    CacheDriver, CacheWriteNotDispatched, CacheWriteLegacyAuthorityUnknown,
)

logger = structlog.get_logger(__name__)


class OTPStorageService:
    def __init__(self, cache_driver: CacheDriver, uow_factory: Callable[[], SqlAlchemyUnitOfWork]):
        self.cache_driver = cache_driver
        self.uow_factory = uow_factory
        self.otp_ttl = 300
        self.cooldown_ttl = 60

    async def _reserve_issuance(self, email: str, purpose: str) -> None:
        """Commit a cross-process SQL issuance fence before any Redis SET.

        This pre-dispatch commit is the authoritative first-writer claim. A
        later instance with a newly uninitialized RedisDriver cannot issue
        another independent SQL OTP after the original Redis ACK was lost.
        Legacy test UOW fakes without a repository reservation capability
        remain fixture-only; a real SQL UOW MUST expose the fence.
        """
        async with self.uow_factory() as uow:
            reserver = getattr(uow.pending_registrations, "reserve_otp_issuance", None)
            if not callable(reserver):
                if isinstance(uow, SqlAlchemyUnitOfWork):
                    raise RuntimeError("durable OTP issuance authority unavailable")
                return
            await reserver(email, purpose, self.otp_ttl)
            # A failed/unknown COMMIT aborts issuance before touching Redis.
            await uow.commit()

    async def save_pending_registration(self, email: str, user_data: dict, otp: str) -> None:
        payload_str = json.dumps({
            "user_data": user_data,
            "otp": otp,
            "purpose": "registration",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        # Durable first-writer reservation must COMMIT before Redis may execute.
        # On conflict or unknown COMMIT no cache/SQL dispatch is allowed.
        await self._reserve_issuance(email, "registration")
        if self.cache_driver is not None:
            try:
                await self.cache_driver.set(f"pending_reg:{email}", payload_str, self.otp_ttl)
            except CacheWriteLegacyAuthorityUnknown:
                # The modern SET was not dispatched, but a pre-fence legacy
                # Redis challenge may already exist. Never mint an independent
                # SQL OTP without a proven single authority.
                logger.error("OTP legacy Redis authority unverified; SQL fallback denied")
                raise
            except CacheWriteNotDispatched:
                # Genuine no-cache-authority controls retain the documented
                # confirmed pre-dispatch SQL fallback contract.
                logger.warning("OTP cache write not dispatched; using database fallback")
            except Exception as exc:
                # Redis may have committed SET before its ACK was lost.
                # Never create a second live authority for this challenge.
                logger.error("OTP cache write unconfirmed; database fallback denied",
                             error_type=type(exc).__name__)
                raise
            else:
                # The challenge is already live in Redis. Failure of the
                # cooldown write cannot justify duplicating it in SQL.
                try:
                    await self.cache_driver.set(
                        f"otp_cooldown:{email}", "active", self.cooldown_ttl
                    )
                except Exception as exc:
                    logger.error("OTP cooldown cache write unconfirmed",
                                 error_type=type(exc).__name__)
                    raise
                return

        async with self.uow_factory() as uow:
            await uow.pending_registrations.create_or_update(
                email=email, payload=payload_str, expires_in_seconds=self.otp_ttl
            )
            await uow.commit()

    async def check_cooldown(self, email: str) -> int:
        try:
            if self.cache_driver:
                ttl = await self.cache_driver.get_ttl(f"otp_cooldown:{email}")
                return max(0, ttl) if ttl else 0
        except Exception as exc:
            logger.error("Redis error during cooldown check; database cooldown lookup", error_type=type(exc).__name__)

        async with self.uow_factory() as uow:
            return await uow.pending_registrations.get_remaining_cooldown(email, self.cooldown_ttl)

    async def save_pending_password_reset(self, email: str, user_data: dict, otp: str) -> None:
        payload_str = json.dumps({
            "user_data": user_data,
            "otp": otp,
            "purpose": "password_reset",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        await self._reserve_issuance(email, "password_reset")
        if self.cache_driver is not None:
            try:
                await self.cache_driver.set(
                    f"pending_password_reset:{email}", payload_str, self.otp_ttl
                )
            except CacheWriteLegacyAuthorityUnknown:
                logger.error("Password-reset legacy Redis authority unverified; SQL fallback denied")
                raise
            except CacheWriteNotDispatched:
                logger.warning(
                    "Password-reset OTP cache write not dispatched; using database fallback"
                )
            except Exception as exc:
                logger.error("Password-reset OTP cache write unconfirmed; "
                             "database fallback denied",
                             error_type=type(exc).__name__)
                raise
            else:
                try:
                    await self.cache_driver.set(
                        f"password_reset_cooldown:{email}", "active", self.cooldown_ttl
                    )
                except Exception as exc:
                    logger.error("Password-reset OTP cooldown cache write unconfirmed",
                                 error_type=type(exc).__name__)
                    raise
                return

        async with self.uow_factory() as uow:
            await uow.pending_registrations.create_or_update(
                email=email, payload=payload_str, expires_in_seconds=self.otp_ttl
            )
            await uow.commit()

    async def check_password_reset_cooldown(self, email: str) -> int:
        try:
            if self.cache_driver:
                ttl = await self.cache_driver.get_ttl(f"password_reset_cooldown:{email}")
                return max(0, ttl) if ttl else 0
        except Exception as exc:
            logger.error(
                "Redis error during password-reset cooldown check, falling back to database",
                error_type=type(exc).__name__,
            )

        async with self.uow_factory() as uow:
            return await uow.pending_registrations.get_remaining_cooldown(email, self.cooldown_ttl)

    async def verify_and_get_data(self, email: str, input_otp: str) -> Optional[dict]:
        """Consume and validate a one-time registration challenge."""
        raw_data = await self._consume_cache_value(f"pending_reg:{email}")
        if raw_data is None:
            raw_data = await self._consume_database_value(email)
        if raw_data is None:
            return None

        data = json.loads(raw_data)
        if data.get("purpose") in (None, "registration") and data.get("otp") == input_otp:
            return data.get("user_data")
        return None

    async def verify_password_reset(self, email: str, input_otp: str) -> Optional[dict]:
        """Consume and validate a one-time password-reset challenge."""
        raw_data = await self._consume_cache_value(f"pending_password_reset:{email}")
        if raw_data is None:
            raw_data = await self._consume_database_value(email)
        if raw_data is None:
            return None

        data = json.loads(raw_data)
        if data.get("purpose") != "password_reset" or data.get("otp") != input_otp:
            return None
        return data.get("user_data")

    async def _consume_cache_value(self, key: str) -> Optional[str]:
        if self.cache_driver is None:
            return None

        # No GET->DELETE compatibility fallback. Lack of an atomic pop
        # capability is an error, not proof that the key is absent.
        consumer = getattr(self.cache_driver, "consume_one_time", None)
        if not callable(consumer):
            raise RuntimeError("OTP cache does not support atomic consume_one_time")
        try:
            raw_data = await consumer(key)
        except Exception as exc:
            # A command may already have consumed the key before its reply
            # was lost. Never convert UNCERTAIN to MISSING or retry in SQL.
            # Redis exception text/tracebacks may contain connection secrets.
            logger.error("OTP atomic cache consumption unconfirmed",
                         error_type=type(exc).__name__)
            raise
        if raw_data is not None and type(raw_data) is not str:
            raise RuntimeError("OTP cache returned an unconfirmed atomic consume receipt")
        return raw_data

    async def _consume_database_value(self, email: str) -> Optional[str]:
        """Disclose an OTP payload only after the winning DELETE commits.

        MISSING/EXPIRED return None; any repository or COMMIT exception
        propagates as UNCERTAIN/ERROR, never as MISSING or a retry. The
        repository may return a provisional payload from DELETE RETURNING,
        but the service must not authenticate against it before COMMIT.
        """
        async with self.uow_factory() as uow:
            pending_record = await uow.pending_registrations.get_and_delete(email)
            provisional_payload = (
                pending_record.payload if pending_record is not None else None
            )
            if provisional_payload is not None and type(provisional_payload) is not str:
                raise RuntimeError("DB OTP consume returned an invalid payload receipt")

            # Even the no-row/expired result is committed (the latter may
            # have deleted an expired row). Never catch ambiguous failures.
            await uow.commit()
            return provisional_payload

