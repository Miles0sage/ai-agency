"""
AI Agency — Production worker loop.
Architecture based on: MetaGPT pub/sub, SWE-agent reviewer loop,
CrewAI cost separation, Devin <30min decomposition, AutoGen Swarm handoffs.
"""
import time
import os
import threading
import requests
from datetime import datetime, timezone
from typing import Optional

# ── Config ─────────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://upximucxncuajnakylyf.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVweGltdWN4bmN1YWpuYWt5bHlmIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2NjcyODUxOCwiZXhwIjoyMDgyMzA0NTE4fQ.VlzvndBY3Bs77zm8ZazERiFBlay2AEzqSpLGHu5BEaM"))

DASHSCOPE_KEY = os.environ.get("DASHSCOPE_API_KEY", "sk-sp-7424af93156c47fb94a524398af5f43e")
DASHSCOPE_URL = "https://coding-intl.dashscope.aliyuncs.com/v1/chat/completions"

POLL_INTERVAL    = int(os.environ.get("POLL_INTERVAL", "20"))
MAX_RETRIES      = int(os.environ.get("MAX_STAGE_RETRIES", "3"))
MAX_TASK_BUDGET  = float(os.environ.get("MAX_TASK_BUDGET_USD", "0.10"))
MAX_SUBTASKS     = int(os.environ.get("MAX_DECOMPOSED_SUBTASKS", "5"))

SOP_STAGES = ["requirements", "plan", "execute", "verify", "deliver"]

# ── Departments (MetaGPT pattern: each dept has role + model) ──────────────────
DEPARTMENTS = {
    "coding": {
        "name": "Engineering",
        "model": "qwen-coder-plus",         # specialist coding model
        "reviewer_model": "qwen-turbo",     # cheap reviewer (SWE-agent pattern)
        "system": "You are a senior software engineer. Write clean, tested, production-ready code with error handling and type hints.",
        "cost_per_call": 0.0008,
        "reviewer_cost": 0.0002,
    },
    "research": {
        "name": "Research",
        "model": "qwen-plus",
        "reviewer_model": "qwen-turbo",
        "system": "You are a research analyst. Provide thorough, well-structured analysis with clear conclusions and actionable insights.",
        "cost_per_call": 0.0005,
        "reviewer_cost": 0.0002,
    },
    "writing": {
        "name": "Writing",
        "model": "qwen-turbo",
        "reviewer_model": "qwen-turbo",
        "system": "You are a professional content writer. Produce clear, engaging, well-structured content tailored to the audience.",
        "cost_per_call": 0.0002,
        "reviewer_cost": 0.0001,
    },
    "qa": {
        "name": "QA",
        "model": "qwen-plus",
        "reviewer_model": "qwen-turbo",
        "system": "You are a QA engineer. Find bugs, edge cases, and quality issues. Provide specific, actionable test cases and verification steps.",
        "cost_per_call": 0.0005,
        "reviewer_cost": 0.0002,
    },
    "marketing": {
        "name": "Marketing",
        "model": "qwen-turbo",
        "reviewer_model": "qwen-turbo",
        "system": "You are a growth marketer. Write compelling copy and strategies that drive conversions and engagement.",
        "cost_per_call": 0.0002,
        "reviewer_cost": 0.0001,
    },
}
DEFAULT_DEPT = DEPARTMENTS["coding"]

# ── Supabase helpers ────────────────────────────────────────────────────────────
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

# ── AI calls ────────────────────────────────────────────────────────────────────
def _call_dashscope(model: str, system: str, user: str, max_tokens: int = 2000) -> dict:
    """Raw HTTP call to Alibaba DashScope (OpenAI-compatible)."""
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

def call_ai(prompt: str, dept: dict, max_tokens: int = 2000) -> dict:
    return _call_dashscope(dept["model"], dept["system"], prompt, max_tokens)

def call_reviewer(output: str, task_title: str, stage: str, dept: dict) -> dict:
    """Cheap reviewer model — scores output PASS/FAIL (SWE-agent pattern)."""
    system = "You are a strict quality reviewer. Respond with exactly one line: PASS or FAIL, then a colon, then brief reason. Example: PASS: code is correct and complete"
    prompt = (
        f"Review this {stage} output for task: {task_title}\n\n"
        f"OUTPUT:\n{output[:3000]}\n\n"
        f"Is this output complete, correct, and production-ready? "
        f"Respond PASS or FAIL with brief reason."
    )
    return _call_dashscope(dept["reviewer_model"], system, prompt, max_tokens=200)

def schema_validate(output: str, stage: str) -> tuple[bool, str]:
    """
    Deterministic schema validation — catches empty/error outputs before calling reviewer.
    Returns (is_valid, reason).
    """
    if not output or not output.strip():
        return False, "empty output"
    if len(output.strip()) < 20:
        return False, f"output too short ({len(output)} chars)"
    error_keywords = ["i cannot", "i'm unable", "i don't have access", "error occurred", "api error"]
    lower = output.lower()
    for kw in error_keywords:
        if kw in lower:
            return False, f"output contains error pattern: '{kw}'"
    return True, "ok"

