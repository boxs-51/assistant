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
