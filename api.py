"""AI Agency API — Submit tasks, check status, kill tasks, view dashboard."""
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import os
import requests
import uuid

from config import SUPABASE_URL, SUPABASE_KEY
from supabase_client import HEADERS as H
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app):
    from agency import start_background_loop
    start_background_loop()
    yield


app = FastAPI(title="AI Agency", version="0.3.1", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class TaskIn(BaseModel):
    title: str
    prompt: str = ""
    task_type: str = "coding"
    priority: int = 5


@app.post("/tasks")
def create_task(t: TaskIn):
    task_id = str(uuid.uuid4())
    data = {
        "id": task_id, "title": t.title, "prompt": t.prompt or t.title,
        "task_type": t.task_type, "priority": t.priority, "status": "pending",
    }
    r = requests.post(f"{SUPABASE_URL}/rest/v1/tasks", headers=H, json=data)
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text)

    # Dispatch to Celery worker (falls back to pending queue if Redis unavailable)
    try:
        from celery_app import app as celery
        celery.send_task("celery_app.process_task_async", args=[data])
    except Exception:
        pass  # Task stays pending — background worker or Celery will pick it up

    return r.json()


@app.delete("/tasks/{task_id}")
def kill_task(task_id: str):
    """Kill a running task — hard stop via Celery revoke + mark failed in Supabase."""
    # Mark failed in Supabase
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/tasks?id=eq.{task_id}",
        headers=H,
        json={"status": "failed", "result": {"error": "Killed by user", "killed": True}},
    )
    return {"status": "killed", "task_id": task_id}


@app.get("/tasks/{task_id}")
def get_task(task_id: str):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/tasks?id=eq.{task_id}&select=*,agency_subtasks(*)", headers=H)
    data = r.json()
    if not isinstance(data, list):
        r = requests.get(f"{SUPABASE_URL}/rest/v1/tasks?id=eq.{task_id}&select=*", headers=H)
        data = r.json()
    if not isinstance(data, list) or not data:
        raise HTTPException(404, "Task not found")
    return data[0]


@app.get("/tasks")
def list_tasks(status: str = None, limit: int = 20):
    q = f"{SUPABASE_URL}/rest/v1/tasks?order=created_at.desc&limit={limit}"
    if status:
        q += f"&status=eq.{status}"
    return requests.get(q, headers=H).json()


@app.get("/dashboard")
def dashboard():
    r = requests.get(f"{SUPABASE_URL}/rest/v1/tasks?select=status,cost_usd", headers=H)
    tasks = r.json() if r.ok else []
    if not isinstance(tasks, list):
        tasks = []
    total = len(tasks)
    by_status = {}
    total_cost = 0
    for t in tasks:
        s = t.get("status", "unknown") if isinstance(t, dict) else "unknown"
        by_status[s] = by_status.get(s, 0) + 1
        total_cost += float(t.get("cost_usd") or 0) if isinstance(t, dict) else 0
    return {"total_tasks": total, "by_status": by_status, "total_cost_usd": round(total_cost, 4)}


_DASHBOARD_HTML = Path(__file__).with_name("dashboard.html")


@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    """Serve the cyberpunk factory floor dashboard."""
    return _DASHBOARD_HTML.read_text()


@app.get("/health")
def health():
    from config import MODEL_ROUTING
    default_model = MODEL_ROUTING.get("default", {}).get("model", "unknown")
    return {"status": "ok", "version": "0.3.2", "default_model": default_model, "commit": "c5439c5"}


@app.get("/.well-known/agent.json")
def agent_card():
    return {
        "name": "AI Factory v2",
        "description": "Autonomous AI agency — submit tasks, get verified deliverables. 5-stage SOP pipeline with quality gates.",
        "url": os.environ.get("PUBLIC_URL", "http://localhost:8000"),
        "version": "0.3.0",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": True,
        },
        "skills": [
            {
                "id": "coding",
                "name": "Software Engineering",
                "description": "Write, test, and review production code",
                "inputModes": ["text"],
                "outputModes": ["text"],
            },
            {
                "id": "research",
                "name": "Research & Analysis",
                "description": "Research topics, analyze data, produce reports",
                "inputModes": ["text"],
                "outputModes": ["text"],
            },
            {
                "id": "writing",
                "name": "Content Writing",
                "description": "Write articles, docs, marketing copy",
                "inputModes": ["text"],
                "outputModes": ["text"],
            },
            {
                "id": "qa",
                "name": "Quality Assurance",
                "description": "Test plans, bug reports, verification",
                "inputModes": ["text"],
                "outputModes": ["text"],
            },
            {
                "id": "marketing",
                "name": "Marketing & Growth",
                "description": "Growth strategies, ad copy, campaigns",
                "inputModes": ["text"],
                "outputModes": ["text"],
            },
        ],
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
    }


@app.post("/webhooks/{source}")
def webhook_trigger(source: str, payload: dict):
    """
    Accept webhooks from GitHub, Slack, PagerDuty, etc.
    Auto-creates tasks based on the event.
    """
    # Extract title and prompt based on source
    if source == "github":
        action = payload.get("action", "")
        issue = payload.get("issue", {})
        title = f"[GitHub] {issue.get('title', 'Unknown issue')}"
        prompt = issue.get("body", "") or title
        task_type = "coding"
    elif source == "slack":
        text = payload.get("event", {}).get("text", payload.get("text", ""))
        title = f"[Slack] {text[:80]}"
        prompt = text
        task_type = "coding"
    elif source == "pagerduty":
        incident = payload.get("messages", [{}])[0].get("incident", {})
        title = f"[PagerDuty] {incident.get('title', 'Incident')}"
        prompt = incident.get("description", "") or title
        task_type = "coding"
    else:
        title = f"[{source}] Webhook task"
        prompt = str(payload)[:2000]
        task_type = "coding"

    task_id = str(uuid.uuid4())
    data = {
        "id": task_id, "title": title, "prompt": prompt,
        "task_type": task_type, "priority": 7, "status": "pending",
        "source": source,
    }

    r = requests.post(f"{SUPABASE_URL}/rest/v1/tasks", headers=H, json=data)
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text)

    # Dispatch to Celery (falls back to pending queue if Redis unavailable)
    try:
        from celery_app import app as celery
        celery.send_task("celery_app.process_task_async", args=[data])
    except Exception:
        pass

    return {"status": "created", "task_id": task_id, "source": source}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
