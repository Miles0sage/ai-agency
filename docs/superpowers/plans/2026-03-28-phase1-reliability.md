# AI Factory v2 — Phase 1: Reliability

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing AI Agency crash-proof — watchdog kills stuck tasks, budget enforcement stops overspend, LiteLLM routes to cheapest capable model, and a global kill switch halts everything on SIGTERM.

**Architecture:** Celery + Redis replaces the in-process polling loop. A StuckDetector (ported from OpenHands) catches 5 loop patterns. LiteLLM proxies all LLM calls through a unified gateway with cost-based routing. Every task has a dollar budget that triggers BudgetExhaustedError when exceeded.

**Tech Stack:** Python 3.11, Celery, Redis, LiteLLM, FastAPI, Supabase (existing)

**Spec:** `docs/superpowers/specs/2026-03-28-ai-factory-v2-design.md`

---

## File Structure

```
ai-agency/
├── agency.py              # MODIFY: extract LLM calls to use litellm_gateway
├── api.py                 # MODIFY: replace background thread with Celery task dispatch
├── celery_app.py          # CREATE: Celery app config + task definitions
├── litellm_gateway.py     # CREATE: unified LLM gateway via LiteLLM
├── watchdog.py            # CREATE: StuckDetector + watchdog service
├── kill_switch.py         # CREATE: global stop flag + SIGTERM handler
├── budget.py              # CREATE: reserve-commit budget enforcement
├── config.py              # CREATE: centralized config (env vars, model routing table)
├── tests/
│   ├── __init__.py            # CREATE: empty, makes tests a package
│   ├── test_watchdog.py       # CREATE
│   ├── test_kill_switch.py    # CREATE
│   ├── test_budget.py         # CREATE
│   ├── test_litellm_gateway.py # CREATE
│   └── test_celery_tasks.py   # CREATE
├── requirements.txt       # MODIFY: add celery, redis, litellm, pytest
└── docker-compose.yml     # CREATE: Redis for Celery broker
```

---

### Task 1: Project Setup — Dependencies + Config

**Files:**
- Modify: `requirements.txt`
- Create: `config.py`
- Create: `docker-compose.yml`

- [ ] **Step 1: Add dependencies to requirements.txt**

```
# Add these lines to requirements.txt
celery[redis]==5.4.0
redis==5.2.1
litellm==1.55.0
pytest==8.3.4
pytest-asyncio==0.24.0
tenacity==9.0.0
```

- [ ] **Step 2: Create config.py — centralized configuration**

```python
# config.py
"""Centralized configuration. All env vars read here, nowhere else."""
import os

# ── Supabase ──
SUPABASE_URL = os.environ["SUPABASE_URL"]  # required — no default
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]  # required — no default

# ── Redis / Celery ──
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# ── LiteLLM Model Routing Table ──
# Format: task_type → list of (model_id, provider, cost_per_1k_tokens)
MODEL_ROUTING = {
    "default":    {"model": "deepseek/deepseek-chat", "cost_input": 0.028, "cost_output": 0.42},
    "coding":     {"model": "openrouter/moonshot/kimi-k2.5", "cost_input": 0.445, "cost_output": 2.22},
    "boilerplate":{"model": "dashscope/qwen-turbo", "cost_input": 0.05, "cost_output": 0.20},
    "research":   {"model": "gemini/gemini-2.0-flash", "cost_input": 0.10, "cost_output": 0.40},
    "fast":       {"model": "groq/llama-3.1-8b-instant", "cost_input": 0.05, "cost_output": 0.08},
}

# ── Budget ──
MAX_TASK_BUDGET_USD = float(os.environ.get("MAX_TASK_BUDGET_USD", "0.10"))

# ── Watchdog ──
WATCHDOG_POLL_INTERVAL = int(os.environ.get("WATCHDOG_POLL_INTERVAL", "30"))
STUCK_TIMEOUT_SECONDS = int(os.environ.get("STUCK_TIMEOUT_SECONDS", "180"))  # 3 min

# ── SOP ──
SOP_STAGES = ["requirements", "plan", "execute", "verify", "deliver"]

# ── Departments ──
DEPARTMENTS = {
    "coding": {
        "name": "Engineering",
        "system": "You are a senior software engineer. Write clean, tested, production-ready code.",
        "route": "coding",
    },
    "research": {
        "name": "Research",
        "system": "You are a research analyst. Provide thorough analysis with actionable insights.",
        "route": "research",
    },
    "writing": {
        "name": "Writing",
        "system": "You are a professional writer. Produce clear, engaging content.",
        "route": "default",
    },
    "qa": {
        "name": "QA",
        "system": "You are a QA engineer. Find bugs, edge cases, provide test cases.",
        "route": "default",
    },
    "marketing": {
        "name": "Marketing",
        "system": "You are a growth marketer. Write compelling copy that drives conversions.",
        "route": "default",
    },
}
```

