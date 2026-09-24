from __future__ import annotations

from tools.v1.live.harness import (
    ARTIFACT_ROOT_ENV,
    MASTER_GATE_ENV,
    NETWORK_GATE_ENV,
    GUI_GATE_ENV,
    LiveCategory,
    LiveHarnessConfigError,
    LiveHarnessDisabled,
    ScenarioRunner,
    create_live_run_config,
)
from tools.v1.live.network_scenarios import (
    NETWORK_QUERY,
    build_network_scenario,
    run_network_live,
)


def _success(action, data):
    return {
        "ok": True,
        "tool": "web_tool",
        "action": action,
        "data": data,
        "error": None,
        "meta": {
            "version": "2.0.0",
            "truncated": False,
            "warnings": [],
        },
    }


def _failure(code, *, retryable=True):
    return {
        "ok": False,
        "tool": "web_tool",
        "action": "search",
        "data": None,
        "error": {
            "code": code,
            "message": "provider unavailable",
            "retryable": retryable,
            "details": {"stage": "request"},
        },
        "meta": {
            "version": "2.0.0",
            "truncated": False,
            "warnings": [],
        },
    }


def _config(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    return create_live_run_config(
        category=LiveCategory.NETWORK,
        repo_root=repo,
        env={
            MASTER_GATE_ENV: "1",
            NETWORK_GATE_ENV: "1",
            ARTIFACT_ROOT_ENV: str(tmp_path / "artifacts"),
        },
        run_id="network-unit",
    )


def test_network_scenario_uses_structured_search_url_then_scrape(tmp_path):
    config = _config(tmp_path)
    calls = []

    def web_run(*, action, **kwargs):
        calls.append((action, kwargs))
        if action == "search":
            return _success(
                "search",
                {
                    "query": NETWORK_QUERY,
                    "returned_count": 1,
                    "provider": "fake-search",
                    "results": [
                        {
                            "title": "Example Domain",
                            "url": "https://example.com/",
                            "snippet": "example",
                        }
                    ],
                },
            )
        assert action == "scrape"
        assert kwargs["url"] == "https://example.com/"
        content = "Example Domain live content"
        return _success(
            "scrape",
            {
                "requested_url": "https://example.com/",
                "final_url": "https://example.com/",
                "status_code": 200,
                "title": "Example Domain",
                "title_truncated": False,
                "content": content,
                "content_format": "markdown",
                "content_chars": len(content),
                "returned_chars": len(content),
                "content_sha256": "a" * 64,
                "method": "static",
                "content_type": "text/html",
                "structured_data": {"tables": [], "charts": []},
                "provenance": {},
            },
        )

    evidence = ScenarioRunner(config).run(
        (build_network_scenario(web_run=web_run),)
    )

    assert evidence["category"] == "NETWORK"
    assert evidence["status"] == "PASS"
    assert [step["status"] for step in evidence["scenarios"][0]["steps"]] == [
        "PASS",
        "PASS",
    ]
    assert calls[0][0] == "search"
    assert calls[1][0] == "scrape"
    assert calls[1][1]["url"] == "https://example.com/"


def test_network_empty_valid_search_is_distinct_failure(tmp_path):
    config = _config(tmp_path)

    def web_run(*, action, **kwargs):
        assert action == "search"
        return _success(
            "search",
            {
                "query": NETWORK_QUERY,
                "returned_count": 0,
                "provider": "fake-search",
                "results": [],
            },
        )

    evidence = ScenarioRunner(config).run(
        (build_network_scenario(web_run=web_run),)
    )

    step = evidence["scenarios"][0]["steps"][0]
    assert evidence["status"] == "FAIL"
    assert step["error"]["code"] == "LIVE_WEB_EMPTY_RESULT"
    assert step["error"]["details"]["live_classification"] == (
        "EMPTY_VALID_RESULT"
    )


def test_network_remote_unavailability_is_classified_without_local_effects(tmp_path):
    config = _config(tmp_path)

    scenario = build_network_scenario(
        web_run=lambda **kwargs: _failure("WEB_NETWORK_ERROR"),
    )
    evidence = ScenarioRunner(config).run((scenario,))

    step = evidence["scenarios"][0]["steps"][0]
    assert evidence["status"] == "FAIL"
    assert step["error"]["code"] == "WEB_NETWORK_ERROR"
    assert step["error"]["retryable"] is True
    assert step["error"]["details"]["live_classification"] == (
        "REMOTE_UNAVAILABLE"
    )
    assert evidence["cleanup"] == {
        "owned_pids": [],
        "terminated_pids": [],
        "still_alive_pids": [],
        "errors": [],
    }


def test_network_success_with_malformed_structured_data_fails_assertion(tmp_path):
    config = _config(tmp_path)

    def web_run(*, action, **kwargs):
        assert action == "search"
        return _success(
            "search",
            {
                "query": NETWORK_QUERY,
                "returned_count": 1,
                "provider": "fake-search",
                "results": [{"title": "missing url"}],
            },
        )

    evidence = ScenarioRunner(config).run(
        (build_network_scenario(web_run=web_run),)
    )
    step = evidence["scenarios"][0]["steps"][0]
    assert step["error"]["code"] == "LIVE_WEB_EMPTY_RESULT"


def test_network_entry_requires_master_and_network_before_artifacts(tmp_path):
    repo = tmp_path / "repo"

    for env in ({}, {MASTER_GATE_ENV: "1"}, {NETWORK_GATE_ENV: "1"}):
        try:
            run_network_live(env=env, repo_root=repo, web_run=lambda **kwargs: None)
        except LiveHarnessDisabled:
            pass
        else:
            raise AssertionError("missing literal NETWORK gates must fail closed")
        assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


def test_network_gate_does_not_require_gui_gate(tmp_path):
    config = _config(tmp_path)
    assert config.category is LiveCategory.NETWORK
    assert list(config.enabled_gates) == [
        MASTER_GATE_ENV,
        NETWORK_GATE_ENV,
    ]


def test_network_entry_rejects_gui_opt_in_before_artifacts(tmp_path):
    calls = []

    def forbidden_web(**kwargs):
        calls.append(kwargs)
        raise AssertionError("Web must not run when GUI gate is enabled")

    try:
        run_network_live(
            env={
                MASTER_GATE_ENV: "1",
                NETWORK_GATE_ENV: "1",
                GUI_GATE_ENV: "1",
            },
            repo_root=tmp_path / "repo",
            web_run=forbidden_web,
        )
    except LiveHarnessConfigError:
        pass
    else:
        raise AssertionError("NETWORK stage must reject simultaneous GUI opt-in")

    assert calls == []
    assert list(tmp_path.iterdir()) == []
