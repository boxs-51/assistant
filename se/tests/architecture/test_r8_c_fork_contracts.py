from __future__ import annotations

from dataclasses import fields, replace

from se.src.runtimes.agent.contracts.fork import (
    ForkPlan,
    ForkRuntimeSeed,
    ForkSideEffectSnapshot,
    fork_overlay_fingerprint,
    fork_plan_fingerprint,
    fork_runtime_seed_fingerprint,
    fork_side_effect_fingerprint,
    fork_transcript_fingerprint,
)
from se.src.runtimes.agent.contracts.inference import InferenceMessage


def _message(content="base"):
    return InferenceMessage(role="user", content={"text": content})


def _effect(revision=4, result_fingerprint="a" * 64):
    return ForkSideEffectSnapshot(
        invocation_id="inv-1",
        invocation_revision=revision,
        capability_id="tool.echo",
        capability_version="1",
        request_fingerprint="b" * 64,
        idempotency="IDEMPOTENT",
        state="COMPLETED",
        remote_outcome_state="TERMINAL_COMMITTED",
        tool_call_id="call-1",
        committed_result_fingerprint=result_fingerprint,
    )


def _plan(**updates):
    transcript = updates.pop("base_transcript", (_message(),))
    effects = updates.pop("side_effects", (_effect(),))
    overlay_messages = (
        {
            "role": "user",
            "content": "branch-local",
            "tool_calls": [],
            "name": None,
            "tool_call_id": None,
            "metadata": {},
        },
    )
    runtime_seed = ForkRuntimeSeed(
        version=1,
        request_id="request-1",
        workflow_id=None,
        metadata={"safe": True},
        causation_id=None,
        trace_id="trace-1",
        limits={"timeout_seconds": 60.0},
        request_fingerprint="r" * 64,
        remaining_active_budget_seconds=30.0,
        checkpoint_iteration=4,
        base_transcript_fingerprint=fork_transcript_fingerprint(transcript),
        side_effect_fingerprint=fork_side_effect_fingerprint(effects),
        branch_context_revision=0,
        overlay_fingerprint=fork_overlay_fingerprint(overlay_messages),
    )
    values = {
        "fork_request_id": "fork-request-a",
        "task_id": "task-1",
        "expected_task_revision": 3,
        "session_id": "session-1",
        "source_branch_id": "branch-1",
        "expected_branch_revision": 2,
        "source_execution_id": "exec-1",
        "expected_execution_revision": 7,
        "source_agent_id": "agent-1",
        "correlation_id": "corr-1",
        "source_checkpoint_id": "cp-7",
        "checkpoint_iteration": 4,
        "expected_task_budget_revision": 5,
        "budget_policy_fingerprint": "c" * 64,
        "base_transcript": transcript,
        "base_transcript_fingerprint": fork_transcript_fingerprint(transcript),
        "side_effects": effects,
        "side_effect_fingerprint": fork_side_effect_fingerprint(effects),
        "runtime_seed": runtime_seed,
        "runtime_seed_fingerprint": fork_runtime_seed_fingerprint(runtime_seed),
        "overlay_messages": overlay_messages,
        "target_user_id": "user-1",
    }
    values.update(updates)
    fingerprint = fork_plan_fingerprint(values)
    return ForkPlan(**values, plan_fingerprint=fingerprint)


def test_r8_c_fork_request_id_is_not_semantic_fingerprint_input():
    first = _plan(fork_request_id="fork-a")
    second = replace(first, fork_request_id="fork-b")
    assert fork_plan_fingerprint(first) == fork_plan_fingerprint(second)
    assert first.plan_fingerprint == fork_plan_fingerprint(second)


def test_r8_c_mapping_key_order_does_not_change_fingerprint():
    first = _plan()
    mapping = {
        item.name: getattr(first, item.name)
        for item in reversed(fields(ForkPlan))
        if item.name not in {"plan_fingerprint", "fork_request_id"}
    }
    assert fork_plan_fingerprint(first) == fork_plan_fingerprint(mapping)


def test_r8_c_semantic_revision_transcript_effect_and_overlay_changes_rehash():
    base = _plan()

    branch_revision = replace(base, expected_branch_revision=3)
    execution_revision = replace(base, expected_execution_revision=8)

    changed_transcript = (_message("changed"),)
    transcript_plan = replace(
        base,
        base_transcript=changed_transcript,
        base_transcript_fingerprint=fork_transcript_fingerprint(changed_transcript),
    )

    changed_effects = (_effect(revision=5),)
    effect_plan = replace(
        base,
        side_effects=changed_effects,
        side_effect_fingerprint=fork_side_effect_fingerprint(changed_effects),
    )

    overlay_plan = replace(
        base,
        overlay_messages=(
            {"role": "user", "content": "different", "tool_calls": [], "name": None, "tool_call_id": None, "metadata": {}},
        ),
    )

    task_revision = replace(base, expected_task_revision=4)
    checkpoint_plan = replace(base, source_checkpoint_id="cp-8")
    budget_plan = replace(base, expected_task_budget_revision=6)
    policy_plan = replace(base, budget_policy_fingerprint="d" * 64)

    changed_effect_result = (
        _effect(result_fingerprint="e" * 64),
    )
    effect_result_plan = replace(
        base,
        side_effects=changed_effect_result,
        side_effect_fingerprint=fork_side_effect_fingerprint(
            changed_effect_result
        ),
    )

    for changed in (
        task_revision,
        branch_revision,
        execution_revision,
        checkpoint_plan,
        transcript_plan,
        effect_plan,
        effect_result_plan,
        overlay_plan,
        budget_plan,
        policy_plan,
    ):
        assert fork_plan_fingerprint(changed) != base.plan_fingerprint


def test_r8_c_side_effect_and_transcript_fingerprints_are_deterministic():
    transcript = (
        InferenceMessage(role="assistant", content={"b": 2, "a": 1}),
    )
    effect = _effect()

    assert fork_transcript_fingerprint(transcript) == fork_transcript_fingerprint(
        (
            InferenceMessage(role="assistant", content={"a": 1, "b": 2}),
        )
    )
    assert fork_side_effect_fingerprint((effect,)) == fork_side_effect_fingerprint(
        (effect,)
    )
