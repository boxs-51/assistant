from enum import Enum
from typing import Any

from ...domain.schemas.identity import Identity
from ...runtimes.capability.drivers.base import BaseCapabilityDriver, CapabilityDefinition


class AuthorizationDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class AuthorizationService:
    """Small policy boundary for capability access and execution."""

    def authorize(
        self,
        identity: Identity | dict | None,
        capability: BaseCapabilityDriver | CapabilityDefinition,
    ) -> AuthorizationDecision:
        definition = capability.definition if isinstance(capability, BaseCapabilityDriver) else capability
        if not definition.require_auth:
            return AuthorizationDecision.ALLOW
        if identity is None:
            return AuthorizationDecision.DENY

        identity_scopes = identity.scopes if isinstance(identity, Identity) else set(identity.get("scopes", set()))
        required_scopes = set(getattr(definition, "required_scopes", []))
        if required_scopes and not required_scopes.issubset(identity_scopes):
            return AuthorizationDecision.DENY
        return AuthorizationDecision.ALLOW

    def is_allowed(
        self,
        identity: Identity | dict | None,
        capability: BaseCapabilityDriver | CapabilityDefinition,
    ) -> bool:
        return self.authorize(identity, capability) is AuthorizationDecision.ALLOW
