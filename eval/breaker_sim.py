"""Simulate the breaker against the runtime's one-shot admission policy.

Counts how many times a model in an outage is called across a run of tasks,
with and without the circuit breaker. The simulation drives the REAL
``CircuitBreaker`` (with an injected clock) and mirrors the runtime's policy:
per candidate, up to ``max_retries + 1`` attempts for transient failures, then
reroute to the next ranked candidate. No providers, no network, no spend —
the same contract as eval/depth_route.py and its siblings.

Policy notes, all derived from the code (not assumed):
- The runtime checks the breaker once PER CANDIDATE, not per attempt: the task
  that OPENS the breaker still pays its full retry budget, and a post-cooldown
  probe task also pays its full budget before the next failure reopens the
  breaker. The breaker's saving is exactly the tasks whose candidate is skipped.
- Tasks are instant, so the run's wall time is (T - 1) * gap.
- The healthy model answers immediately on its first call.

Usage: python -m eval.breaker_sim  (module tree; prints the table)
"""

from __future__ import annotations

from volante.breaker import CircuitBreaker

T0 = 1_000_000.0


def calls_during_task(
    breaker: CircuitBreaker,
    max_retries: int,
    now: float,
) -> int:
    """Provider calls one task pays against a broken model behind a healthy one."""
    if breaker.is_open("broken", now=now):
        return 0  # skipped at the candidate gate: no call at all
    calls = 0
    for _ in range(max_retries + 1):
        calls += 1  # attempt a transient failure
        breaker.record_failure("broken", now=now, kind="timeout")
    return calls


def sim(
    tasks: int,
    gap: float,
    max_retries: int = 2,
    threshold: int = 2,
    cooldown: float = 60.0,
) -> tuple[int, int]:
    """(calls without breaker, calls with breaker) for an outage that never clears."""
    without = tasks * (max_retries + 1)

    with_breaker = CircuitBreaker(threshold=threshold, cooldown_seconds=cooldown)
    calls_with = 0
    for i in range(tasks):
        calls_with += calls_during_task(with_breaker, max_retries, T0 + i * gap)
    return without, calls_with


def main() -> None:
    rows = [
        ("10 tasks, 30s apart, max_retries=2", sim(10, 30.0)),
        ("100 tasks, 10s apart, max_retries=2", sim(100, 10.0)),
        ("100 tasks, 5s apart, max_retries=0", sim(100, 5.0, max_retries=0)),
    ]
    print(f"{'scenario':<38}{'no breaker':>11}{'breaker':>9}{'avoided':>9}")
    for label, (without, with_b) in rows:
        print(f"{label:<38}{without:>11}{with_b:>9}{100 - 100 * with_b // without:>7}%")


if __name__ == "__main__":
    main()
