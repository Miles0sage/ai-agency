# budget.py
"""Reserve-commit budget enforcement. Ported from OpenHands Metrics pattern."""
import threading


class BudgetExhaustedError(Exception):
    def __init__(self, budget: float, spent: float):
        self.budget = budget
        self.spent = spent
        super().__init__(f"Budget exhausted: ${spent:.4f} spent of ${budget:.4f} limit")


class BudgetEnforcer:
    def __init__(self, total_budget_usd: float):
        self._total = total_budget_usd
        self._reserved = 0.0
        self._spent = 0.0
        self._lock = threading.Lock()

    def reserve(self, estimated_cost: float) -> bool:
        with self._lock:
            if self._spent + self._reserved + estimated_cost > self._total:
                return False
            self._reserved += estimated_cost
            return True

    def commit(self, actual: float, reservation: float = 0.0):
        with self._lock:
            self._reserved = max(0.0, self._reserved - reservation)
            self._spent += actual

    def check_budget(self):
        with self._lock:
            if self._spent >= self._total:
                raise BudgetExhaustedError(self._total, self._spent)

    @property
    def remaining(self) -> float:
        with self._lock:
            return self._total - self._spent - self._reserved

    @property
    def spent(self) -> float:
        with self._lock:
            return self._spent
