"""
AI Agency — Production worker loop.

Features ported from Segundo (ai-factory):
  - Thompson Sampling bandit routing (orchestrator.py pattern)
  - Ralph Gate confidence scoring (ralph_gate.py pattern)
  - Quality Gate tier escalation (quality_gate.py pattern)
  - 5-stage SOP pipeline with reviewer loop
  - Task decomposition for complex tasks (Devin <30min rule)
  - Per-task budget cap enforcement
"""
import re
import time
import os
import threading
import requests
from datetime import datetime, timezone
from typing import Optional
import random
import math

# ── Config ─────────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://upximucxncuajnakylyf.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVweGltdWN4bmN1YWpuYWt5bHlmIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2NjcyODUxOCwiZXhwIjoyMDgyMzA0NTE4fQ.VlzvndBY3Bs77zm8ZazERiFBlay2AEzqSpLGHu5BEaM"))

DASHSCOPE_KEY = os.environ.get("DASHSCOPE_API_KEY", "sk-sp-7424af93156c47fb94a524398af5f43e")
DASHSCOPE_URL = "https://coding-intl.dashscope.aliyuncs.com/v1/chat/completions"

POLL_INTERVAL   = int(os.environ.get("POLL_INTERVAL", "20"))
MAX_RETRIES     = int(os.environ.get("MAX_STAGE_RETRIES", "3"))
MAX_TASK_BUDGET = float(os.environ.get("MAX_TASK_BUDGET_USD", "0.10"))
MAX_SUBTASKS    = int(os.environ.get("MAX_DECOMPOSED_SUBTASKS", "5"))

# Ralph Gate thresholds (ported from ai-factory/ralph_gate.py)
CONFIDENCE_THRESHOLD = 0.7
ACCEPT_THRESHOLD     = 0.6   # >= accept output
GOOD_THRESHOLD       = 0.8   # >= accept immediately, skip self-correct

SOP_STAGES = ["requirements", "plan", "execute", "verify", "deliver"]

# ── Quality Gate Tiers (ported from ai-factory/quality_gate.py) ───────────────
# Tier 1: cheap fast, Tier 2: balanced, Tier 3: best quality
TIERS = {
    1: {"model": "qwen-turbo",      "cost": 0.0002},
    2: {"model": "qwen-plus",       "cost": 0.0005},
    3: {"model": "qwen-coder-plus", "cost": 0.0008},
}

# Which tiers to use per task_type (start_tier, max_tier)
TIER_RANGES = {
    "coding":    (1, 3),
    "research":  (1, 2),
    "writing":   (1, 2),
    "qa":        (1, 3),
    "marketing": (1, 1),
}

# ── Departments ───────────────────────────────────────────────────────────────
DEPARTMENTS = {
    "coding": {
        "name": "Engineering",
        "default_model": "qwen-coder-plus",
        "system": "You are a senior software engineer. Write clean, tested, production-ready code with error handling and type hints.",
        "confidence_type": "coding",
    },
    "research": {
        "name": "Research",
        "default_model": "qwen-plus",
        "system": "You are a research analyst. Provide thorough, well-structured analysis with clear conclusions and actionable insights.",
        "confidence_type": "research",
    },
    "writing": {
        "name": "Writing",
        "default_model": "qwen-turbo",
        "system": "You are a professional content writer. Produce clear, engaging, well-structured content tailored to the audience.",
        "confidence_type": "writing",
    },
    "qa": {
        "name": "QA",
        "default_model": "qwen-plus",
        "system": "You are a QA engineer. Find bugs, edge cases, quality issues. Provide specific, actionable test cases and verification steps.",
        "confidence_type": "review",
    },
    "marketing": {
        "name": "Marketing",
        "default_model": "qwen-turbo",
        "system": "You are a growth marketer. Write compelling copy and strategies that drive conversions and engagement.",
        "confidence_type": "writing",
    },
}
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

def thompson_sample(successes: int, failures: int) -> float:
    """
    Draw a sample from Beta(alpha, beta) distribution.
    Higher successes → higher expected value → model gets picked more.
    Ported from ai-factory/orchestrator.py.
    """
    alpha = max(successes + 1, 1)
    beta  = max(failures  + 1, 1)
    # Use Python's random.betavariate for Beta distribution sampling
    return random.betavariate(alpha, beta)


