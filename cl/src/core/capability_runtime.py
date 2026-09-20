from __future__ import annotations

from typing import Any, Dict


class CapabilityRegistrationError(RuntimeError):
    pass


class CapabilityRuntime:
    """
    Owns client-side capability advertisement and invocation dispatch.
    """

    def __init__(
        self,
        registry,
        realtime,
        *,
        client_id: str,
        owner_id: str,
        dispatcher,
    ) -> None:
        self.registry = registry
        self.realtime = realtime

        self.client_id = client_id
        self.owner_id = owner_id

        self.dispatcher = dispatcher

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def build_registration(self) -> Dict[str, Any]:
        capabilities = []

        for capability_id, tool in self.registry.tools.items():
            metadata = (
                tool.get("metadata", {})
                if isinstance(tool, dict)
                else {}
            )

            name = metadata.get(
                "name",
                capability_id,
            )

            description = metadata.get(
                "description",
                name,
            )

            parameters = metadata.get(
                "parameters",
                metadata.get(
                    "input_schema",
                    {
                        "type": "object",
                    },
                ),
            )

            output_schema = metadata.get(
                "output_schema",
                {},
            )

            capabilities.append(
                {
                    "definition": {
                        "id": capability_id,
                        "version": metadata.get(
                            "version",
                            "1.0",
                        ),
                        "name": name,
                        "description": description,
                        "parameters": parameters,
                        "output_schema": output_schema,
                        "source": "CLIENT",
                        "execution_kind": "PYTHON",
                        "kind": metadata.get("kind", "TOOL"),
                        "execution_mode": metadata.get("execution_mode", "ONE_SHOT"),
                        "idempotency": metadata.get(
                            "idempotency",
                            "UNKNOWN",
                        ),
                        "effects": metadata.get("effects", []),
                        "require_auth": metadata.get(
                            "require_auth",
                            False,
                        ),
                        "required_scopes": metadata.get(
                            "required_scopes",
                            [],
                        ),
                        "metadata": {
                            "client_id": self.client_id,
                        },
                    },
                    "kind": "TOOL",
                    "location": "CLIENT",
                    "driver_kind": "REMOTE_CLIENT",
                    "owner_type": "CLIENT",
                    "owner_id": self.owner_id,
                    "connection_id": self.realtime.connection_id,
                    "implementation_id": (
                        f"{self.realtime.connection_id}:"
                        f"{capability_id}"
                    ),
                    "metadata": {
                        "client_id": self.client_id,
                        "local_name": capability_id,
                    },
                }
            )

        return {
            "connection_id": self.realtime.connection_id,
            "client_id": self.client_id,
            "owner_id": self.owner_id,
            "capabilities": capabilities,
        }

    def register(
        self,
        *,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        if not self.realtime.is_registered:
            raise CapabilityRegistrationError(
                "Connection must be registered before capability.register."
            )

        payload = self.build_registration()
        self.dispatcher.update_registration_snapshot(
            item["definition"]["id"] for item in payload["capabilities"]
        )

        self.realtime.send(
            "capability.register",
            payload,
        )

        return self.realtime.wait_capabilities_registered(
            timeout
        )

    # ------------------------------------------------------------------
    # Inbound realtime
    # ------------------------------------------------------------------

    def handle_message(
        self,
        envelope: Dict[str, Any],
    ) -> bool:
        message_type = envelope.get(
            "type"
        )

        if message_type == "capability.invoke":
            self.dispatcher.dispatch(
                envelope
            )
            return True

        if message_type == "capability.cancel":
            invocation_id = envelope.get(
                "invocation_id"
            )

            if invocation_id:
                self.dispatcher.cancel(
                    invocation_id
                )

            return True

        if message_type == "capability.registered":
            self.realtime.mark_capabilities_registered(
                envelope
            )
            return True

        return False

    def shutdown(self) -> None:
        self.dispatcher.shutdown()
