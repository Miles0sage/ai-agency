"""
Self-healing watchdog — monitors failed tasks, patches agency code,
deploys fixes, creates GitHub issues, and sends Discord alerts.

Runs as a background thread inside the FastAPI process.
"""
import os
import re
import json
import time
import threading
import subprocess
from datetime import datetime, timezone, timedelta

from supabase_client import sb_get
from discord_notify import notify_system_event

SEEN_FAILURES: set = set()
POLL_INTERVAL = int(os.environ.get("SELFHEAL_POLL_INTERVAL", "60"))
SELFHEAL_ENABLED = os.environ.get("SELFHEAL_ENABLED", "true").lower() == "true"
GITHUB_REPO = os.environ.get("GITHUB_REPO", "Miles0sage/ai-agency")
AGENCY_DIR = os.path.dirname(os.path.abspath(__file__))

AGENCY_FILES = [
    "agency.py", "api.py", "config.py", "litellm_gateway.py",
    "supabase_client.py", "stuck_detector.py", "discord_notify.py",
]


# ── Failure detection ──────────────────────────────────────────────────────────

def get_recent_failures() -> list:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    tasks = sb_get(
        f"tasks?status=eq.failed"
        f"&updated_at=gte.{cutoff}"
        f"&select=*"
        f"&order=updated_at.desc"
        f"&limit=20"
    )
    return [t for t in tasks if isinstance(t, dict) and t.get("id") not in SEEN_FAILURES]


# ── Analysis & fix generation ──────────────────────────────────────────────────

def analyze_failure(task: dict) -> dict | None:
    """Use Claude Code CLI to classify the failure and generate a code patch."""
    result_field = task.get("result", {})
    if isinstance(result_field, str):
        try:
            result_field = json.loads(result_field)
        except Exception:
            pass

    if isinstance(result_field, dict):
        error_msg = result_field.get("error", "") or str(result_field)
        killed = result_field.get("killed", False)
    else:
        error_msg = str(result_field)
        killed = False

    # Skip user-killed tasks
    if killed or not error_msg:
        return None

    prompt = f"""You are debugging a failed AI agency task. The agency lives at /root/ai-agency/.

FAILED TASK:
  title: {task.get('title', '')}
  type: {task.get('task_type', '')}
  worker: {task.get('worker_used', 'unknown')}
  error: {error_msg[:800]}

Read the relevant source files in /root/ai-agency/ to understand the codebase.

Decide:
1. Is this a BUG in the agency source code, or a task-content failure (bad prompt, API down, etc.)?
2. If it's a code bug, what is the minimal patch?

Respond ONLY with valid JSON:
{{
  "is_code_bug": true | false,
  "file": "<filename.py or null>",
  "old_code": "<exact string to replace, or null>",
  "new_code": "<replacement string, or null>",
  "description": "<one-line fix summary>",
  "severity": "critical | high | medium | low",
  "root_cause": "<one-sentence root cause>"
}}"""

    try:
        r = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "text"],
            capture_output=True, text=True, timeout=120,
            cwd=AGENCY_DIR,
        )
        raw = r.stdout.strip()
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            return json.loads(match.group())
        print(f"[selfheal] claude returned no JSON: {raw[:200]}")
    except Exception as e:
        print(f"[selfheal] claude analyze error: {e}")
    return None


# ── Code patching ──────────────────────────────────────────────────────────────

def apply_patch(fix: dict) -> bool:
    if not fix or not fix.get("is_code_bug"):
        return False
    fname = fix.get("file")
    old = fix.get("old_code")
    new = fix.get("new_code")
    if not all([fname, old, new]):
        return False

    fpath = os.path.join(AGENCY_DIR, fname)
    if not os.path.exists(fpath):
        print(f"[selfheal] file not found: {fname}")
        return False

    with open(fpath) as f:
        content = f.read()

    if old not in content:
        print(f"[selfheal] patch target not found in {fname}")
        return False

    with open(fpath, "w") as f:
        f.write(content.replace(old, new, 1))

    print(f"[selfheal] patched {fname}: {fix.get('description')}")
    return True


