"""
AI Agency — Production worker loop.

Features ported from Segundo (ai-factory):
  - Thompson Sampling bandit routing (orchestrator.py pattern)
  - Ralph Gate confidence scoring (ralph_gate.py pattern)
  - Quality Gate tier escalation (quality_gate.py pattern)
  - 5-stage SOP pipeline with reviewer loop
  - Task decomposition for complex tasks (Devin <30min rule)
  - Per-task budget cap enforcement
  - BudgetEnforcer reserve/commit pattern
  - StuckDetector loop detection
  - Kill switch graceful shutdown
"""
import re
import time
import threading
import requests
from datetime import datetime, timezone
from typing import Optional
import random

from config import (
    SUPABASE_URL, SUPABASE_KEY, SOP_STAGES, DEPARTMENTS,
    MAX_TASK_BUDGET_USD, STUCK_TIMEOUT_SECONDS,
)
from litellm_gateway import call_llm, get_model_for_task
from budget import BudgetEnforcer, BudgetExhaustedError
from kill_switch import should_exit, install_signal_handlers
from stuck_detector import StuckDetector, run_watchdog_sweep
from learning import record_outcome, build_context_from_history

# ── Config (non-secret, read from env with defaults) ─────────────────────────
import os
POLL_INTERVAL   = int(os.environ.get("POLL_INTERVAL", "20"))
MAX_RETRIES     = int(os.environ.get("MAX_STAGE_RETRIES", "3"))
MAX_SUBTASKS    = int(os.environ.get("MAX_DECOMPOSED_SUBTASKS", "5"))

# Ralph Gate thresholds (ported from ai-factory/ralph_gate.py)
CONFIDENCE_THRESHOLD = 0.7
ACCEPT_THRESHOLD     = 0.6   # >= accept output
GOOD_THRESHOLD       = 0.8   # >= accept immediately, skip self-correct

DEFAULT_DEPT = DEPARTMENTS["coding"]

# ── Supabase helpers ───────────────────────────────────────────────────────────
SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

def sb_get(path: str) -> list:
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/{path}", headers=SB_HEADERS, timeout=10)
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []

def sb_post(table: str, data: dict) -> Optional[dict]:
    try:
        r = requests.post(f"{SUPABASE_URL}/rest/v1/{table}", headers=SB_HEADERS, json=data, timeout=10)
        result = r.json()
        if isinstance(result, list) and result:
            return result[0]
        return result if isinstance(result, dict) else None
    except Exception:
        return None

def sb_patch(table: str, row_id: str, data: dict) -> Optional[dict]:
    try:
        r = requests.patch(
            f"{SUPABASE_URL}/rest/v1/{table}?id=eq.{row_id}",
            headers=SB_HEADERS, json=data, timeout=10
        )
        result = r.json()
        if isinstance(result, list) and result:
            return result[0]
        return result if isinstance(result, dict) else None
    except Exception:
        return None

# ── Thompson Sampling Bandit (ported from ai-factory/orchestrator.py) ─────────
# Per-thread Random instance — avoids module-level state contention if concurrency added later
_thread_local = threading.local()
def _rng() -> random.Random:
    if not hasattr(_thread_local, "rng"):
        _thread_local.rng = random.Random()
    return _thread_local.rng

# Serialises read-modify-write bandit updates within this process.
# The worker runs single-threaded, so this is belt-and-suspenders for future safety.
_BANDIT_LOCK = threading.Lock()


def thompson_sample(successes: int, failures: int) -> float:
    """
    Draw a sample from Beta(alpha, beta) distribution.
    Higher successes -> higher expected value -> model gets picked more.
    Uses per-thread Random instance for thread safety.
    Ported from ai-factory/orchestrator.py (betavariate replaces manual Gamma approx).
    """
    alpha = successes + 1  # always >= 1 since successes >= 0
    beta  = failures  + 1
    return _rng().betavariate(alpha, beta)