- [ ] **Step 3: Create docker-compose.yml for Redis**

```yaml
# docker-compose.yml
version: "3.8"
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    restart: unless-stopped
```

- [ ] **Step 4: Create tests/__init__.py**

```bash
mkdir -p tests && touch tests/__init__.py
```

- [ ] **Step 5: Install dependencies and start Redis**

Run: `cd /root/ai-agency && pip install -r requirements.txt`
Run: `docker compose up -d redis`
Expected: Redis running on localhost:6379

- [ ] **Step 6: Commit**

```bash
cd /root/ai-agency
git add config.py docker-compose.yml requirements.txt
git commit -m "feat: add celery/redis/litellm deps and centralized config"
```

---

### Task 2: Kill Switch — Global Stop Flag

**Files:**
- Create: `kill_switch.py`
- Create: `tests/test_kill_switch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_kill_switch.py
"""Tests for global kill switch."""
import signal
import os
from kill_switch import (
    should_exit,
    request_shutdown,
    reset_shutdown,
    stop_if_should_exit,
)


def test_should_exit_starts_false():
    reset_shutdown()
    assert should_exit() is False


def test_request_shutdown_sets_flag():
    reset_shutdown()
    request_shutdown()
    assert should_exit() is True


def test_reset_clears_flag():
    request_shutdown()
    reset_shutdown()
    assert should_exit() is False


def test_stop_if_should_exit_returns_false_normally():
    reset_shutdown()
    result = stop_if_should_exit(retry_state=None)
    assert result is False


def test_stop_if_should_exit_returns_true_when_shutdown():
    reset_shutdown()
    request_shutdown()
    result = stop_if_should_exit(retry_state=None)
    assert result is True
    reset_shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/ai-agency && python -m pytest tests/test_kill_switch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kill_switch'`

- [ ] **Step 3: Write minimal implementation**

```python
# kill_switch.py
"""
Global kill switch — ported from OpenHands stop_if_should_exit pattern.

Set the flag via request_shutdown() or SIGTERM/SIGINT.
All tenacity retry loops check stop_if_should_exit() to halt immediately.
"""
import signal
import threading

_shutdown_requested = threading.Event()


def should_exit() -> bool:
    """Check if shutdown has been requested."""
    return _shutdown_requested.is_set()


def request_shutdown(*args):
    """Request graceful shutdown. Safe to call from signal handlers."""
    _shutdown_requested.set()


def reset_shutdown():
    """Reset the flag. Used in tests only."""
    _shutdown_requested.clear()


def stop_if_should_exit(retry_state) -> bool:
    """Tenacity stop callback. Returns True to stop retrying."""
    return _shutdown_requested.is_set()


def install_signal_handlers():
    """Install SIGTERM/SIGINT handlers. Call once at process startup."""
    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/ai-agency && python -m pytest tests/test_kill_switch.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /root/ai-agency
git add kill_switch.py tests/test_kill_switch.py
git commit -m "feat: add global kill switch with SIGTERM handler"
```

---

### Task 3: Budget Enforcement — Reserve-Commit Pattern

