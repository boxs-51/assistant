import math

import pytest

from cl.src.game_automation.actions.action import (
    ActionIntent,
    ActionKind,
    ActionOutcome,
    ActionStatus,
)


def test_valid_action_intent_and_outcome_are_local_and_bounded():
    intent = ActionIntent(
        action_id="a-1",
        automation_session_id="session-1",
        binding_generation=3,
        kind=ActionKind.KEY_HOLD,
        key="w",
        hold_seconds=0.25,
        deadline_monotonic=100.0,
        cooldown_key="move",
        cooldown_seconds=0.5,
    )
    outcome = ActionOutcome(
        action_id=intent.action_id,
        status=ActionStatus.SUCCEEDED,
        started_monotonic=10.0,
        completed_monotonic=10.25,
    )

    assert intent.kind is ActionKind.KEY_HOLD
    assert outcome.succeeded is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"action_id": ""},
        {"automation_session_id": ""},
        {"binding_generation": 0},
        {"deadline_monotonic": math.inf},
        {"deadline_monotonic": 0.0},
        {"cooldown_seconds": -1.0},
        {"cooldown_seconds": 1.0, "cooldown_key": None},
    ],
)
def test_action_intent_rejects_invalid_identity_deadline_or_timing(overrides):
    kwargs = dict(
        action_id="a",
        automation_session_id="s",
        binding_generation=1,
        kind=ActionKind.KEY_TAP,
        key="x",
    )
    kwargs.update(overrides)
    with pytest.raises(ValueError):
        ActionIntent(**kwargs)


def test_action_intent_rejects_unbounded_or_mismatched_input_shapes():
    with pytest.raises(ValueError, match="positive"):
        ActionIntent(
            action_id="hold",
            automation_session_id="s",
            binding_generation=1,
            kind=ActionKind.KEY_HOLD,
            key="w",
            hold_seconds=0.0,
        )

    with pytest.raises(ValueError, match="integer"):
        ActionIntent(
            action_id="move",
            automation_session_id="s",
            binding_generation=1,
            kind=ActionKind.MOUSE_MOVE,
            x=1.5,
            y=2,
        )

    with pytest.raises(ValueError, match="button"):
        ActionIntent(
            action_id="click",
            automation_session_id="s",
            binding_generation=1,
            kind=ActionKind.MOUSE_CLICK,
            x=1,
            y=2,
        )


def test_action_outcome_rejects_invalid_monotonic_order():
    with pytest.raises(ValueError, match="precede"):
        ActionOutcome(
            action_id="a",
            status=ActionStatus.FAILED,
            started_monotonic=2.0,
            completed_monotonic=1.0,
            failure_reason="failed",
        )
