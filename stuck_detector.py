# stuck_detector.py
"""
StuckDetector — ported from OpenHands controller/stuck.py.
Detects loop patterns: identical action×4, error loops×3, empty output×3.
Also provides Supabase watchdog sweep for stuck tasks.
"""
import hashlib
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field


@dataclass
class StuckDetector:
    max_identical: int = 4
    max_error_repeat: int = 3
    max_empty_output: int = 3
    stuck_reason: str = ""
    _actions: list = field(default_factory=list)
    _observations: list = field(default_factory=list)
    _errors: list = field(default_factory=list)

    def _hash(self, text: str) -> str:
        return hashlib.md5(text.encode()).hexdigest()

    def record_action(self, action: str):
        self._actions.append(self._hash(action))

    def record_observation(self, observation: str):
        self._observations.append(self._hash(observation))
        self._errors.append(None)

    def record_error(self, error: str):
        self._observations.append(self._hash(error))
        self._errors.append(self._hash(error))

    def is_stuck(self) -> bool:
        self.stuck_reason = ""
        if len(self._actions) < 3:
            return False

        # Heuristic 1: identical action+observation repeated N times
        if len(self._actions) >= self.max_identical and len(self._observations) >= self.max_identical:
            recent_actions = self._actions[-self.max_identical:]
            recent_obs = self._observations[-self.max_identical:]
            if len(set(recent_actions)) == 1 and len(set(recent_obs)) == 1:
                self.stuck_reason = "identical_action_observation"
                return True

        # Heuristic 2: same action + error repeated N times
        if len(self._actions) >= self.max_error_repeat and len(self._errors) >= self.max_error_repeat:
            recent_actions = self._actions[-self.max_error_repeat:]
            recent_errors = self._errors[-self.max_error_repeat:]
            if (len(set(recent_actions)) == 1
                    and all(e is not None for e in recent_errors)
                    and len(set(recent_errors)) == 1):
                self.stuck_reason = "repeating_action_error"
                return True

        # Heuristic 3: empty output loop
        if len(self._observations) >= self.max_empty_output:
            empty_hash = self._hash("")
            recent_obs = self._observations[-self.max_empty_output:]
            recent_actions = self._actions[-self.max_empty_output:]
            if all(o == empty_hash for o in recent_obs) and len(set(recent_actions)) == 1:
                self.stuck_reason = "empty_output_loop"
                return True

        return False

    def reset(self):
        self._actions.clear()
        self._observations.clear()
        self._errors.clear()
        self.stuck_reason = ""


def run_watchdog_sweep(supabase_url: str, supabase_key: str, timeout_seconds: int = 180):
    """Sweep Supabase for tasks stuck in_progress with no subtask activity. Reset to failed.

    Accepts supabase_url and supabase_key params for backward compat,
    but uses the shared supabase_client internally.
    """
    from supabase_client import sb_get, sb_patch

    try:
        tasks = sb_get("tasks?status=eq.in_progress&select=id,title,updated_at")
        if not tasks:
            return []

        reset_tasks = []
        now = time.time()
        for task in tasks:
            updated = task.get("updated_at", "")
            if not updated:
                continue
            try:
                updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                age_seconds = now - updated_dt.timestamp()
            except (ValueError, TypeError):
                continue

            if age_seconds > timeout_seconds:
                task_id = task["id"]
                subtasks = sb_get(f"agency_subtasks?parent_task_id=eq.{task_id}&select=id&limit=1")
                if not subtasks:
                    sb_patch("tasks", task_id, {
                        "status": "failed",
                        "result": {"error": f"Watchdog: stuck {int(age_seconds)}s, 0 subtasks", "watchdog_reset": True},
                    })
                    reset_tasks.append(task_id)
                    print(f"[watchdog] Reset {task_id[:8]} ({task.get('title', '?')}) — {int(age_seconds)}s idle")
        return reset_tasks
    except Exception as e:
        print(f"[watchdog] Error: {e}")
        return []
