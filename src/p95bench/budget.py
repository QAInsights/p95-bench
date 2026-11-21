"""Persistent, shared v2 budget reservations, including uncertain retry charges."""

import fcntl
import json
from contextlib import contextmanager
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from uuid import uuid4

from p95bench.packs import finite
from p95bench.providers import SpendLimitExceeded

DEFAULT_LEDGER = Path.home() / ".local/state/p95-bench/p95-v2.0.0-budget.json"


def micros(value):
    if not finite(value):
        raise ValueError("Invalid budget amount")
    return int((Decimal(str(value)) * 1_000_000).to_integral_value(rounding=ROUND_CEILING))


class BudgetLedger:
    def __init__(self, path=DEFAULT_LEDGER, limit=60):
        self.path = Path(path)
        self.limit = micros(limit)
        if not 0 < self.limit <= 60_000_000:
            raise ValueError("V2 shared spend cap must be positive and at most $60")

    @contextmanager
    def transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = (json.loads(self.path.read_text()) if self.path.exists()
                    else {"limit": self.limit, "charges": {}})
            if (data.get("limit") != self.limit or not isinstance(data.get("charges"), dict)
                    or any(not isinstance(v, int) or v < 0 for v in data["charges"].values())):
                raise ValueError("Invalid or mismatched shared budget ledger")
            yield data
            temporary = self.path.with_suffix(".pending")
            temporary.write_text(json.dumps(data) + "\n")
            temporary.replace(self.path)

    def reserve(self, maximum):
        amount, key = micros(maximum), uuid4().hex
        with self.transaction() as data:
            if sum(data["charges"].values()) + amount > self.limit:
                raise SpendLimitExceeded("V2 shared spend cap: insufficient reserved budget")
            data["charges"][key] = amount
        return key

    def settle(self, key, actual_upper_bound):
        amount = micros(actual_upper_bound)
        with self.transaction() as data:
            reserved = data["charges"][key]
            data["charges"][key] = amount
        if amount > reserved:
            raise ValueError("Usage exceeds conservative reservation; stop and audit pricing")
