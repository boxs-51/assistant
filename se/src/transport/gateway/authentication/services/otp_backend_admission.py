"""Fail-closed OTP backend selection. Not a legacy-writer revocation certificate."""
from __future__ import annotations

class OTPBackendUnavailable(RuntimeError):
    """Shared OTP backend cannot be established."""

def select_otp_cache_driver(storage_engine, *, auth_enabled: bool):
    """Never silently downgrade auth OTPs to independent SQL/InMemory issuance.

    This selects Redis only. It cannot block old Redis SET writers: a verified
    external writer-drain / ACL epoch is separately required before rollout.
    """
    if not auth_enabled:
        return storage_engine.get_cache_driver()
    registry = getattr(storage_engine, "drivers", None)
    if registry is None or registry.get_entry("redis") is None:
        raise OTPBackendUnavailable("OTP Redis authority is not configured")
    if not registry.is_available("redis"):
        raise OTPBackendUnavailable("OTP Redis authority is unavailable")
    try:
        driver = storage_engine.get_cache_driver("redis")
    except Exception:
        raise OTPBackendUnavailable("OTP Redis authority selection failed") from None
    if driver is None:
        raise OTPBackendUnavailable("OTP Redis authority returned no driver")
    return driver
