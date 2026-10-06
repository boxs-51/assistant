#!/usr/bin/env python3
"""Registration-only real ClientRuntime probe for AE-R13-C rollout evidence."""

from __future__ import annotations

import argparse
import sys
import time
from types import SimpleNamespace

from cl.src.core.client_runtime import ClientRuntime


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Exercise the real pending-ticket publication path with a "
            "zero-capability ClientRuntime generation."
        )
    )
    parser.add_argument(
        "--gateway-url",
        default="http://127.0.0.1:8000",
    )
    parser.add_argument(
        "--hold-seconds",
        type=float,
        default=8.0,
    )
    args = parser.parse_args()
    if args.hold_seconds < 0:
        parser.error("--hold-seconds must be >= 0")

    registry = SimpleNamespace(
        settings={"r13c_probe": True},
        tools={},
    )
    runtime = ClientRuntime(args.gateway_url, registry)
    try:
        runtime.start()
        if not runtime.ready:
            print("R13C_PROBE=HOLD reason=client_runtime_not_ready")
            return 2

        # Intentionally do not print client/user/session/connection/ticket IDs.
        print("R13C_PROBE=READY capabilities=0")
        time.sleep(args.hold_seconds)
        print(
            "R13C_PROBE=PENDING_COUNT "
            f"value={len(runtime.pending_resume_tickets)}"
        )
        return 0
    except Exception as exc:
        print(
            f"R13C_PROBE=HOLD error_type={type(exc).__name__}",
            file=sys.stderr,
        )
        return 2
    finally:
        try:
            runtime.stop()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