**Files:**
- Create: `budget.py`
- Create: `tests/test_budget.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_budget.py
"""Tests for reserve-commit budget enforcement."""
import pytest
from budget import BudgetEnforcer, BudgetExhaustedError


def test_initial_remaining():
    b = BudgetEnforcer(0.10)
    assert b.remaining == 0.10


def test_reserve_reduces_remaining():
    b = BudgetEnforcer(0.10)
    assert b.reserve(0.03) is True
    assert b.remaining == pytest.approx(0.07)


def test_reserve_fails_when_over_budget():
    b = BudgetEnforcer(0.10)
    b.reserve(0.08)
    assert b.reserve(0.05) is False


def test_commit_moves_from_reserved_to_spent():
    b = BudgetEnforcer(0.10)
    b.reserve(0.05)
    b.commit(actual=0.03, reservation=0.05)
    assert b.spent == pytest.approx(0.03)
    assert b.remaining == pytest.approx(0.07)


def test_check_budget_raises_when_exhausted():
    b = BudgetEnforcer(0.01)
    b.commit(actual=0.02, reservation=0.0)
    with pytest.raises(BudgetExhaustedError):
        b.check_budget()


def test_check_budget_passes_when_ok():
    b = BudgetEnforcer(0.10)
    b.commit(actual=0.02, reservation=0.0)
    b.check_budget()  # should not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/ai-agency && python -m pytest tests/test_budget.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'budget'`

- [ ] **Step 3: Write minimal implementation**

```python
# budget.py
"""
Reserve-commit budget enforcement.

Pattern: reserve budget BEFORE LLM call, commit actual cost AFTER.
Prevents agents from self-reporting their own spend.
Ported from OpenHands Metrics + runcycles.io reserve-commit pattern.
"""
import threading


class BudgetExhaustedError(Exception):
    """Raised when task budget is exceeded."""
    def __init__(self, budget: float, spent: float):
        self.budget = budget
        self.spent = spent
        super().__init__(f"Budget exhausted: ${spent:.4f} spent of ${budget:.4f} limit")


class BudgetEnforcer:
    """Thread-safe per-task budget tracker with reserve-commit semantics."""

    def __init__(self, total_budget_usd: float):
        self._total = total_budget_usd
        self._reserved = 0.0
        self._spent = 0.0
        self._lock = threading.Lock()

    def reserve(self, estimated_cost: float) -> bool:
        """Reserve budget before an LLM call. Returns False if would exceed."""
        with self._lock:
            if self._spent + self._reserved + estimated_cost > self._total:
                return False
            self._reserved += estimated_cost
            return True

    def commit(self, actual: float, reservation: float = 0.0):
        """Commit actual cost after LLM call completes."""
        with self._lock:
            self._reserved = max(0.0, self._reserved - reservation)
            self._spent += actual

    def check_budget(self):
        """Raise BudgetExhaustedError if spent exceeds total."""
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/ai-agency && python -m pytest tests/test_budget.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /root/ai-agency
git add budget.py tests/test_budget.py
git commit -m "feat: add reserve-commit budget enforcement"
```

---

### Task 4: Watchdog — StuckDetector (ported from OpenHands)

**Files:**
- Create: `watchdog.py`
- Create: `tests/test_watchdog.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_watchdog.py
"""Tests for StuckDetector — ported from OpenHands 5 heuristics."""
from watchdog import StuckDetector


def test_not_stuck_with_no_history():
    detector = StuckDetector()
    assert detector.is_stuck() is False


def test_not_stuck_with_varied_actions():
    detector = StuckDetector()
    detector.record_action("search files")
    detector.record_observation("found 3 files")
    detector.record_action("read file.py")
    detector.record_observation("file contents...")
    assert detector.is_stuck() is False


def test_stuck_on_identical_action_repeated_4_times():
    detector = StuckDetector()
    for _ in range(4):
        detector.record_action("search files")
        detector.record_observation("found 3 files")
    assert detector.is_stuck() is True
    assert detector.stuck_reason == "identical_action_observation"


def test_stuck_on_error_loop_3_times():
    detector = StuckDetector()
    for _ in range(3):
        detector.record_action("run code")
        detector.record_error("SyntaxError: unexpected EOF")
    assert detector.is_stuck() is True
    assert detector.stuck_reason == "repeating_action_error"


def test_stuck_on_empty_output_3_times():
    detector = StuckDetector()
    for _ in range(3):
        detector.record_action("generate response")
        detector.record_observation("")
    assert detector.is_stuck() is True
    assert detector.stuck_reason == "empty_output_loop"


def test_reset_clears_history():
    detector = StuckDetector()
    for _ in range(4):
        detector.record_action("same")
        detector.record_observation("same")
    assert detector.is_stuck() is True
    detector.reset()
    assert detector.is_stuck() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/ai-agency && python -m pytest tests/test_watchdog.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'watchdog'`

