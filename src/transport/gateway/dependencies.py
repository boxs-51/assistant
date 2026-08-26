from fastapi import Request

from ...application.container import ApplicationContainer


def get_container(request: Request) -> ApplicationContainer:
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise RuntimeError("Application container has not been initialized.")
    return container


def get_runtime_kernel(request: Request):
    return get_container(request).require("runtime_kernel")


def get_provider_runtime(request: Request):
    return get_container(request).require("provider_runtime")


def get_session_runtime(request: Request):
    return get_container(request).require("session_runtime")


def get_context_runtime(request: Request):
    return get_container(request).require("context_runtime")


def get_capability_runtime(request: Request):
    return get_container(request).require("capability_runtime")


def get_event_bus(request: Request):
    return get_container(request).require("event_bus")


def get_http_client(request: Request):
    return get_container(request).require("http_client")


def get_auth_manager(request: Request):
    return get_container(request).require("auth_manager")


def get_circuit_breaker_manager(request: Request):
    return get_container(request).require("circuit_breaker_manager")


def get_legacy_model_router(request: Request):
    return get_container(request).require("legacy_model_router")