def update_bandit(model: str, task_type: str, success: bool):
    """
    Atomic-safe update of Thompson bandit state in Supabase.
    Uses a process-level lock to serialise the read-modify-write cycle,
    preventing lost updates from concurrent calls within this process.
    """
    with _BANDIT_LOCK:
        try:
            rows = sb_get(
                f"agency_bandit_state?model=eq.{model}&task_type=eq.{task_type}&select=id,successes,failures"
            )
            if rows:
                row = rows[0]
                s = row.get("successes", 0) + (1 if success else 0)
                f = row.get("failures",  0) + (0 if success else 1)
                sb_patch("agency_bandit_state", row["id"], {
                    "successes": s, "failures": f,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                })
            else:
                sb_post("agency_bandit_state", {
                    "model": model,
                    "task_type": task_type,
                    "successes": 1 if success else 0,
                    "failures":  0 if success else 1,
                })
        except Exception:
            pass  # bandit table may not exist yet — degrade gracefully


def get_best_model(task_type: str, candidates: list[str]) -> str:
    """
    Use Thompson sampling to pick the best model for this task_type.
    Candidates with no history default to 50% (equal exploration).
    """
    if not candidates:
        return get_model_for_task(task_type)

    best_model = candidates[0]
    best_score = -1.0

    for model in candidates:
        rows = sb_get(
            f"agency_bandit_state?model=eq.{model}&task_type=eq.{task_type}&select=successes,failures"
        )
        if rows:
            row = rows[0]
            score = thompson_sample(row.get("successes", 0), row.get("failures", 0))
        else:
            score = thompson_sample(0, 0)  # 0.5 baseline, with exploration noise

        if score > best_score:
            best_score = score
            best_model = model

    return best_model

# ── Ralph Gate: Confidence Scoring (ported from ai-factory/ralph_gate.py) ─────

def _has_function_or_class(output: str) -> bool:
    patterns = [r'\bdef\s+\w+\s*\(', r'\bclass\s+\w+', r'\basync\s+def\s+\w+\s*\(']
    return any(re.search(p, output) for p in patterns)

def _has_error_keywords(output: str) -> bool:
    # Strip docstring "Raises:" sections to avoid false positives from documented exceptions
    clean = re.sub(r'raises?:\s*\n.*?(?=\n\s*\n|\Z)', '', output, flags=re.IGNORECASE | re.DOTALL)
    clean_lower = clean.lower()
    error_patterns = [
        r'traceback \(most recent',
        r'(?:syntax|type|name|key|index|value|import|runtime)error:',
        r'failed to \w+',
        r'unexpected token',
        r'compilation? (?:error|failed)',
        r'(?:^|\n)\s*error[:\s]',
    ]
    return any(re.search(p, clean_lower) for p in error_patterns)

def _calculate_intent_overlap(intent: str, output: str) -> float:
    intent_words = set(re.findall(r'\b\w+\b', intent.lower()))
    output_words = set(re.findall(r'\b\w+\b', output.lower()))
    shared = intent_words.intersection(output_words)
    return len(shared) / max(len(intent_words), 1) if intent_words else 1.0

def _has_citations(output: str) -> bool:
    lower = output.lower()
    return (bool(re.search(r'\[\d+\]', output)) or
            bool(re.search(r'\([^)]*,\s*\d{4}\)', output)) or
            any(kw in lower for kw in ['reference', 'citation', 'source', 'according to']))

def _has_specific_findings(output: str) -> bool:
    patterns = [r'\bfinding[s]?\b', r'\bissue[s]?\b', r'\bproblem[s]?\b',
                r'\brecommendation[s]?\b', r'\bvulnerabilit\w*\b']
    return any(re.search(p, output.lower()) for p in patterns)

def _has_actionable_items(output: str) -> bool:
    patterns = [r'\bstep\s+\d+\b', r'\baction:\s+', r'\bimplement\s+',
                r'\bfix\b', r'\benable\b', r'\bimprove\b']
    return any(re.search(p, output.lower()) for p in patterns)


def _output_has_structure(output: str) -> bool:
    """Check if output has multi-paragraph or list structure (good writing signal)."""
    return (output.count('\n') >= 3 or
            bool(re.search(r'^\s*[-*\u2022]\s', output, re.MULTILINE)) or
            bool(re.search(r'^\s*\d+[.)]\s', output, re.MULTILINE)))

