""" per-call runtime state shared across the pipeline, idle handler, and end-call tool """
from dataclasses import dataclass, field


@dataclass
class CallSession:
    call_id: str
    end_reason: str = "disconnected"
    _sequence: int = field(default=0, init=False)

    def next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence
