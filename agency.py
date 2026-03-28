"""
AI Agency — Autonomous task loop.
Tasks in, deliverables out.
"""
import time
import json
import sys
import os
import subprocess
import requests
from datetime import datetime

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://upximucxncuajnakylyf.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVweGltdWN4bmN1YWpuYWt5bHlmIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2NjcyODUxOCwiZXhwIjoyMDgyMzA0NTE4fQ.VlzvndBY3Bs77zm8ZazERiFBlay2AEzqSpLGHu5BEaM"))
FACTORY_DIR = "/root/ai-factory"
POLL_INTERVAL = 30

headers = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

SOP_STAGES = ["requirements", "plan", "execute", "verify", "deliver"]

def sb_get(path):
    return requests.get(f"{SUPABASE_URL}/rest/v1/{path}", headers=headers).json()

def sb_post(table, data):
    return requests.post(f"{SUPABASE_URL}/rest/v1/{table}", headers=headers, json=data).json()

def sb_patch(table, id, data):
    h = {**headers, "Prefer": "return=representation"}
    return requests.patch(f"{SUPABASE_URL}/rest/v1/{table}?id=eq.{id}", headers=h, json=data).json()

def dispatch_to_factory(prompt, task_type="feature", cwd="/root"):
    """Call AI Factory orchestrator directly."""
    try:
        result = subprocess.run(
            [sys.executable, f"{FACTORY_DIR}/orchestrator.py", prompt, "--cwd", cwd],
            capture_output=True, text=True, timeout=300
        )
        return {"success": result.returncode == 0, "output": result.stdout[:5000], "error": result.stderr[:2000]}
    except Exception as e:
        return {"success": False, "output": "", "error": str(e)}

def process_task(task):
    task_id = task["id"]
    title = task.get("title", "")
    desc = task.get("description", "")
    task_type = task.get("task_type", "coding")

    print(f"\n→ [{task_id[:8]}] {title}")
    sb_patch("tasks", task_id, {"status": "in_progress"})

    total_cost = 0
    all_passed = True

    for stage in SOP_STAGES:
        print(f"  [{stage}]...", end=" ", flush=True)

        # Create subtask
        sub = sb_post("agency_subtasks", {
            "parent_task_id": task_id,
            "stage": stage,
            "status": "in_progress",
        })
        sub_id = sub[0]["id"] if isinstance(sub, list) and sub else None

        # Build stage-specific prompt
        prompts = {
            "requirements": f"Analyze requirements for: {title}\n{desc}\nOutput clear bullet points.",
            "plan": f"Create implementation plan for: {title}\n{desc}\nOutput numbered steps.",
            "execute": f"Implement: {title}\n{desc}\nWrite the code or content.",
            "verify": f"Review and verify the output for: {title}\nCheck for errors, test if code.",
            "deliver": f"Prepare final deliverable for: {title}\nClean up and format for client.",
        }

        start = time.time()
        result = dispatch_to_factory(prompts[stage], task_type)
        duration = time.time() - start
        cost = 0.001  # Alibaba default

        if sub_id:
            sb_patch("agency_subtasks", sub_id, {
                "status": "completed" if result["success"] else "failed",
                "output": result["output"][:10000],
                "worker": "alibaba",
                "cost_usd": cost,
                "duration_secs": round(duration, 1),
                "completed_at": datetime.utcnow().isoformat(),
            })

        total_cost += cost

        if result["success"]:
            print(f"✓ ({duration:.1f}s)")
        else:
            print(f"✗ ({result['error'][:60]})")
            all_passed = False

    # Complete the task
    sb_patch("tasks", task_id, {
        "status": "completed" if all_passed else "review",
        "cost_usd": total_cost,
        "result": {"stages_completed": len(SOP_STAGES), "all_passed": all_passed},
        "completed_at": datetime.utcnow().isoformat(),
    })
    print(f"  → {'DONE' if all_passed else 'NEEDS REVIEW'} (${total_cost:.3f})")

def run_loop():
    print(f"🏭 AI Agency started — polling every {POLL_INTERVAL}s")
    print(f"   Supabase: {SUPABASE_URL}")
    print(f"   Factory:  {FACTORY_DIR}\n")

    while True:
        try:
            tasks = sb_get("tasks?status=eq.pending&order=priority.desc,created_at.asc&limit=5")
            if tasks:
                print(f"📋 {len(tasks)} pending tasks")
                for task in tasks:
                    process_task(task)
            else:
                print(".", end="", flush=True)
        except Exception as e:
            print(f"\n⚠ Error: {e}")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    if not SUPABASE_KEY:
        print("Set SUPABASE_SERVICE_KEY or SUPABASE_KEY env var")
        sys.exit(1)
    run_loop()
