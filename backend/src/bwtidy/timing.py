"""Durations of the steps of a slow operation, for one INFO line. Never a name or a value."""

import time


class Timings:
    def __init__(self) -> None:
        self.steps: dict[str, float] = {}
        self._last = time.monotonic()

    def lap(self, step: str) -> None:
        """Charge the time since the last lap (or the start) to step."""
        now = time.monotonic()
        self.steps[step] = self.steps.get(step, 0.0) + now - self._last
        self._last = now

    def total(self) -> float:
        return sum(self.steps.values())

    def __str__(self) -> str:
        return ", ".join(f"{step} {seconds:.2f} s" for step, seconds in self.steps.items())
