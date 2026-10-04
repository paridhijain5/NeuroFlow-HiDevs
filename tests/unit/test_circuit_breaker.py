"""Unit tests for the circuit breaker state machine.

ADAPT: assumed API: CircuitBreaker(failure_threshold, recovery_timeout) with
.state ('closed'|'open'|'half_open'), .record_failure(), .record_success(),
.allow_request().
"""
import time

from backend.resilience.circuit_breaker import CircuitBreaker  # ADAPT


def _state(cb: CircuitBreaker) -> str:
    s = cb.state
    return str(getattr(s, "value", s)).lower()


def make() -> CircuitBreaker:
    return CircuitBreaker(failure_threshold=3, recovery_timeout=0.05)


def test_starts_closed() -> None:
    assert _state(make()) == "closed"


def test_stays_closed_below_threshold() -> None:
    cb = make()
    cb.record_failure()
    cb.record_failure()
    assert _state(cb) == "closed"


def test_opens_at_threshold() -> None:
    cb = make()
    for _ in range(3):
        cb.record_failure()
    assert _state(cb) == "open"


def test_open_blocks_requests() -> None:
    cb = make()
    for _ in range(3):
        cb.record_failure()
    assert cb.allow_request() is False


def test_half_open_after_timeout() -> None:
    cb = make()
    for _ in range(3):
        cb.record_failure()
    time.sleep(0.1)
    assert cb.allow_request() is True
    assert _state(cb) in {"half_open", "half-open", "halfopen"}


def test_success_in_half_open_closes() -> None:
    cb = make()
    for _ in range(3):
        cb.record_failure()
    time.sleep(0.1)
    cb.allow_request()
    cb.record_success()
    assert _state(cb) == "closed"


def test_failure_in_half_open_reopens() -> None:
    cb = make()
    for _ in range(3):
        cb.record_failure()
    time.sleep(0.1)
    cb.allow_request()
    cb.record_failure()
    assert _state(cb) == "open"


def test_success_resets_failure_count() -> None:
    cb = make()
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    assert _state(cb) == "closed"
