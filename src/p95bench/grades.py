"""Checkpoint scores for v2; the legacy Grade remains unchanged."""

from dataclasses import asdict, dataclass
from decimal import Decimal
from math import isfinite


def finite(value, minimum=0):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and isfinite(value) and value >= minimum)


@dataclass(frozen=True)
class Checkpoint:
    id: str
    earned: float
    possible: float

    def __post_init__(self):
        if (not isinstance(self.id, str) or not self.id.strip()
                or not finite(self.possible, .001) or not finite(self.earned)
                or self.earned > self.possible):
            raise ValueError("Invalid checkpoint points or id")


@dataclass(frozen=True)
class CheckpointGrade:
    checkpoints: tuple[Checkpoint, ...]
    detail: str = ""

    def __post_init__(self):
        object.__setattr__(self, "checkpoints", tuple(self.checkpoints))
        if (not self.checkpoints
                or any(not isinstance(c, Checkpoint) for c in self.checkpoints)
                or len({c.id for c in self.checkpoints}) != len(self.checkpoints)
                or sum(Decimal(str(c.possible)) for c in self.checkpoints) != 100):
            raise ValueError("Unique checkpoints must sum to 100 possible points")

    @property
    def score(self):
        return float(sum(Decimal(str(c.earned)) for c in self.checkpoints) / 100)

    @property
    def passed(self):
        return self.score == 1

    def to_dict(self):
        return {"schema_version": 2, "status": "graded", "score": self.score,
                "points_earned": float(sum(Decimal(str(c.earned)) for c in self.checkpoints)),
                "max_points": 100,
                "passed": self.passed, "detail": self.detail,
                "checkpoints": [asdict(c) for c in self.checkpoints]}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, dict) or value.get("schema_version") != 2
                or value.get("status") != "graded"):
            raise ValueError("Expected a graded v2 checkpoint result")
        try:
            result = cls(tuple(Checkpoint(**c) for c in value["checkpoints"]),
                         value.get("detail", ""))
        except (KeyError, TypeError) as error:
            raise ValueError("Malformed checkpoint result") from error
        expected = result.to_dict()
        if any(value.get(k) != expected[k] for k in
               ("score", "points_earned", "max_points", "passed")):
            raise ValueError("Checkpoint aggregate mismatch")
        return result
