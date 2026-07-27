from fastapi import FastAPI

import structlog
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from ..infrastructure.config.core import ConfigLoader, ConfigurationRegistry
from ..infrastructure.config import settings
from infrastructure.observability import ObservabilityConfig, LoggingConfig, TracingConfig
from .gateway.middleware.observability import gateway_metrics
from ..runtime.kernel.kernel import RuntimeKernel
from ..runtime.kernel.manifest import RuntimeManifest
from .gateway.runtime import GatewayRuntime
from .gateway.middleware.factory import create_middleware_stack
from .gateway.router import (
    auth_router, files_router, models_router, chat_router, embeddings_router,
    admin_router, agent_router, tool_router, events_router, health_router
)

app = FastAPI(title="AI Gateway")
logger = structlog.get_logger(__name__)

create_middleware_stack(app)

# Import các router từ các module
app.include_router(auth_router)
app.include_router(files_router)
app.include_router(models_router)
app.include_router(chat_router)
app.include_router(embeddings_router)
app.include_router(admin_router)
app.include_router(agent_router)
app.include_router(tool_router)
app.include_router(events_router)
app.include_router(health_router)

@app.on_event("startup")
async def startup_event():
    """Initializes the application and the Runtime Kernel."""
    # 1. Load configuration and setup observability (logging, tracing)
    # This must happen before anything else.
    loader = ConfigLoader(default_config_path="config/gateway/default.yaml")
    app_config = loader.load_config()
    ConfigurationRegistry.set_config(app_config)
    app.state.config = ConfigurationRegistry.get_config()

    obs_config = ObservabilityConfig(
        service_name=settings.gateway.name,
        service_version=settings.gateway.version,
        logging=LoggingConfig(level=settings.logging.level),
        tracing=TracingConfig(enable=settings.tracing.enable, otlp_endpoint=settings.tracing.otlp_endpoint)
    )
    gateway_metrics.setup_gateway_observability(obs_config)
    FastAPIInstrumentor.instrument_app(app)
    logger.info("Configuration and observability initialized.")

    # 2. Initialize the Runtime Kernel
    kernel = RuntimeKernel()
    app.state.kernel = kernel
    logger.info("Runtime Kernel created.")

    # 3. Manually register the main GatewayRuntime
    # In the future, this could be replaced by an automatic discovery process.
    gateway_runtime_instance = GatewayRuntime(app)
    gateway_manifest = RuntimeManifest(
        id="gateway",
        name="Main Gateway Services",
        version="1.0"
    )
    # The registration method is synchronous in the current (mock) implementation
    kernel.registry.register(gateway_runtime_instance, gateway_manifest)
    logger.info(f"Runtime '{gateway_manifest.id}' registered.")

    # 4. Start the kernel, which will initialize and start all registered runtimes
    await kernel.startup()
    logger.info("Gateway startup complete.")


@app.on_event("shutdown")
async def shutdown_event():
    """Shuts down the application and the Runtime Kernel."""
    logger.info("Gateway shutting down...")
    if hasattr(app.state, 'kernel'):
        await app.state.kernel.shutdown()
    logger.info("Gateway shutdown complete.")


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "src.main:app", 
        host="127.0.0.1", 
        port=8000, 
        reload=True
    )