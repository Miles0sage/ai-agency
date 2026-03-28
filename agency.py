"""
AI Agency — Autonomous task processing loop.
Runs as a background thread inside the FastAPI server.
Uses Alibaba DashScope API directly (no subprocess, Railway-safe).
"""
import time
import json
import os
import threading
import requests
from datetime import datetime, timezone

# ── Config ───────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://upximucxncuajnakylyf.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVweGltdWN4bmN1YWpuYWt5bHlmIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2NjcyODUxOCwiZXhwIjoyMDgyMzA0NTE4fQ.VlzvndBY3Bs77zm8ZazERiFBlay2AEzqSpLGHu5BEaM"))

DASHSCOPE_KEY = os.environ.get("DASHSCOPE_API_KEY", "sk-sp-7424af93156c47fb94a524398af5f43e")
DASHSCOPE_URL = "https://coding-intl.dashscope.aliyuncs.com/v1/chat/completions"

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "20"))
SOP_STAGES = ["requirements", "plan", "execute", "verify", "deliver"]

# ── Departments ───────────────────────────────────────────────────────────────
# Each dept maps task_type → model + system prompt
DEPARTMENTS = {
    "coding": {
        "name": "Engineering",
        "model": "qwen-coder-plus",
        "system": "You are a senior software engineer. Write clean, tested, production-ready code. Always include error handling.",
        "cost_per_call": 0.0008,
    },
    "research": {
        "name": "Research",
        "model": "qwen-plus",
        "system": "You are a research analyst. Provide thorough, well-cited analysis with clear conclusions and actionable insights.",
        "cost_per_call": 0.0005,
    },
    "writing": {
        "name": "Writing",
        "model": "qwen-turbo",
        "system": "You are a professional content writer. Produce clear, engaging, well-structured content tailored to the audience.",
        "cost_per_call": 0.0002,
    },
    "qa": {
        "name": "QA",
        "model": "qwen-plus",
        "system": "You are a QA engineer. Find bugs, edge cases, and quality issues. Provide specific, actionable test cases.",
        "cost_per_call": 0.0005,
    },
    "marketing": {
        "name": "Marketing",
        "model": "qwen-turbo",
        "system": "You are a growth marketer. Write compelling copy and strategies that drive conversions and engagement.",
        "cost_per_call": 0.0002,
    },
}
DEFAULT_DEPT = DEPARTMENTS["coding"]

# ── Supabase helpers ──────────────────────────────────────────────────────────
SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

def sb_get(path):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{path}", headers=SB_HEADERS, timeout=10)
    return r.json() if r.ok else []

def sb_post(table, data):
    r = requests.post(f"{SUPABASE_URL}/rest/v1/{table}", headers=SB_HEADERS, json=data, timeout=10)
    return r.json() if r.ok else {}

def sb_patch(table, row_id, data):
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{table}?id=eq.{row_id}",
        headers=SB_HEADERS, json=data, timeout=10
    )
    return r.json() if r.ok else {}