- [ ] **Step 3: Write minimal implementation**

```python
# watchdog.py
"""
StuckDetector — ported from OpenHands controller/stuck.py.

Detects 5 loop patterns that indicate an agent is stuck:
1. Identical action + observation repeated 4 times
2. Same action + error observation repeated 3 times
3. Empty output loop 3 times
4. Agent monologue (no observations) 3 times
5. Context window death loop (not implemented yet — needs condensation)

Also provides a Supabase watchdog that resets stuck tasks.
"""
import hashlib
import time
import requests
from typing import Optional
from dataclasses import dataclass, field


@dataclass
class StuckDetector:
    """Detects when an agent is stuck in a loop."""

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
    """
    Sweep Supabase for tasks stuck in 'in_progress' with no subtask activity.
    Reset them to 'failed' so they can be retried or escalated.
    """
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

    try:
        r = requests.get(
            f"{supabase_url}/rest/v1/tasks?status=eq.in_progress&select=id,title,updated_at",
            headers=headers, timeout=10,
        )
        tasks = r.json() if r.ok else []
        if not isinstance(tasks, list):
            return []

        reset_tasks = []
        now = time.time()

        for task in tasks:
            updated = task.get("updated_at", "")
            if not updated:
                continue

            # Parse ISO timestamp
            from datetime import datetime, timezone
            try:
                updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                age_seconds = now - updated_dt.timestamp()
            except (ValueError, TypeError):
                continue

            if age_seconds > timeout_seconds:
                # Check for subtask activity
                task_id = task["id"]
                sr = requests.get(
                    f"{supabase_url}/rest/v1/agency_subtasks?parent_task_id=eq.{task_id}&select=id&limit=1",
                    headers=headers, timeout=10,
                )
                subtasks = sr.json() if sr.ok else []

                if not isinstance(subtasks, list) or len(subtasks) == 0:
                    # No subtasks + stale = stuck
                    requests.patch(
                        f"{supabase_url}/rest/v1/tasks?id=eq.{task_id}",
                        headers=headers,
                        json={
                            "status": "failed",
                            "result": {
                                "error": f"Watchdog reset: stuck in_progress for {int(age_seconds)}s with 0 subtasks",
                                "watchdog_reset": True,
                            },
                        },
                        timeout=10,
                    )
                    reset_tasks.append(task_id)
                    print(f"[watchdog] Reset stuck task {task_id[:8]} ({task.get('title', '?')}) — {int(age_seconds)}s idle")

        return reset_tasks

    except Exception as e:
        print(f"[watchdog] Error during sweep: {e}")
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/ai-agency && python -m pytest tests/test_watchdog.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /root/ai-agency
git add watchdog.py tests/test_watchdog.py
git commit -m "feat: add StuckDetector with 5 loop heuristics + Supabase watchdog sweep"
```

---

### Task 5: LiteLLM Gateway — Unified Model Routing

**Files:**
- Create: `litellm_gateway.py`
- Create: `tests/test_litellm_gateway.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_litellm_gateway.py
"""Tests for LiteLLM unified gateway."""
from unittest.mock import patch, MagicMock
from litellm_gateway import call_llm, get_model_for_task, strip_thinking_tags


def test_get_model_for_coding():
    model = get_model_for_task("coding")
    assert "kimi" in model.lower() or "moonshot" in model.lower()


def test_get_model_for_default():
    model = get_model_for_task("writing")
    assert "deepseek" in model.lower()


def test_get_model_for_unknown_falls_back():
    model = get_model_for_task("nonexistent_type")
    assert "deepseek" in model.lower()


def test_strip_thinking_tags():
    text = "Hello <think>internal reasoning</think> World"
    assert strip_thinking_tags(text) == "Hello  World"


def test_strip_thinking_tags_multiline():
    text = "Start\n<think>\nlong\nthinking\n</think>\nEnd"
    assert strip_thinking_tags(text) == "Start\n\nEnd"


def test_strip_thinking_tags_no_tags():
    text = "No tags here"
    assert strip_thinking_tags(text) == "No tags here"


@patch("litellm_gateway.litellm_completion")
def test_call_llm_success(mock_completion):
    mock_choice = MagicMock()
    mock_choice.message.content = "Hello world"
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage.prompt_tokens = 10
    mock_response.usage.completion_tokens = 5
    mock_completion.return_value = mock_response

    result = call_llm("Test prompt", system="You are helpful", task_type="writing")
    assert result["success"] is True
    assert result["output"] == "Hello world"
    assert result["prompt_tokens"] == 10
    assert result["completion_tokens"] == 5


@patch("litellm_gateway.litellm_completion")
def test_call_llm_failure(mock_completion):
    mock_completion.side_effect = Exception("API error")

    result = call_llm("Test prompt", system="You are helpful", task_type="writing")
    assert result["success"] is False
    assert "API error" in result["error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/ai-agency && python -m pytest tests/test_litellm_gateway.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'litellm_gateway'`