def _output_is_substantial(output: str, min_chars: int = 150) -> float:
    """0-1 score for output length — partial credit for shorter outputs."""
    return min(1.0, len(output.strip()) / min_chars)


def evaluate_confidence(task_intent: str, output: str, task_type: str = "coding") -> float:
    """
    Evaluate confidence in AI output 0-1 based on task_type signals.
    Ported from ai-factory/ralph_gate.py — evaluate_confidence().

    Note: intent_overlap is only used for coding/research where the output
    domain vocabulary should match the task description. For writing/marketing/QA,
    structural quality signals are used instead — creative outputs inherently
    don't share vocabulary with the instruction that produced them.
    """
    if not output or not output.strip():
        return 0.0

    # Map task_type to confidence type via department config
    dept = DEPARTMENTS.get(task_type, DEFAULT_DEPT)
    # Departments no longer carry confidence_type; derive from task_type
    conf_type_map = {
        "coding": "coding", "research": "research", "qa": "review",
        "writing": "writing", "marketing": "writing",
    }
    conf_type = conf_type_map.get(task_type, "coding")

    if conf_type == "coding":
        score = (
            (0.35 if _has_function_or_class(output) else 0.0) +
            (0.0  if _has_error_keywords(output)    else 0.35) +
            0.30 * _calculate_intent_overlap(task_intent, output)
        )
    elif conf_type == "research":
        score = (
            0.35 * _output_is_substantial(output, 150) +
            (0.25 if _output_has_structure(output) else 0.10) +
            (0.20 if _has_citations(output) else 0.10) +
            (0.0  if _has_error_keywords(output) else 0.20)
        )
    elif conf_type == "review":
        # QA/review: structural signals only — output vocabulary won't match meta-instruction
        score = (
            0.30 * _output_is_substantial(output, 200) +
            (0.30 if _has_specific_findings(output) or _has_actionable_items(output) else 0.15) +
            (0.25 if _output_has_structure(output) else 0.0) +
            (0.0  if _has_error_keywords(output) else 0.15)
        )
    else:  # writing / marketing — structural quality, not intent overlap
        score = (
            0.35 * _output_is_substantial(output, 150) +
            (0.30 if _output_has_structure(output) else 0.10) +
            (0.0  if _has_error_keywords(output)   else 0.35)
        )

    return max(0.0, min(1.0, score))


# ── Quality Gate: tiered execute with escalation ───────────────────────────────

def execute_with_quality_gate(
    prompt: str,
    task_type: str,
    dept: dict,
    budget_remaining: float,
) -> dict:
    """
    Run execute stage through quality gate with self-correction and escalation.
    Uses call_llm for all LLM calls. Bandit selects initial model.

    Attempt 1 -> confidence score -> self-correct if 0.6-0.8 -> escalate if <0.6
    Score >=0.8 -> accept immediately.

    Returns: {success, output, cost_usd, confidence, model_used}
    """
    system = dept["system"]
    task_route = dept.get("route", "default")

    # Use bandit to pick best model from available routes
    default_model = get_model_for_task(task_type)
    model = get_best_model(task_type, [default_model])

    total_cost = 0.0
    current_prompt = prompt
    best_result = None
    best_confidence = 0.0

    # Up to 2 attempts: initial + self-correction or escalation
    for attempt in range(2):
        if total_cost >= budget_remaining:
            break

        result = call_llm(current_prompt, system=system, task_type=task_type, model_override=model)
        total_cost += result.get("cost_usd", 0)

        if not result["success"]:
            update_bandit(model, task_type, False)
            if not best_result:
                best_result = result
            break

        confidence = evaluate_confidence(prompt, result["output"], task_type)

        if confidence > best_confidence:
            best_confidence = confidence
            best_result = result

        # Excellent — accept immediately
        if confidence >= GOOD_THRESHOLD:
            update_bandit(model, task_type, True)
            break

        # Acceptable but not great — self-correct
        if ACCEPT_THRESHOLD <= confidence < GOOD_THRESHOLD and attempt == 0:
            update_bandit(model, task_type, confidence >= CONFIDENCE_THRESHOLD)
            current_prompt = (
                f"{prompt}\n\n"
                f"--- SELF-CORRECTION (confidence={confidence:.0%}) ---\n"
                f"Your output was partially acceptable but needs improvement. "
                f"Please fix and resubmit a complete, high-quality answer."
            )
            continue

        # Below threshold
        update_bandit(model, task_type, confidence >= CONFIDENCE_THRESHOLD)
        break

    if not best_result:
        return {"success": False, "output": "", "cost_usd": total_cost,
                "confidence": 0.0, "model_used": model}

    return {
        "success": best_confidence >= ACCEPT_THRESHOLD,
        "output":  best_result.get("output", ""),
        "cost_usd": total_cost,
        "confidence": best_confidence,
        "model_used": best_result.get("model", model),
    }