# ── AI call ───────────────────────────────────────────────────────────────────
def call_ai(prompt: str, dept: dict, max_tokens: int = 2000) -> dict:
    """Call Alibaba DashScope API directly."""
    payload = {
        "model": dept["model"],
        "messages": [
            {"role": "system", "content": dept["system"]},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    try:
        r = requests.post(
            DASHSCOPE_URL,
            headers={"Authorization": f"Bearer {DASHSCOPE_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )
        if r.status_code == 200:
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            tokens = data.get("usage", {}).get("total_tokens", 0)
            return {"success": True, "output": content, "tokens": tokens, "error": ""}
        else:
            return {"success": False, "output": "", "tokens": 0, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        return {"success": False, "output": "", "tokens": 0, "error": str(e)}

# ── Stage prompts ─────────────────────────────────────────────────────────────
def stage_prompt(stage: str, title: str, prompt: str, prev_output: str = "") -> str:
    context = f"\nPrevious stage output:\n{prev_output[:1500]}" if prev_output else ""
    templates = {
        "requirements": f"Analyze the requirements for this task:\n\nTitle: {title}\nDescription: {prompt}\n\nOutput clear, numbered requirements.",
        "plan":         f"Create a step-by-step implementation plan for:\n\nTitle: {title}\nDescription: {prompt}{context}\n\nOutput numbered steps with time estimates.",
        "execute":      f"Execute this task:\n\nTitle: {title}\nDescription: {prompt}{context}\n\nProduce the complete deliverable.",
        "verify":       f"Review and verify this deliverable:\n\nTitle: {title}{context}\n\nList issues found and confirm quality. Rate: PASS or FAIL.",
        "deliver":      f"Prepare the final polished deliverable for:\n\nTitle: {title}{context}\n\nFormat cleanly for the client.",
    }
    return templates.get(stage, prompt)

# ── Task processor ────────────────────────────────────────────────────────────
def process_task(task: dict):
    task_id = task["id"]
    title = task.get("title", "Untitled")
    prompt = task.get("prompt", task.get("description", title))
    task_type = task.get("task_type", "coding")

    dept = DEPARTMENTS.get(task_type, DEFAULT_DEPT)
    print(f"\n→ [{task_id[:8]}] {title} [{dept['name']}]")

    sb_patch("tasks", task_id, {"status": "in_progress"})

    total_cost = 0.0
    all_passed = True
    prev_output = ""

    for stage in SOP_STAGES:
        print(f"  [{stage}]", end=" ", flush=True)

        # Try to create a subtask record (table may not exist — ignore errors)
        sub_id = None
        try:
            sub = sb_post("agency_subtasks", {
                "parent_task_id": task_id,
                "stage": stage,
                "status": "in_progress",
            })
            if isinstance(sub, list) and sub:
                sub_id = sub[0].get("id")
            elif isinstance(sub, dict) and sub.get("id"):
                sub_id = sub["id"]
        except Exception:
            pass

        start = time.time()
        p = stage_prompt(stage, title, prompt, prev_output)
        result = call_ai(p, dept)
        duration = round(time.time() - start, 1)
        cost = dept["cost_per_call"]
        total_cost += cost

        if result["success"]:
            prev_output = result["output"]
            print(f"✓ {duration}s")
        else:
            all_passed = False
            print(f"✗ {result['error'][:80]}")

        if sub_id:
            try:
                sb_patch("agency_subtasks", sub_id, {
                    "status": "completed" if result["success"] else "failed",
                    "output": result["output"][:10000] if result["output"] else result["error"][:1000],
                    "worker": dept["model"],
                    "cost_usd": cost,
                    "duration_secs": duration,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                })
            except Exception:
                pass

    final_status = "completed" if all_passed else "review"
    sb_patch("tasks", task_id, {
        "status": final_status,
        "cost_usd": round(total_cost, 4),
        "result": {
            "department": dept["name"],
            "model": dept["model"],
            "stages_completed": len(SOP_STAGES),
            "all_passed": all_passed,
            "output_preview": prev_output[:500] if prev_output else "",
        },
        "completed_at": datetime.now(timezone.utc).isoformat(),
    })
    print(f"  → {final_status.upper()} (${total_cost:.4f})")
    return final_status

# ── Main loop ─────────────────────────────────────────────────────────────────
def run_loop():
    print(f"[agency] Worker started — polling every {POLL_INTERVAL}s")
    print(f"[agency] Supabase: {SUPABASE_URL}")
    print(f"[agency] DashScope model pool: {list(DEPARTMENTS.keys())}")

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
            print(f"\n[agency] Error #{consecutive_errors}: {e}")
            if consecutive_errors >= 5:
                print("[agency] Too many errors, backing off 5 minutes")
                time.sleep(300)
                consecutive_errors = 0
                continue

        time.sleep(POLL_INTERVAL)


def start_background_loop():
    """Start the agency loop in a daemon thread (called from api.py)."""
    t = threading.Thread(target=run_loop, daemon=True, name="agency-worker")
    t.start()
    print(f"[agency] Background worker thread started (tid={t.ident})")
    return t


if __name__ == "__main__":
    run_loop()