def update_bandit(model: str, task_type: str, success: bool):
    """
    Update Thompson bandit state in Supabase after a task outcome.
    Upserts agency_bandit_state row for (model, task_type).
    """
    try:
        # Fetch current state
        rows = sb_get(
            f"agency_bandit_state?model=eq.{model}&task_type=eq.{task_type}&select=id,successes,failures"
        )
        if rows:
            row = rows[0]
            row_id = row["id"]
            s = row.get("successes", 0) + (1 if success else 0)
            f = row.get("failures", 0)  + (0 if success else 1)
            sb_patch("agency_bandit_state", row_id, {"successes": s, "failures": f})
        else:
            sb_post("agency_bandit_state", {
                "model": model,
                "task_type": task_type,
                "successes": 1 if success else 0,
                "failures":  0 if success else 1,
            })
    except Exception:
        pass  # bandit table may not exist yet — graceful degradation


def get_best_model(task_type: str, candidates: list[str]) -> str:
    """
    Use Thompson sampling to pick the best model for this task_type.
    Candidates with no history default to 50% (equal exploration).
    """
    if not candidates:
        return DEPARTMENTS.get(task_type, DEFAULT_DEPT)["default_model"]

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
    error_patterns = [
        r'traceback \(most recent',
        r'(?:syntax|type|name|key|index|value|import|runtime)error:',
        r'failed to \w+',
        r'unexpected token',
        r'compilation? (?:error|failed)',
        r'(?:^|\n)\s*error[:\s]',
    ]
    output_lower = output.lower()
    return any(re.search(p, output_lower) for p in error_patterns)

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
                r'\brecommendation[s]?\b', r'\bvulnerabilit']
    return any(re.search(p, output.lower()) for p in patterns)

def _has_actionable_items(output: str) -> bool:
    patterns = [r'\bstep\s+\d+\b', r'\baction:\s+', r'\bimplement\s+',
                r'\bfix\b', r'\benable\b', r'\bimprove\b']
    return any(re.search(p, output.lower()) for p in patterns)


def evaluate_confidence(task_intent: str, output: str, task_type: str = "coding") -> float:
    """
    Evaluate confidence in AI output 0-1 based on task_type signals.
    Ported from ai-factory/ralph_gate.py — evaluate_confidence().
    """
    if not output or not output.strip():
        return 0.0

    conf_type = DEPARTMENTS.get(task_type, DEFAULT_DEPT).get("confidence_type", "coding")

    if conf_type == "coding":
        score = (
            (0.35 if _has_function_or_class(output) else 0.0) +
            (0.0  if _has_error_keywords(output)    else 0.35) +
            0.30 * _calculate_intent_overlap(task_intent, output)
        )
    elif conf_type == "research":
        score = (
            (0.30 if len(output) > 200 else len(output) / 200 * 0.30) +
            (0.30 if _has_citations(output) else 0.0) +
            0.40 * _calculate_intent_overlap(task_intent, output)
        )
    elif conf_type == "review":
        score = (
            (0.30 if _has_specific_findings(output) else 0.0) +
            (0.30 if _has_actionable_items(output)   else 0.0) +
            0.40 * _calculate_intent_overlap(task_intent, output)
        )
    else:  # writing / marketing
        score = (
            (0.30 if len(output) > 100 else len(output) / 100 * 0.30) +
            (0.0  if _has_error_keywords(output) else 0.20) +
            0.50 * _calculate_intent_overlap(task_intent, output)
        )

    return max(0.0, min(1.0, score))


# ── AI call ────────────────────────────────────────────────────────────────────
def _call_dashscope(model: str, system: str, user: str, max_tokens: int = 2000) -> dict:
    """Raw HTTP call to Alibaba DashScope (OpenAI-compatible endpoint)."""
    try:
        r = requests.post(
            DASHSCOPE_URL,
            headers={"Authorization": f"Bearer {DASHSCOPE_KEY}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                "max_tokens": max_tokens,
                "temperature": 0.3,
            },
            timeout=120,
        )
        if r.status_code == 200:
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            return {"success": True, "output": content, "error": ""}
        return {"success": False, "output": "", "error": f"HTTP {r.status_code}: {r.text[:300]}"}
    except Exception as e:
        return {"success": False, "output": "", "error": str(e)}


# ── Quality Gate: tiered execute with escalation ───────────────────────────────

