from fastapi import Request, Depends

from ...application.container import ApplicationContainer


def get_container(request: Request) -> ApplicationContainer:
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise RuntimeError("Application container has not been initialized.")
    return container


def get_runtime_kernel(container: ApplicationContainer = Depends(get_container)):
    return container.require("runtime_kernel")


def get_provider_runtime(container: ApplicationContainer = Depends(get_container)):
    return container.require("provider_runtime")


def get_session_runtime(container: ApplicationContainer = Depends(get_container)):
    return container.require("session_runtime")


def get_context_runtime(container: ApplicationContainer = Depends(get_container)):
    return container.require("context_runtime")


def get_capability_runtime(container: ApplicationContainer = Depends(get_container)):
    return container.require("capability_runtime")


def get_event_bus(container: ApplicationContainer = Depends(get_container)):
    return container.require("event_bus")


def get_http_client(container: ApplicationContainer = Depends(get_container)):
    return container.require("http_client")


def get_auth_manager(container: ApplicationContainer = Depends(get_container)):
    return container.require("auth_manager")

def get_oauth(container: ApplicationContainer = Depends(get_container)):
    return container.require("oauth")

def get_circuit_breaker_manager(container: ApplicationContainer = Depends(get_container)):
    return container.require("circuit_breaker_manager")

def get_config(container: ApplicationContainer = Depends(get_container)):
    return container.require("config")

def get_storage(container: ApplicationContainer = Depends(get_container)):
    return container.require("storage")

def get_uow_factory(container: ApplicationContainer = Depends(get_container)):
    return container.require("uow_factory")

def get_auth(container: ApplicationContainer = Depends(get_container)):
    return container.require("auth")

