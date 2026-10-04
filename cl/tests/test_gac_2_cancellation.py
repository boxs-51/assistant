import threading
import time

from cl.src.game_automation.actions.cancellation import (
    CancellationToken,
    EmergencyStopLatch,
    WaitResult,
    WakeSignal,
    wait_interruptibly,
)


def _run_wait(result, token, emergency):
    result.append(
        wait_interruptibly(
            5.0,
            token,
            emergency,
        )
    )


def test_cancellation_wakes_in_progress_wait_promptly():
    signal = WakeSignal()
    token = CancellationToken(signal)
    emergency = EmergencyStopLatch(signal)
    result = []
    thread = threading.Thread(
        target=_run_wait,
        args=(result, token, emergency),
        daemon=True,
    )

    thread.start()
    time.sleep(0.02)
    assert token.cancel() is True
    thread.join(timeout=0.5)

    assert thread.is_alive() is False
    assert result == [WaitResult.CANCELLED]


def test_emergency_stop_wakes_wait_and_is_latched_without_reset():
    signal = WakeSignal()
    token = CancellationToken(signal)
    emergency = EmergencyStopLatch(signal)
    result = []
    thread = threading.Thread(
        target=_run_wait,
        args=(result, token, emergency),
        daemon=True,
    )

    thread.start()
    time.sleep(0.02)
    assert emergency.trigger() is True
    assert emergency.trigger() is False
    thread.join(timeout=0.5)

    assert thread.is_alive() is False
    assert result == [WaitResult.EMERGENCY_STOPPED]
    assert emergency.is_triggered is True
    assert not hasattr(emergency, "reset")


def test_future_action_token_is_independent_of_previous_cancellation():
    signal = WakeSignal()
    first = CancellationToken(signal)
    emergency = EmergencyStopLatch(signal)
    assert first.cancel() is True

    second = CancellationToken(signal)
    assert second.is_cancelled is False
    assert (
        wait_interruptibly(0.0, second, emergency)
        is WaitResult.COMPLETED
    )
