from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MockScenario:
    name: str = "success"
    latency_ms: int = 0
    error_type: str | None = None
    error_message: str = "mock fault"
    error_status_code: int | None = None
    error_code: str | None = None
    fail_operations: set[str] = field(default_factory=set)
    # None means persistent when error_type is set; N means exactly N failures.
    fail_next: int | None = None
    # N means emit N complete chunks, then fail before chunk N+1.
    fail_after_chunks: int | None = None
    stream_chunk_size: int = 1
    fixed_chat_response: str | None = None

    def clone(self) -> "MockScenario":
        return MockScenario(
            name=self.name,
            latency_ms=self.latency_ms,
            error_type=self.error_type,
            error_message=self.error_message,
            error_status_code=self.error_status_code,
            error_code=self.error_code,
            fail_operations=set(self.fail_operations),
            fail_next=self.fail_next,
            fail_after_chunks=self.fail_after_chunks,
            stream_chunk_size=self.stream_chunk_size,
            fixed_chat_response=self.fixed_chat_response,
        )