def git_push(description: str) -> bool:
    try:
        subprocess.run(["git", "-C", AGENCY_DIR, "add", "-A"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", AGENCY_DIR, "commit", "-m",
                        f"fix: {description}"],
                       check=True, capture_output=True)
        r = subprocess.run(["git", "-C", AGENCY_DIR, "push", "origin", "main"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            print(f"[selfheal] pushed: {description}")
            return True
        print(f"[selfheal] push failed: {r.stderr.strip()}")
    except Exception as e:
        print(f"[selfheal] git error: {e}")
    return False


# ── GitHub issue ───────────────────────────────────────────────────────────────

def create_issue(task: dict, fix: dict | None) -> str | None:
    title = f"[Auto] Failure: {task.get('title', 'unknown')[:70]}"

    result_field = task.get("result", {})
    if isinstance(result_field, dict):
        error_txt = result_field.get("error", str(result_field))
    else:
        error_txt = str(result_field)

    fix_line = fix.get("description", "No code fix applied") if fix else "No code fix applied"
    root_cause = fix.get("root_cause", "Unknown") if fix else "Unknown"
    severity = fix.get("severity", "unknown") if fix else "unknown"
    fix_applied = fix and fix.get("is_code_bug", False)

    body = f"""## Auto-generated failure report

| Field | Value |
|-------|-------|
| Task ID | `{task.get('id', '')}` |
| Task type | {task.get('task_type', '')} |
| Severity | **{severity}** |
| Worker | {task.get('worker_used', 'unknown')} |
| Fix deployed | {'✅ yes' if fix_applied else '❌ no'} |

## Error
```
{error_txt[:1200]}
```

## Root cause
{root_cause}

## Fix applied
{fix_line}

---
*Generated by self-healing watchdog v1*
"""

    try:
        r = subprocess.run(
            ["gh", "issue", "create",
             "--repo", GITHUB_REPO,
             "--title", title,
             "--body", body,
             "--label", "bug"],
            capture_output=True, text=True, timeout=30,
        )
        url = r.stdout.strip()
        print(f"[selfheal] issue: {url}")
        return url
    except Exception as e:
        print(f"[selfheal] gh issue error: {e}")
        return None


# ── Main loop ──────────────────────────────────────────────────────────────────

def _loop():
    print("[selfheal] watchdog started — polling every", POLL_INTERVAL, "s")
    time.sleep(30)  # let app stabilise first

    while True:
        try:
            failures = get_recent_failures()
            for task in failures:
                tid = task.get("id", "")
                SEEN_FAILURES.add(tid)
                print(f"[selfheal] processing failure {tid[:8]}: {task.get('title','')[:50]}")

                fix = analyze_failure(task)
                fix_deployed = False
                issue_url = None

                if fix and fix.get("is_code_bug"):
                    patched = apply_patch(fix)
                    if patched:
                        fix_deployed = git_push(fix.get("description", "auto-fix"))

                issue_url = create_issue(task, fix)

                # Discord notification
                lines = [f"🔴 **Task failed:** {task.get('title','')[:60]}"]
                if fix:
                    lines.append(f"🔍 **Root cause:** {fix.get('root_cause','?')}")
                if fix_deployed:
                    lines.append(f"✅ **Auto-fix deployed:** {fix.get('description')}")
                else:
                    lines.append("⚠️ No code fix — manual review needed")
                if issue_url:
                    lines.append(f"📋 **Issue:** {issue_url}")

                notify_system_event("\n".join(lines))

        except Exception as e:
            print(f"[selfheal] loop error: {e}")

        time.sleep(POLL_INTERVAL)


def start_watchdog():
    if not SELFHEAL_ENABLED:
        print("[selfheal] disabled via SELFHEAL_ENABLED=false")
        return
    t = threading.Thread(target=_loop, name="selfheal-watchdog", daemon=True)
    t.start()