def execute_with_quality_gate(
    prompt: str,
    task_type: str,
    dept: dict,
    budget_remaining: float,
) -> dict:
    """
    Run execute stage through tiered quality gate cascade.
    Ported from ai-factory/quality_gate.py pattern.

    Tier 1 (cheap) → confidence score → escalate if <0.6 → Tier 2 → Tier 3
    Score 0.6-0.8 → self-correct at same tier first.
    Score >=0.8 → accept immediately.

    Returns: {success, output, cost, confidence, tier_used, model_used}
    """
    start_tier, max_tier = TIER_RANGES.get(task_type, (1, 3))
    tier_history = []
    total_cost = 0.0
    current_prompt = prompt

    # Use bandit to pick best model — candidates are all models in scope
    candidate_models = list({TIERS[t]["model"] for t in range(start_tier, max_tier + 1)})
    bandit_model = get_best_model(task_type, candidate_models)

    for tier in range(start_tier, max_tier + 1):
        if total_cost >= budget_remaining:
            break

        # Use bandit-selected model for first tier, escalate linearly after
        if tier == start_tier:
            model = bandit_model
            # Match cost to whichever tier that model belongs to
            model_cost = next(
                (TIERS[t]["cost"] for t in TIERS if TIERS[t]["model"] == model),
                TIERS[tier]["cost"]
            )
        else:
            model = TIERS[tier]["model"]
            model_cost = TIERS[tier]["cost"]

        system = dept["system"]

        # Inject previous tier failure context for escalation
        if tier_history:
            prev = tier_history[-1]
            current_prompt = (
                f"A previous model ({prev['model']}, confidence={prev['confidence']:.0%}) "
                f"attempted this but was insufficient.\n\n"
                f"ORIGINAL TASK:\n{prompt}\n\n"
                f"PREVIOUS OUTPUT:\n{prev['output'][:1500]}\n\n"
                f"Please produce a better, complete, correct solution."
            )

        # Attempt 1
        result = _call_dashscope(model, system, current_prompt)
        total_cost += model_cost

        if not result["success"]:
            tier_history.append({"tier": tier, "model": model, "confidence": 0.0,
                                  "output": result["error"], "success": False})
            update_bandit(model, task_type, False)
            continue

        confidence = evaluate_confidence(prompt, result["output"], task_type)

        # Self-correction if in middle band (0.6-0.8)
        if ACCEPT_THRESHOLD <= confidence < GOOD_THRESHOLD and total_cost < budget_remaining:
            sc_prompt = (
                f"{current_prompt}\n\n"
                f"--- SELF-CORRECTION (confidence={confidence:.0%}) ---\n"
                f"Your output was partially acceptable but needs improvement. "
                f"Please fix and resubmit a complete, high-quality answer."
            )
            sc_result = _call_dashscope(model, system, sc_prompt)
            total_cost += model_cost
            if sc_result["success"]:
                sc_confidence = evaluate_confidence(prompt, sc_result["output"], task_type)
                if sc_confidence >= confidence:
                    result = sc_result
                    confidence = sc_confidence

        success = confidence >= ACCEPT_THRESHOLD
        update_bandit(model, task_type, confidence >= CONFIDENCE_THRESHOLD)

        tier_history.append({
            "tier": tier, "model": model, "confidence": confidence,
            "output": result["output"], "success": success,
        })

        if confidence >= GOOD_THRESHOLD:
            break  # Excellent — accept immediately
        if success and tier == max_tier:
            break  # Last tier, accept whatever we have

    # Return best result
    successful = [t for t in tier_history if t["success"]]
    best = successful[-1] if successful else (
        max(tier_history, key=lambda t: t["confidence"]) if tier_history else None
    )

    if not best:
        return {"success": False, "output": "", "cost": total_cost,
                "confidence": 0.0, "tier_used": 0, "model_used": "none"}

    return {
        "success": best["success"],
        "output":  best["output"],
        "cost":    total_cost,
        "confidence": best["confidence"],
        "tier_used":  best["tier"],
        "model_used": best["model"],
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
    result = _call_dashscope(
        "qwen-plus", system,
        f"Split this task:\n\nTitle: {task['title']}\nPrompt: {task.get('prompt','')}",
        max_tokens=1000
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

    # Execute stage: full Quality Gate tier cascade + bandit routing
    if stage == "execute":
        gate_result = execute_with_quality_gate(p, task_type, dept, budget_remaining)
        output    = gate_result["output"]
        cost      = gate_result["cost"]
        confidence = gate_result["confidence"]
        model_used = gate_result["model_used"]
        success    = gate_result["success"]
        tier_used  = gate_result["tier_used"]
    else:
        # Non-execute stages: pick model via bandit, single call
        model = get_best_model(task_type, [dept["default_model"], "qwen-turbo"])
        cost_per_call = next(
            (TIERS[t]["cost"] for t in TIERS if TIERS[t]["model"] == model),
            0.0005
        )
        start = time.time()
        result = _call_dashscope(model, dept["system"], p)
        cost = cost_per_call

        valid, reason = schema_validate(result.get("output", ""), stage)
        output = result.get("output", "") if valid else ""
        success = valid and result["success"]
        confidence = evaluate_confidence(prompt, output, task_type) if success else 0.0
        model_used = model
        tier_used = 0
        update_bandit(model, task_type, success)

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
        "cost":       cost,
        "confidence": confidence,
        "model_used": model_used,
        "tier_used":  tier_used,
    }


# ── Process a full task ────────────────────────────────────────────────────────
def process_task(task: dict) -> str:
    task_id   = task["id"]
    title     = task.get("title", "Untitled")
    prompt    = task.get("prompt", task.get("description", title))
    task_type = task.get("task_type", "coding")
    budget    = float(task.get("budget_cap_usd") or MAX_TASK_BUDGET)

    dept = DEPARTMENTS.get(task_type, DEFAULT_DEPT)
    dept_name = dept["name"]
    print(f"\n→ [{task_id[:8]}] {title} [{dept_name}] budget=${budget:.2f}")

    if budget <= 0:
        sb_patch("tasks", task_id, {"status": "failed", "result": {"error": "zero budget"}})
        return "failed"

    sb_patch("tasks", task_id, {
        "status": "in_progress",
        "department": dept_name,
        "model_used": dept["default_model"],
    })

    # Decompose complex tasks (Devin <30min rule)
    if should_decompose(task):
        print(f"  [decompose] splitting complex task...")
        subtasks = decompose_task(task)
        if subtasks:
            print(f"  [decompose] → {len(subtasks)} subtasks queued")
            sub_budget = round(budget / len(subtasks), 4)
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
    total_cost = 0.0
    all_passed = True
    prev_output = ""
    final_confidence = 0.0
    final_model = dept["default_model"]
    final_tier = 0

    for stage in SOP_STAGES:
        budget_remaining = budget - total_cost
        if budget_remaining <= 0:
            print(f"  budget exhausted — skipping {stage}")
            all_passed = False
            break

        print(f"  [{stage}]", end=" ", flush=True)
        stage_result = process_stage(
            task_id, title, prompt, stage, dept, task_type, prev_output, budget_remaining
        )

        total_cost += stage_result["cost"]

        if stage_result["success"]:
            prev_output = stage_result["output"]
            if stage == "execute":
                final_confidence = stage_result["confidence"]
                final_model = stage_result["model_used"]
                final_tier  = stage_result["tier_used"]
            conf_str = f"conf={stage_result['confidence']:.0%}" if stage_result["confidence"] else ""
            print(f"✓ {conf_str}")
        else:
            all_passed = False
            print(f"✗")
            if stage == "execute":
                print(f"  execute failed — marking for review")
                break

    final_status = "completed" if all_passed else "review"
    sb_patch("tasks", task_id, {
        "status": final_status,
        "cost_usd": round(total_cost, 5),
        "result": {
            "department":     dept_name,
            "model":          final_model,
            "tier_used":      final_tier,
            "confidence_score": round(final_confidence, 3),
            "all_passed":     all_passed,
            "total_cost_usd": round(total_cost, 5),
            "output_preview": prev_output[:800] if prev_output else "",
        },
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })
    print(f"  → {final_status.upper()} conf={final_confidence:.0%} ${total_cost:.4f}")
    return final_status


# ── Main loop ──────────────────────────────────────────────────────────────────
def run_loop():
    dept_summary = ", ".join(f"{k}={v['default_model']}" for k, v in DEPARTMENTS.items())
    print(f"[agency] Worker started — poll={POLL_INTERVAL}s retries={MAX_RETRIES} budget=${MAX_TASK_BUDGET}")
    print(f"[agency] Supabase: {SUPABASE_URL}")
    print(f"[agency] Departments: {dept_summary}")
    print(f"[agency] Features: Thompson sampling + Ralph Gate + Quality Gate tiers")

    consecutive_errors = 0
    while True:
        try:
            tasks = sb_get("tasks?status=eq.pending&order=priority.desc,created_at.asc&limit=3")
            if isinstance(tasks, list) and tasks:
                print(f"\n[agency] {len(tasks)} pending task(s)")
                for task in tasks:
                    process_task(task)
                consecutive_errors = 0
            else:
                print(".", end="", flush=True)
                consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            print(f"\n[agency] error #{consecutive_errors}: {e}")
            if consecutive_errors >= 5:
                print("[agency] too many errors — backing off 5 min")
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