# ── Schema validation (deterministic, free) ───────────────────────────────────
def schema_validate(output: str, stage: str) -> tuple:
    if not output or not output.strip():
        return False, "empty output"
    if len(output.strip()) < 20:
        return False, f"too short ({len(output)} chars)"
    error_kws = ["i cannot", "i'm unable", "i don't have access", "i am unable"]
    if any(kw in output.lower() for kw in error_kws):
        return False, "output is refusal"
    return True, "ok"


# ── Task decomposition (Devin <30min pattern) ──────────────────────────────────
def should_decompose(task: dict) -> bool:
    prompt = task.get("prompt", "")
    task_type = task.get("task_type", "coding")
    if task_type in ("coding", "research") and len(prompt) > 500:
        return True
    multi_signals = ["and also", "additionally", "furthermore", "\n-", "\n*", "\n1."]
    if sum(1 for s in multi_signals if s in prompt.lower()) >= 2:
        return True
    return False

def decompose_task(task: dict) -> list:
    system = (
        "You are a technical project manager. Split the task into independent subtasks, "
        f"each completable in under 30 minutes. Max {MAX_SUBTASKS} subtasks. "
        "Respond with ONLY a JSON array: [{\"title\":\"...\",\"prompt\":\"...\",\"task_type\":\"...\"}]"
    )
    result = call_llm(
        f"Split this task:\n\nTitle: {task['title']}\nPrompt: {task.get('prompt','')}",
        system=system,
        task_type="default",
        max_tokens=1000,
    )
    if not result["success"]:
        return []
    import json
    try:
        match = re.search(r'\[.*\]', result["output"], re.DOTALL)
        if match:
            subtasks = json.loads(match.group())
            return [s for s in subtasks if isinstance(s, dict) and s.get("title") and s.get("prompt")][:MAX_SUBTASKS]
    except Exception:
        pass
    return []


# ── Stage prompts ──────────────────────────────────────────────────────────────
def stage_prompt(stage: str, title: str, prompt: str, prev_output: str = "") -> str:
    ctx = f"\n\nPrevious stage output:\n{prev_output[:2000]}" if prev_output else ""
    return {
        "requirements": f"Analyze requirements:\n\nTitle: {title}\nDescription: {prompt}\n\nOutput numbered requirements.",
        "plan":         f"Create implementation plan:\n\nTitle: {title}\nDescription: {prompt}{ctx}\n\nOutput numbered steps.",
        "execute":      f"Execute completely:\n\nTitle: {title}\nDescription: {prompt}{ctx}\n\nProduce complete deliverable.",
        "verify":       f"Verify and test:\n\nTitle: {title}{ctx}\n\nList issues, confirm PASS or FAIL.",
        "deliver":      f"Prepare final deliverable:\n\nTitle: {title}{ctx}\n\nFormat cleanly for the client.",
    }.get(stage, prompt)


