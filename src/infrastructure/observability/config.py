from dataclasses import dataclass, field
import os
@dataclass
class LoggingConfig:
    level: str = "INFO"

    development: bool = field(
        default_factory=lambda: os.getenv("ENV", "local").lower() in ["local", "dev", "development"]
    )

@dataclass
class TracingConfig:
    enable: bool = False
    otlp_endpoint: str = "http://localhost:4318"

@dataclass
class ObservabilityConfig:
    service_name: str
    service_version: str
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    tracing: TracingConfig = field(default_factory=TracingConfig)