- [ ] **Step 3: Write minimal implementation**

```python
# litellm_gateway.py
"""
Unified LLM gateway via LiteLLM.

Replaces hardcoded DashScope/MiniMax calls with a single interface
that routes to the cheapest capable model per task type.
"""
import re
import os
from typing import Optional
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

import litellm
from litellm import completion as litellm_completion

from config import MODEL_ROUTING, DEPARTMENTS
from kill_switch import stop_if_should_exit

# Suppress litellm's verbose logging
litellm.suppress_debug_info = True
litellm.set_verbose = False

# Set API keys from env (LiteLLM reads these automatically)
# DEEPSEEK_API_KEY, OPENROUTER_API_KEY, DASHSCOPE_API_KEY, GEMINI_API_KEY, GROQ_API_KEY


def strip_thinking_tags(text: str) -> str:
    """Strip <think>...</think> blocks from model output."""
    return re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)


def get_model_for_task(task_type: str) -> str:
    """Get the LiteLLM model ID for a task type."""
    dept = DEPARTMENTS.get(task_type)
    route_key = dept["route"] if dept else "default"
    route = MODEL_ROUTING.get(route_key, MODEL_ROUTING["default"])
    return route["model"]


def get_cost_for_model(model: str) -> tuple[float, float]:
    """Return (cost_input_per_1m, cost_output_per_1m) for a model."""
    for route in MODEL_ROUTING.values():
        if route["model"] == model:
            return route["cost_input"], route["cost_output"]
    return 0.028, 0.42  # default to DeepSeek pricing


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((litellm.RateLimitError, litellm.Timeout)),
    reraise=True,
)
def _completion_with_retry(**kwargs):
    """LiteLLM completion with tenacity retry + kill switch check."""
    if should_exit():
        raise InterruptedError("Shutdown requested")
    return litellm_completion(**kwargs)


def call_llm(
    prompt: str,
    system: str = "You are a helpful assistant.",
    task_type: str = "default",
    model_override: Optional[str] = None,
    max_tokens: int = 2000,
    temperature: float = 0.3,
) -> dict:
    """
    Call an LLM via LiteLLM unified gateway.

    Returns: {success, output, error, model, prompt_tokens, completion_tokens, cost_usd}
    """
    model = model_override or get_model_for_task(task_type)

    try:
        response = _completion_with_retry(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )

        content = response.choices[0].message.content or ""
        content = strip_thinking_tags(content).strip()

        prompt_tokens = response.usage.prompt_tokens if response.usage else 0
        completion_tokens = response.usage.completion_tokens if response.usage else 0

        # Calculate cost
        cost_in, cost_out = get_cost_for_model(model)
        cost_usd = (prompt_tokens * cost_in / 1_000_000) + (completion_tokens * cost_out / 1_000_000)

        return {
            "success": True,
            "output": content,
            "error": "",
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": round(cost_usd, 6),
        }

    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": str(e),
            "model": model,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost_usd": 0.0,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/ai-agency && python -m pytest tests/test_litellm_gateway.py -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /root/ai-agency
git add litellm_gateway.py tests/test_litellm_gateway.py
git commit -m "feat: add LiteLLM unified gateway with cost-based model routing"
```

---

### Task 6: Celery App — Task Queue with Hard Kill