# ── Task decomposition (Devin <30min pattern) ──────────────────────────────────
def should_decompose(task: dict) -> bool:
    """Heuristic: decompose complex tasks to keep each subtask under 30 min."""
    prompt = task.get("prompt", "")
    task_type = task.get("task_type", "coding")
    # Decompose if: prompt is long (likely multi-part) or task_type is coding + long prompt
    if task_type in ("coding", "research") and len(prompt) > 500:
        return True
    # Decompose if prompt contains multiple distinct requirements
    multi_signals = ["and also", "additionally", "furthermore", "plus", "as well as", "\n-", "\n*", "\n1."]
    if sum(1 for s in multi_signals if s in prompt.lower()) >= 2:
        return True
    return False

def decompose_task(task: dict, dept: dict) -> list[dict]:
    """Use orchestrator model to split complex task into ≤MAX_SUBTASKS subtasks."""
    system = (
        "You are a technical project manager. Split the given task into independent subtasks. "
        f"Each subtask must be completable in under 30 minutes. Maximum {MAX_SUBTASKS} subtasks. "
        "Respond with a JSON array of objects: [{\"title\": \"...\", \"prompt\": \"...\", \"task_type\": \"...\"}]. "
        "Only output the JSON array, nothing else."
    )
    prompt = f"Split this task into subtasks:\n\nTitle: {task['title']}\nPrompt: {task.get('prompt', '')}"
    result = _call_dashscope("qwen-plus", system, prompt, max_tokens=1000)

    if not result["success"]:
        return []

    import json
    import re
    try:
        # Extract JSON array from response
        text = result["output"].strip()
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            subtasks = json.loads(match.group())
            # Validate shape
            valid = [s for s in subtasks if isinstance(s, dict) and s.get("title") and s.get("prompt")]
            return valid[:MAX_SUBTASKS]
    except Exception:
        pass
    return []

# ── Stage prompts ───────────────────────────────────────────────────────────────
def stage_prompt(stage: str, title: str, prompt: str, prev_output: str = "") -> str:
    ctx = f"\n\nPrevious stage output:\n{prev_output[:2000]}" if prev_output else ""
    return {
        "requirements": f"Analyze the requirements for this task:\n\nTitle: {title}\nDescription: {prompt}\n\nOutput clear numbered requirements.",
        "plan":         f"Create a step-by-step implementation plan:\n\nTitle: {title}\nDescription: {prompt}{ctx}\n\nOutput numbered steps with time estimates.",
        "execute":      f"Execute this task completely:\n\nTitle: {title}\nDescription: {prompt}{ctx}\n\nProduce the complete, production-ready deliverable.",
        "verify":       f"Verify and test this deliverable:\n\nTitle: {title}{ctx}\n\nList any issues. Confirm PASS or FAIL with specific findings.",
        "deliver":      f"Prepare the final polished deliverable:\n\nTitle: {title}{ctx}\n\nFormat cleanly and completely for the client.",
    }.get(stage, prompt)

# ── Core: process one SOP stage with reviewer loop ─────────────────────────────
def process_stage(
    task_id: str, title: str, prompt: str, stage: str,
    dept: dict, prev_output: str, budget_remaining: float
) -> dict:
    """
    Run a single SOP stage with SWE-agent reviewer loop.
    Returns: {success, output, cost, retries}
    """
    sub_id = None
    sub = sb_post("agency_subtasks", {
        "parent_task_id": task_id,
        "stage": stage,
        "status": "in_progress",
        "worker": dept["model"],
    })
    if sub and sub.get("id"):
        sub_id = sub["id"]

    stage_cost = 0.0
    final_output = ""
    final_success = False
    retries = 0

    for attempt in range(MAX_RETRIES):
        if stage_cost >= budget_remaining:
            print(f"    budget exhausted at stage {stage}")
            break

        # Build prompt (on retry, include previous attempt's feedback)
        p = stage_prompt(stage, title, prompt, prev_output)
        if attempt > 0 and final_output:
            p += f"\n\nPrevious attempt was rejected. Reviewer feedback: {review_reason}\nPlease fix and resubmit."

        start = time.time()
        result = call_ai(p, dept)
        duration = round(time.time() - start, 1)
        stage_cost += dept["cost_per_call"]
        retries = attempt

        if not result["success"]:
            print(f"    attempt {attempt+1} API error: {result['error'][:60]}")
            continue

        output = result["output"]

        # Step 1: deterministic schema validation (free)
        valid, reason = schema_validate(output, stage)
        if not valid:
            print(f"    attempt {attempt+1} schema fail: {reason}")
            review_reason = reason
            continue

        # Step 2: semantic reviewer (cheap model — SWE-agent pattern)
        # Skip reviewer on cheap/fast stages to save budget
        if stage in ("execute", "verify") and dept.get("reviewer_model"):
            rev = call_reviewer(output, title, stage, dept)
            stage_cost += dept["reviewer_cost"]
            review_text = rev.get("output", "FAIL: no response").strip()
            passed = review_text.upper().startswith("PASS")
            review_reason = review_text[5:].strip() if ":" in review_text else review_text

            print(f"    attempt {attempt+1} {'PASS' if passed else 'FAIL'} ({duration}s) — {review_reason[:60]}")

            if passed:
                final_output = output
                final_success = True
                break
            # FAIL → loop back with feedback
        else:
            # No reviewer for requirements/plan/deliver — schema pass is enough
            print(f"    attempt {attempt+1} ok ({duration}s)")
            final_output = output
            final_success = True
            break

    # Update subtask record
    if sub_id:
        sb_patch("agency_subtasks", sub_id, {
            "status": "completed" if final_success else "failed",
            "output": (final_output or result.get("error", ""))[:10000],
            "cost_usd": round(stage_cost, 5),
            "retry_count": retries,
            "review_score": "PASS" if final_success else "FAIL",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })

    return {"success": final_success, "output": final_output, "cost": stage_cost}

