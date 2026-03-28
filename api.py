"""AI Agency API — Submit tasks, check status, view dashboard."""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import os

app = FastAPI(title="AI Agency", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _start_worker():
    from agency import start_background_loop
    start_background_loop()

SB_URL = os.environ.get("SUPABASE_URL", "https://upximucxncuajnakylyf.supabase.co")
SB_KEY = os.environ.get("SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVweGltdWN4bmN1YWpuYWt5bHlmIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2NjcyODUxOCwiZXhwIjoyMDgyMzA0NTE4fQ.VlzvndBY3Bs77zm8ZazERiFBlay2AEzqSpLGHu5BEaM"))
H = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json", "Prefer": "return=representation"}

class TaskIn(BaseModel):
    title: str
    prompt: str = ""
    task_type: str = "coding"
    priority: int = 5

@app.post("/tasks")
def create_task(t: TaskIn):
    import uuid
    data = {"id": str(uuid.uuid4()), "title": t.title, "prompt": t.prompt or t.title, "task_type": t.task_type, "priority": t.priority, "status": "pending"}
    r = requests.post(f"{SB_URL}/rest/v1/tasks", headers=H, json=data)
    if r.status_code >= 400: raise HTTPException(r.status_code, r.text)
    return r.json()

@app.get("/tasks/{task_id}")
def get_task(task_id: str):
    # Try with subtasks join first, fall back to plain select if table missing
    r = requests.get(f"{SB_URL}/rest/v1/tasks?id=eq.{task_id}&select=*,agency_subtasks(*)", headers=H)
    data = r.json()
    if not isinstance(data, list):
        r = requests.get(f"{SB_URL}/rest/v1/tasks?id=eq.{task_id}&select=*", headers=H)
        data = r.json()
    if not isinstance(data, list) or not data:
        raise HTTPException(404, "Task not found")
    return data[0]

@app.get("/tasks")
def list_tasks(status: str = None, limit: int = 20):
    q = f"{SB_URL}/rest/v1/tasks?order=created_at.desc&limit={limit}"
    if status: q += f"&status=eq.{status}"
    return requests.get(q, headers=H).json()

@app.get("/dashboard")
def dashboard():
    r = requests.get(f"{SB_URL}/rest/v1/tasks?select=status,cost_usd", headers=H)
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