# ── Core: process one SOP stage ────────────────────────────────────────────────
def process_stage(
    task_id: str, title: str, prompt: str, stage: str,
    dept: dict, task_type: str, prev_output: str, budget_remaining: float
) -> dict:
    """Process a single SOP stage. Execute stage uses Quality Gate; others use direct call."""
    sub_id = None
    try:
        sub = sb_post("agency_subtasks", {
            "parent_task_id": task_id, "stage": stage, "status": "in_progress",
        })
        if sub and sub.get("id"):
            sub_id = sub["id"]
    except Exception:
        pass

    p = stage_prompt(stage, title, prompt, prev_output)

    # Execute stage: full Quality Gate cascade + bandit routing
    if stage == "execute":
        gate_result = execute_with_quality_gate(p, task_type, dept, budget_remaining)
        output     = gate_result["output"]
        cost       = gate_result["cost_usd"]
        confidence = gate_result["confidence"]
        model_used = gate_result["model_used"]
        success    = gate_result["success"]
    else:
        # Non-execute stages: single call via call_llm
        result = call_llm(p, system=dept["system"], task_type=task_type)
        cost = result.get("cost_usd", 0)

        valid, reason = schema_validate(result.get("output", ""), stage)
        output = result.get("output", "") if valid else ""
        success = valid and result["success"]
        confidence = evaluate_confidence(prompt, output, task_type) if success else 0.0
        model_used = result.get("model", get_model_for_task(task_type))
        update_bandit(model_used, task_type, success)

    if sub_id:
        try:
            sb_patch("agency_subtasks", sub_id, {
                "status": "completed" if success else "failed",
                "output": output[:10000] if output else "",
                "worker": model_used,
                "cost_usd": round(cost, 5),
                "review_score": f"{confidence:.2f}",
                "completed_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass

    return {
        "success":    success,
        "output":     output,
        "cost_usd":   cost,
        "confidence": confidence,
        "model_used": model_used,
    }


# ── Process a full task ────────────────────────────────────────────────────────
def process_task(task: dict) -> str:
    task_id   = task["id"]
    title     = task.get("title", "Untitled")
    prompt    = task.get("prompt", task.get("description", title))
    task_type = task.get("task_type", "coding")

    dept = DEPARTMENTS.get(task_type, DEFAULT_DEPT)
    dept_name = dept["name"]

    budget_enforcer = BudgetEnforcer(float(task.get("budget_cap_usd") or MAX_TASK_BUDGET_USD))
    detector = StuckDetector()

    print(f"\n-> [{task_id[:8]}] {title} [{dept_name}] budget=${budget_enforcer.remaining:.2f}")

    if budget_enforcer.remaining <= 0:
        sb_patch("tasks", task_id, {"status": "failed", "result": {"error": "zero budget"}})
        return "failed"

    sb_patch("tasks", task_id, {
        "status": "in_progress",
        "department": dept_name,
        "model_used": get_model_for_task(task_type),
    })

    # Decompose complex tasks (Devin <30min rule)
    if should_decompose(task):
        print(f"  [decompose] splitting complex task...")
        subtasks = decompose_task(task)
        if subtasks:
            print(f"  [decompose] -> {len(subtasks)} subtasks queued")
            sub_budget = round(budget_enforcer.remaining / len(subtasks), 4)
            for st in subtasks:
                sb_post("tasks", {
                    "title": st["title"],
                    "prompt": st["prompt"],
                    "task_type": st.get("task_type", task_type),
                    "priority": task.get("priority", 5),
                    "status": "pending",
                    "parent_task_id": task_id,
                    "budget_cap_usd": sub_budget,
                })
            sb_patch("tasks", task_id, {
                "status": "in_progress",
                "result": {"decomposed": True, "subtask_count": len(subtasks)},
            })
            return "decomposed"

    # SOP pipeline
    all_passed = True
    prev_output = ""
    final_confidence = 0.0
    final_model = get_model_for_task(task_type)

    for stage in SOP_STAGES:
        # Budget check via enforcer
        try:
            budget_enforcer.check_budget()
        except BudgetExhaustedError as e:
            print(f"  [budget] {e}")
            all_passed = False
            break

        # Kill switch check
        if should_exit():
            print("  [kill] Shutdown requested")
            all_passed = False
            break

        # Enhance execute stage with learning from past successes
        if stage == "execute":
            history_ctx = build_context_from_history(SUPABASE_URL, SUPABASE_KEY, task_type)
            if history_ctx:
                prompt = f"{history_ctx}\n\n---\n\n{prompt}"

        print(f"  [{stage}]", end=" ", flush=True)
        stage_result = process_stage(
            task_id, title, prompt, stage, dept, task_type, prev_output, budget_enforcer.remaining
        )

        # Commit actual cost to budget enforcer
        budget_enforcer.commit(actual=stage_result.get("cost_usd", 0))

        # Feed stuck detector
        detector.record_action(stage)
        if stage_result["success"]:
            detector.record_observation(stage_result["output"][:200])
        else:
            detector.record_error(stage_result.get("error", stage_result.get("output", "failed")))

        # Check if stuck
        if detector.is_stuck():
            print(f"  [stuck] {detector.stuck_reason}")
            all_passed = False
            break

        if stage == "execute":
            # Always capture execute metrics regardless of success
            final_confidence = stage_result["confidence"]
            final_model = stage_result["model_used"]

        if stage_result["success"]:
            prev_output = stage_result["output"]
            conf_str = f"conf={stage_result['confidence']:.0%}" if stage_result["confidence"] else ""
            print(f"ok {conf_str}")
        else:
            all_passed = False
            print(f"FAIL")
            if stage == "execute":
                print(f"  execute failed -- marking for review")
                break

    # Record outcome for self-improving learning
    total_cost = budget_enforcer.spent
    record_outcome(
        SUPABASE_URL, SUPABASE_KEY,
        task_type, prompt[:500], final_model,
        final_confidence, total_cost, all_passed,
        prev_output[:500] if prev_output else "",
    )

    final_status = "completed" if all_passed else "review"
    sb_patch("tasks", task_id, {
        "status": final_status,
        "cost_usd": round(budget_enforcer.spent, 5),
        "result": {
            "department":       dept_name,
            "model":            final_model,
            "confidence_score": round(final_confidence, 3),
            "all_passed":       all_passed,
            "total_cost_usd":   round(budget_enforcer.spent, 5),
            "output_preview":   prev_output[:800] if prev_output else "",
        },
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })
    from discord_notify import notify_task_complete
    notify_task_complete(
        task_id=task_id, title=title, status=final_status,
        confidence=final_confidence, cost_usd=total_cost, department=dept_name,
    )

    print(f"  -> {final_status.upper()} conf={final_confidence:.0%} ${budget_enforcer.spent:.4f}")
    return final_status


# ── Main loop ──────────────────────────────────────────────────────────────────
def run_loop():
    install_signal_handlers()

    dept_summary = ", ".join(f"{k}={v.get('route', 'default')}" for k, v in DEPARTMENTS.items())
    print(f"[agency] Worker started -- poll={POLL_INTERVAL}s retries={MAX_RETRIES} budget=${MAX_TASK_BUDGET_USD}")
    print(f"[agency] Supabase: {SUPABASE_URL}")
    print(f"[agency] Departments: {dept_summary}")
    print(f"[agency] Features: Thompson sampling + Ralph Gate + LiteLLM gateway + BudgetEnforcer + StuckDetector")

    consecutive_errors = 0
    while True:
        if should_exit():
            print("[agency] Shutdown requested, exiting")
            break

        try:
            tasks = sb_get("tasks?status=eq.pending&order=priority.desc,created_at.asc&limit=3")
            if isinstance(tasks, list) and tasks:
                print(f"\n[agency] {len(tasks)} pending task(s)")
                for task in tasks:
                    if should_exit():
                        print("[agency] Shutdown requested, exiting")
                        break
                    process_task(task)
                consecutive_errors = 0

                # Watchdog sweep after processing batch
                run_watchdog_sweep(SUPABASE_URL, SUPABASE_KEY, STUCK_TIMEOUT_SECONDS)
            else:
                print(".", end="", flush=True)
                consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            print(f"\n[agency] error #{consecutive_errors}: {e}")
            if consecutive_errors >= 5:
                print("[agency] too many errors -- backing off 5 min")
                time.sleep(300)
                consecutive_errors = 0
                continue

        time.sleep(POLL_INTERVAL)


def start_background_loop():
    """Called from api.py startup event."""
    t = threading.Thread(target=run_loop, daemon=True, name="agency-worker")
    t.start()
    print(f"[agency] background thread started (tid={t.ident})")
    return t


if __name__ == "__main__":
    run_loop()