# ── Process a single task through full SOP ──────────────────────────────────────
def process_task(task: dict) -> str:
    task_id  = task["id"]
    title    = task.get("title", "Untitled")
    prompt   = task.get("prompt", task.get("description", title))
    task_type = task.get("task_type", "coding")
    budget   = float(task.get("budget_cap_usd") or MAX_TASK_BUDGET)

    dept = DEPARTMENTS.get(task_type, DEFAULT_DEPT)
    print(f"\n→ [{task_id[:8]}] {title} [{dept['name']}] budget=${budget:.2f}")

    # Budget guard
    if budget <= 0:
        sb_patch("tasks", task_id, {"status": "failed", "result": {"error": "zero budget"}})
        return "failed"

    sb_patch("tasks", task_id, {"status": "in_progress", "department": dept["name"], "model_used": dept["model"]})

    # ── Decomposition check (Devin pattern) ──────────────────────────────────
    if should_decompose(task):
        print(f"  [decompose] task is complex, splitting...")
        subtasks = decompose_task(task, dept)
        if subtasks:
            print(f"  [decompose] split into {len(subtasks)} subtasks")
            # Queue each subtask as a new pending task
            for st in subtasks:
                sb_post("tasks", {
                    "title": st["title"],
                    "prompt": st["prompt"],
                    "task_type": st.get("task_type", task_type),
                    "priority": task.get("priority", 5),
                    "status": "pending",
                    "parent_task_id": task_id,
                    "trace_id": str(task.get("trace_id", task_id)),
                    "budget_cap_usd": round(budget / len(subtasks), 4),
                })
            # Mark parent as in_progress/orchestrating
            sb_patch("tasks", task_id, {
                "status": "in_progress",
                "result": {"decomposed": True, "subtask_count": len(subtasks)},
            })
            return "decomposed"

    # ── SOP pipeline ─────────────────────────────────────────────────────────
    total_cost = 0.0
    all_passed = True
    prev_output = ""

    for stage in SOP_STAGES:
        print(f"  [{stage}]", end=" ", flush=True)
        budget_remaining = budget - total_cost

        if budget_remaining <= 0:
            print(f"budget exhausted, skipping remaining stages")
            all_passed = False
            break

        stage_result = process_stage(
            task_id, title, prompt, stage, dept, prev_output, budget_remaining
        )

        total_cost += stage_result["cost"]

        if stage_result["success"]:
            prev_output = stage_result["output"]
        else:
            all_passed = False
            # Fail fast on execute stage — no point delivering bad work
            if stage == "execute":
                print(f"  execute failed after {MAX_RETRIES} attempts, marking for review")
                break

    final_status = "completed" if all_passed else "review"
    sb_patch("tasks", task_id, {
        "status": final_status,
        "cost_usd": round(total_cost, 5),
        "result": {
            "department": dept["name"],
            "model": dept["model"],
            "all_passed": all_passed,
            "total_cost_usd": round(total_cost, 5),
            "output_preview": prev_output[:800] if prev_output else "",
        },
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })
    print(f"  → {final_status.upper()} ${total_cost:.4f}")
    return final_status

# ── Main loop ───────────────────────────────────────────────────────────────────
def run_loop():
    print(f"[agency] Worker started — poll={POLL_INTERVAL}s retries={MAX_RETRIES} budget=${MAX_TASK_BUDGET}")
    print(f"[agency] Supabase: {SUPABASE_URL}")
    dept_summary = ', '.join(f'{k}={v["model"]}' for k, v in DEPARTMENTS.items())
    print(f"[agency] Departments: {dept_summary}")

    consecutive_errors = 0
    while True:
        try:
            tasks = sb_get("tasks?status=eq.pending&order=priority.desc,created_at.asc&limit=3")
            if tasks:
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
                print("[agency] backing off 5 min after repeated errors")
                time.sleep(300)
                consecutive_errors = 0
                continue

        time.sleep(POLL_INTERVAL)


def start_background_loop():
    """Called from api.py startup to launch worker as daemon thread."""
    t = threading.Thread(target=run_loop, daemon=True, name="agency-worker")
    t.start()
    print(f"[agency] background thread started (tid={t.ident})")
    return t


if __name__ == "__main__":
    run_loop()
