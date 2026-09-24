from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from tools.v1 import web_tool
from tools.v1.live.harness import (
    GUI_GATE_ENV,
    LiveCategory,
    LiveHarnessConfigError,
    LiveHarnessDisabled,
    Scenario,
    ScenarioContext,
    ScenarioRunner,
    ScenarioStep,
    create_live_run_config,
    validate_tool_result,
    write_evidence,
)


NETWORK_QUERY = "IANA example domain"
REMOTE_UNAVAILABLE_CODES = {
    "DEPENDENCY_UNAVAILABLE",
    "WEB_TIMEOUT",
    "WEB_NETWORK_ERROR",
    "WEB_SEARCH_PROVIDER_FAILED",
    "WEB_PROXY_UNAVAILABLE",
    "WEB_HTTP_ERROR",
    "WEB_CHALLENGE_REQUIRED",
    "WEB_BROWSER_REQUEST_BLOCKED",
    "WEB_REDIRECT_BLOCKED",
    "WEB_REDIRECT_LIMIT",
    "WEB_CLEANUP_FAILED",
}


def _failure(
    *,
    action: str,
    code: str,
    classification: str,
    retryable: bool,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(details or {})
    payload["live_classification"] = classification
    return {
        "ok": False,
        "tool": "web_tool",
        "action": action,
        "data": None,
        "error": {
            "code": code,
            "message": "Network live scenario did not produce usable structured evidence.",
            "retryable": retryable,
            "details": payload,
        },
        "meta": {
            "version": "live-harness",
            "truncated": False,
            "warnings": [],
        },
    }


def _annotate_web_failure(result: Mapping[str, Any]) -> Mapping[str, Any]:
    validated = validate_tool_result(result)
    if validated["ok"] is True:
        return validated
    error = validated["error"]
    assert isinstance(error, Mapping)
    details = error.get("details")
    classification = (
        "REMOTE_UNAVAILABLE"
        if error.get("code") in REMOTE_UNAVAILABLE_CODES
        or error.get("retryable") is True
        else "TOOL_CONTRACT_FAILURE"
    )
    merged_details = dict(details) if isinstance(details, Mapping) else {}
    merged_details["live_classification"] = classification
    return {
        **validated,
        "error": {
            **error,
            "details": merged_details,
        },
    }


def _public_http_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return value


def _same_requested_url(actual: Any, expected: str) -> bool:
    if not isinstance(actual, str):
        return False
    left = urlsplit(actual)
    right = urlsplit(expected)
    return (
        left.scheme.lower() == right.scheme.lower()
        and left.netloc.lower() == right.netloc.lower()
        and (left.path or "/") == (right.path or "/")
        and left.query == right.query
    )


def build_network_scenario(*, web_run=web_tool.run) -> Scenario:
    def search(context: ScenarioContext) -> Mapping[str, Any]:
        result = _annotate_web_failure(
            web_run(
                action="search",
                query=NETWORK_QUERY,
                max_results=5,
                timeout=15,
            )
        )
        if result["ok"] is not True:
            return result

        data = result.get("data")
        if not isinstance(data, Mapping):
            return result
        results = data.get("results")
        if not isinstance(results, list):
            return result

        returned_count = data.get("returned_count")
        provider = data.get("provider")
        query = data.get("query")

        # EMPTY_VALID_RESULT is reserved for an exact, structurally valid
        # empty success. Any non-empty/count-inconsistent/malformed success
        # remains a successful ToolResult and therefore must fail later via
        # the scenario validator, never be rewritten as harmless emptiness.
        if (
            query == NETWORK_QUERY
            and isinstance(provider, str)
            and bool(provider)
            and returned_count == 0
            and results == []
        ):
            return _failure(
                action="search",
                code="LIVE_WEB_EMPTY_RESULT",
                classification="EMPTY_VALID_RESULT",
                retryable=False,
                details={"returned_count": 0},
            )

        if (
            type(returned_count) is int
            and returned_count == len(results)
            and returned_count > 0
        ):
            urls = [
                url
                for item in results
                if isinstance(item, Mapping)
                for url in [_public_http_url(item.get("url"))]
                if url is not None
            ]
            if urls:
                context.state["discovered_url"] = urls[0]

        return result

    def validate_search(result: Mapping[str, Any]) -> bool:
        data = result.get("data")
        if not isinstance(data, Mapping):
            return False
        results = data.get("results")
        returned_count = data.get("returned_count")
        provider = data.get("provider")
        return (
            data.get("query") == NETWORK_QUERY
            and type(returned_count) is int
            and returned_count > 0
            and isinstance(provider, str)
            and bool(provider)
            and isinstance(results, list)
            and any(
                isinstance(item, Mapping)
                and _public_http_url(item.get("url")) is not None
                and isinstance(item.get("title"), str)
                and bool(item.get("title"))
                for item in results
            )
        )

    def scrape(context: ScenarioContext) -> Mapping[str, Any]:
        discovered = context.state["discovered_url"]
        result = web_run(
            action="scrape",
            url=discovered,
            force_js=False,
            timeout=20,
            max_chars=12000,
            max_retries=2,
            clean_noise=True,
            deduplicate=True,
        )
        return _annotate_web_failure(result)

    def validate_scrape(result: Mapping[str, Any]) -> bool:
        data = result.get("data")
        discovered = result_context_url[0]
        if not isinstance(data, Mapping) or discovered is None:
            return False
        content = data.get("content")
        final_url = _public_http_url(data.get("final_url"))
        return (
            _same_requested_url(data.get("requested_url"), discovered)
            and final_url is not None
            and type(data.get("status_code")) is int
            and 200 <= data["status_code"] < 400
            and isinstance(content, str)
            and bool(content.strip())
            and type(data.get("returned_chars")) is int
            and data["returned_chars"] == len(content)
            and isinstance(data.get("content_sha256"), str)
            and len(data["content_sha256"]) == 64
            and data.get("content_format") == "markdown"
            and data.get("method") in {"static", "dynamic"}
        )

    # The validator signature has no context parameter. Capture the discovered
    # URL in a tiny scenario-local cell during the scrape execute step.
    result_context_url: list[str | None] = [None]
    original_scrape = scrape

    def scrape_with_capture(context: ScenarioContext) -> Mapping[str, Any]:
        result_context_url[0] = context.state.get("discovered_url")
        return original_scrape(context)

    return Scenario(
        id="network-web-search-scrape",
        category=LiveCategory.NETWORK,
        steps=(
            ScenarioStep(
                id="web-search",
                tool="web_tool",
                action="search",
                summary="Discover a public URL from structured Web search results.",
                execute=search,
                validate=validate_search,
            ),
            ScenarioStep(
                id="web-scrape",
                tool="web_tool",
                action="scrape",
                summary="Read one discovered public URL through structured Web data.",
                execute=scrape_with_capture,
                validate=validate_scrape,
            ),
        ),
    )


def run_network_live(
    *,
    env: Mapping[str, str] | None = None,
    repo_root: str | Path | None = None,
    web_run=web_tool.run,
) -> tuple[dict[str, Any], Path]:
    effective_env = os.environ if env is None else env
    # T10-D is NETWORK-only. A simultaneous GUI opt-in would widen the
    # operator authority beyond this stage, so reject it before artifact
    # allocation or any Web call.
    if effective_env.get(GUI_GATE_ENV) == "1":
        raise LiveHarnessConfigError(
            "TV1-T10-D requires RUN_TOOLS_V1_LIVE_GUI to remain disabled"
        )

    root = (
        Path(__file__).resolve().parents[3]
        if repo_root is None
        else Path(repo_root)
    )

    # NETWORK config requires master + network literal gates before artifact
    # allocation. No GUI capability is requested by this category.
    config = create_live_run_config(
        category=LiveCategory.NETWORK,
        repo_root=root,
        env=effective_env,
    )
    evidence = ScenarioRunner(config).run(
        (build_network_scenario(web_run=web_run),)
    )
    evidence_path = write_evidence(
        evidence,
        config.artifact_directory,
    )
    return evidence, evidence_path


def main() -> int:
    try:
        evidence, path = run_network_live()
    except LiveHarnessDisabled as exc:
        print(f"SKIP: {exc}")
        return 2
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1

    print(f"{evidence['status']}: NETWORK evidence written to {path}")
    return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
