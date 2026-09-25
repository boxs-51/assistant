from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class ProviderUploadOutcomeKind(str, Enum):
    SAFE_NO_REMOTE_COMMIT = "SAFE_NO_REMOTE_COMMIT"
    REMOTE_SUCCESS_KNOWN = "REMOTE_SUCCESS_KNOWN"
    REMOTE_OUTCOME_UNKNOWN = "REMOTE_OUTCOME_UNKNOWN"


@dataclass(frozen=True)
class ProviderUploadOutcome:
    kind: ProviderUploadOutcomeKind
    provider_file_id: str | None = None
    provider_uri: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind is ProviderUploadOutcomeKind.REMOTE_SUCCESS_KNOWN:
            if (
                not isinstance(self.provider_file_id, str)
                or not self.provider_file_id.strip()
            ):
                raise ValueError(
                    "REMOTE_SUCCESS_KNOWN requires a stable provider_file_id."
                )
        elif self.provider_file_id is not None:
            raise ValueError(
                "Non-success upload outcomes cannot claim a stable provider_file_id."
            )

    @classmethod
    def safe_no_remote_commit(
        cls, *, metadata: Mapping[str, Any] | None = None
    ) -> "ProviderUploadOutcome":
        return cls(
            kind=ProviderUploadOutcomeKind.SAFE_NO_REMOTE_COMMIT,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def remote_success_known(
        cls,
        *,
        provider_file_id: str,
        provider_uri: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "ProviderUploadOutcome":
        return cls(
            kind=ProviderUploadOutcomeKind.REMOTE_SUCCESS_KNOWN,
            provider_file_id=provider_file_id,
            provider_uri=provider_uri,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def remote_outcome_unknown(
        cls, *, metadata: Mapping[str, Any] | None = None
    ) -> "ProviderUploadOutcome":
        return cls(
            kind=ProviderUploadOutcomeKind.REMOTE_OUTCOME_UNKNOWN,
            metadata=dict(metadata or {}),
        )


class FileProvider(ABC):
    # =========================
    # Files
    # =========================
    @abstractmethod
    async def upload_file(self, **kwargs) -> Any:
        raise NotImplementedError

    async def upload_file_outcome(self, **kwargs) -> ProviderUploadOutcome:
        """
        Optional CAS-F5 provider-side upload classification boundary.

        Existing generic provider file APIs continue to use upload_file().
        Implementations that opt into CAS-F5-B must classify side effects
        without inferring authority from exception text.
        """
        raise NotImplementedError

    @abstractmethod
    async def download_file(self, **kwargs) -> Any:
        raise NotImplementedError

    @abstractmethod
    async def delete_file(self, **kwargs) -> Any:
        raise NotImplementedError

    @abstractmethod
    async def list_files(self, **kwargs) -> Any:
        raise NotImplementedError

    @abstractmethod
    async def get_file(self, **kwargs) -> Any:
        raise NotImplementedError