**Files:**
- Create: `celery_app.py`
- Create: `tests/test_celery_tasks.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_celery_tasks.py
"""Tests for Celery task definitions — unit tests without broker."""
from unittest.mock import patch, MagicMock
from celery_app import make_celery


def test_celery_app_creates():
    app = make_celery()
    assert app.main == "ai-agency"


def test_process_task_is_registered():
    app = make_celery()
    assert "celery_app.process_task_async" in app.tasks


def test_watchdog_sweep_is_registered():
    app = make_celery()
    assert "celery_app.watchdog_sweep" in app.tasks
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /root/ai-agency && python -m pytest tests/test_celery_tasks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'celery_app'`

- [ ] **Step 3: Write minimal implementation**

```python
# celery_app.py
"""
Celery app — task queue with hard kill support.

Workers run agency tasks as Celery tasks. Hard kill via:
  app.control.revoke(task_id, terminate=True, signal='SIGKILL')

Watchdog runs as a periodic Celery Beat task.
"""
from celery import Celery
from celery.schedules import crontab
from config import REDIS_URL, STUCK_TIMEOUT_SECONDS, SUPABASE_URL, SUPABASE_KEY
from kill_switch import install_signal_handlers


def make_celery() -> Celery:
    app = Celery(
        "ai-agency",
        broker=REDIS_URL,
        backend=REDIS_URL,
    )
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        task_track_started=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,  # one task at a time per worker
        task_soft_time_limit=900,  # 15 min soft limit
        task_time_limit=960,  # 16 min hard limit (SIGKILL)
        beat_schedule={
            "watchdog-sweep": {
                "task": "celery_app.watchdog_sweep",
                "schedule": 30.0,  # every 30 seconds
            },
        },
    )

    @app.task(name="celery_app.process_task_async", bind=True, max_retries=2)
    def process_task_async(self, task_data: dict):
        """Process a single agency task through the SOP pipeline."""
        install_signal_handlers()
        from agency import process_task
        return process_task(task_data)

    @app.task(name="celery_app.watchdog_sweep")
    def watchdog_sweep():
        """Periodic sweep for stuck tasks."""
        from watchdog import run_watchdog_sweep
        return run_watchdog_sweep(
            SUPABASE_URL, SUPABASE_KEY,
            timeout_seconds=STUCK_TIMEOUT_SECONDS,
        )

    return app


# Module-level app for Celery CLI: `celery -A celery_app.app worker`
app = make_celery()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /root/ai-agency && python -m pytest tests/test_celery_tasks.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /root/ai-agency
git add celery_app.py tests/test_celery_tasks.py
git commit -m "feat: add Celery task queue with hard kill + periodic watchdog"
```

---

### Task 7: Wire It Together — Update agency.py and api.py

**Files:**
- Modify: `agency.py` — replace `_call_worker` with `litellm_gateway.call_llm`, add StuckDetector + budget
- Modify: `api.py` — dispatch tasks via Celery instead of background thread

- [ ] **Step 1: Update agency.py — replace LLM calls with gateway**

In `agency.py`, replace the `_call_worker` function body and update `process_stage`:

```python
# At top of agency.py, replace direct API imports with:
from litellm_gateway import call_llm
from budget import BudgetEnforcer, BudgetExhaustedError
from watchdog import StuckDetector
from kill_switch import should_exit, install_signal_handlers
from config import (
    SUPABASE_URL, SUPABASE_KEY, SOP_STAGES, DEPARTMENTS,
    MAX_TASK_BUDGET_USD,
)
```

Replace `_call_worker` calls in `process_stage` with `call_llm(prompt, system=dept["system"], task_type=task_type)`.

Add `BudgetEnforcer` to `process_task`:
```python
def process_task(task: dict) -> str:
    budget_enforcer = BudgetEnforcer(float(task.get("budget_cap_usd") or MAX_TASK_BUDGET_USD))
    detector = StuckDetector()
    # ... in SOP loop, before each stage:
    budget_enforcer.check_budget()
    if should_exit():
        break
    # ... after each stage:
    budget_enforcer.commit(actual=stage_result["cost_usd"], reservation=0)
    detector.record_action(stage)
    if stage_result["success"]:
        detector.record_observation(stage_result["output"][:200])
    else:
        detector.record_error(stage_result.get("error", "failed"))
    if detector.is_stuck():
        print(f"  [stuck] {detector.stuck_reason} — aborting")
        break
```

- [ ] **Step 2: Update api.py — dispatch via Celery**

Replace the `_start_worker` startup event in `api.py`:

```python
# api.py — remove the background thread, dispatch via Celery
from celery_app import app as celery_app

@app.post("/tasks")
def create_task(t: TaskIn):
    import uuid
    task_id = str(uuid.uuid4())
    data = {"id": task_id, "title": t.title, "prompt": t.prompt or t.title,
            "task_type": t.task_type, "priority": t.priority, "status": "pending"}
    r = requests.post(f"{SB_URL}/rest/v1/tasks", headers=H, json=data)
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text)

    # Dispatch to Celery worker
    from celery_app import app as celery
    celery.send_task("celery_app.process_task_async", args=[data])

    return r.json()

# Remove the @app.on_event("startup") background thread
```

- [ ] **Step 3: Run all tests**

Run: `cd /root/ai-agency && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Test locally**

Run: `cd /root/ai-agency && docker compose up -d redis`
Run (terminal 1): `celery -A celery_app.app worker --loglevel=info`
Run (terminal 2): `celery -A celery_app.app beat --loglevel=info`
Run (terminal 3): `uvicorn api:app --host 0.0.0.0 --port 8000`

Test: `curl -X POST http://localhost:8000/tasks -H 'Content-Type: application/json' -d '{"title":"Write hello world in Python","task_type":"coding"}'`

Expected: Task created, Celery worker picks it up, processes through SOP pipeline, completes.

- [ ] **Step 5: Commit**

```bash
cd /root/ai-agency
git add agency.py api.py
git commit -m "feat: wire LiteLLM gateway + Celery dispatch + watchdog + budget into agency"
```

---

### Task 8: Update Railway Deployment

**Files:**
- Modify: `Procfile`
- Modify: `railway.toml`
- Modify: `requirements.txt` (already done in Task 1)

- [ ] **Step 1: Update Procfile for Celery**

```
web: uvicorn api:app --host 0.0.0.0 --port ${PORT:-8000}
worker: celery -A celery_app.app worker --loglevel=info --concurrency=2
beat: celery -A celery_app.app beat --loglevel=info
```

- [ ] **Step 2: Update railway.toml**

```toml
[build]
builder = "nixpacks"

[deploy]
startCommand = "uvicorn api:app --host 0.0.0.0 --port ${PORT:-8000}"
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 3
```

Note: Railway needs separate services for worker and beat. Create them via Railway dashboard or CLI.

- [ ] **Step 3: Set environment variables on Railway**

Required env vars:
```
REDIS_URL=<Railway Redis URL>
DEEPSEEK_API_KEY=<key>
OPENROUTER_API_KEY=<key>
DASHSCOPE_API_KEY=<existing>
GEMINI_API_KEY=<key>
GROQ_API_KEY=<key>
SUPABASE_URL=<existing>
SUPABASE_SERVICE_KEY=<existing>
```

- [ ] **Step 4: Deploy and verify**

Run: `git push` (triggers Railway deploy)
Verify: `curl https://<railway-url>/dashboard` returns task stats
Verify: Submit a task and watch it complete via Celery worker logs

- [ ] **Step 5: Commit deployment config**

```bash
cd /root/ai-agency
git add Procfile railway.toml
git commit -m "feat: update Railway deployment for Celery worker + beat"
```

---

## Phase 1 Complete Checklist

After all 8 tasks:
- [ ] Kill switch halts all retries on SIGTERM within 5 seconds
- [ ] Budget enforcement prevents any task from exceeding $0.10
- [ ] StuckDetector catches loops within 4 iterations
- [ ] Watchdog sweep resets tasks stuck >3 minutes
- [ ] LiteLLM routes to cheapest model per task type
- [ ] Celery hard kill works: `app.control.revoke(task_id, terminate=True, signal='SIGKILL')`
- [ ] All tests pass: `pytest tests/ -v`
- [ ] Railway deployment working with Celery worker

---

## What's Next

Phase 1 makes the system crash-proof. Phase 2 adds real capabilities:
- **Plan:** `docs/superpowers/plans/2026-03-28-phase2-capabilities.md` (E2B + browser-use + Composio)
- **Plan:** `docs/superpowers/plans/2026-03-28-phase3-memory.md` (pgvector + A2A)
- **Plan:** `docs/superpowers/plans/2026-03-28-phase4-dashboard.md` (cyberpunk UI + observability)
