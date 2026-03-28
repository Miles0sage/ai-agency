# Phase 3: Production-Ready AI Factory

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the AI Factory production-ready — wire browser-use into research, deploy to Railway with Redis, add tests for the two untested critical modules, and add pgvector memory for cross-task learning.

**Architecture:** Four independent subsystems: (1) browser-use integration into the research department's execute stage, (2) Railway deployment with Redis addon for Celery, (3) test coverage for agency.py and api.py, (4) pgvector episodic memory on Supabase. Each task is independently shippable.

**Tech Stack:** Python 3.11, browser-use, FastAPI, Celery, Redis, Supabase (pgvector), pytest

**Spec:** `docs/superpowers/specs/2026-03-28-ai-factory-v2-design.md`

---

## File Structure

```
ai-agency/
├── agency.py              # MODIFY: add browser-use to research execute stage
├── config.py              # MODIFY: add REDIS_URL, ENABLE_BROWSER
├── supabase_client.py     # existing — shared Supabase helpers
├── browser_agent.py       # existing — already built, needs wiring
├── tests/
│   ├── test_agency.py     # CREATE: unit tests for process_task, confidence, quality gate
│   ├── test_api.py        # CREATE: FastAPI endpoint tests with TestClient
│   └── conftest.py        # CREATE: shared fixtures (mock supabase, mock llm)
├── migrations/
│   └── 003_pgvector.sql   # CREATE: enable pgvector, create embeddings table
└── requirements.txt       # MODIFY: add httpx (for TestClient)
```

---

### Task 1: Shared Test Fixtures

**Files:**
- Create: `tests/conftest.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Add httpx to requirements.txt**

```
# Add to requirements.txt
httpx==0.27.0
```

Run: `cd /root/ai-agency && pip install httpx`

- [ ] **Step 2: Create conftest.py with shared fixtures**

```python
# tests/conftest.py
"""Shared test fixtures for AI Agency tests."""
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def mock_supabase():
    """Mock all Supabase calls — prevents real HTTP during tests."""
    with patch("supabase_client.requests") as mock_req:
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_response.json.return_value = []
        mock_req.get.return_value = mock_response
        mock_req.post.return_value = mock_response
        mock_req.patch.return_value = mock_response
        yield mock_req


@pytest.fixture
def mock_llm():
    """Mock LiteLLM calls — returns configurable output."""
    with patch("litellm_gateway.litellm_completion") as mock_comp:
        mock_choice = MagicMock()
        mock_choice.message.content = "def fizzbuzz():\n    for i in range(1, 16):\n        print(i)"
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_resp.usage.prompt_tokens = 100
        mock_resp.usage.completion_tokens = 50
        mock_comp.return_value = mock_resp
        yield mock_comp


@pytest.fixture
def sample_task():
    """Standard test task dict."""
    return {
        "id": "test-task-001",
        "title": "Write fizzbuzz",
        "prompt": "Write a Python function that prints fizzbuzz for 1 to 15",
        "task_type": "coding",
        "priority": 5,
        "status": "pending",
    }
```

- [ ] **Step 3: Run existing tests to verify fixtures don't break anything**

Run: `cd /root/ai-agency && SUPABASE_URL=https://test.supabase.co SUPABASE_SERVICE_KEY=test python -m pytest tests/ --tb=short -q`
Expected: 34 passed

- [ ] **Step 4: Commit**

```bash
cd /root/ai-agency
git add tests/conftest.py requirements.txt
git commit -m "test: add shared fixtures (mock_supabase, mock_llm, sample_task)"
```

---

### Task 2: Tests for agency.py — Confidence Scoring

**Files:**
- Create: `tests/test_agency.py`

- [ ] **Step 1: Write failing tests for evaluate_confidence**

```python
# tests/test_agency.py
"""Tests for agency.py — confidence scoring, schema validation, task decomposition."""
import os
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")

from agency import evaluate_confidence, schema_validate, should_decompose


class TestEvaluateConfidence:
    def test_empty_output_returns_zero(self):
        assert evaluate_confidence("Write code", "", "coding") == 0.0

    def test_coding_with_function_scores_high(self):
        output = "def fizzbuzz():\n    for i in range(1, 16):\n        print(i)"
        score = evaluate_confidence("Write fizzbuzz", output, "coding")
        assert score >= 0.5

    def test_coding_with_error_scores_low(self):
        output = "SyntaxError: unexpected EOF while parsing"
        score = evaluate_confidence("Write code", output, "coding")
        assert score < 0.4

    def test_research_with_structure_scores_well(self):
        output = "## Findings\n- Point 1: important finding\n- Point 2: another finding\n\nAccording to sources, this is significant."
        score = evaluate_confidence("Research topic", output, "research")
        assert score >= 0.5

    def test_writing_with_substance_scores_well(self):
        output = "This is a comprehensive article about the topic.\n\n" + "Content. " * 30
        score = evaluate_confidence("Write article", output, "writing")
        assert score >= 0.5

    def test_score_bounded_zero_to_one(self):
        score = evaluate_confidence("x", "y" * 1000, "coding")
        assert 0.0 <= score <= 1.0


class TestSchemaValidate:
    def test_empty_output_fails(self):
        valid, reason = schema_validate("", "execute")
        assert valid is False

    def test_short_output_fails(self):
        valid, reason = schema_validate("hi", "execute")
        assert valid is False

    def test_refusal_output_fails(self):
        valid, reason = schema_validate("I cannot help with that", "execute")
        assert valid is False

    def test_valid_output_passes(self):
        valid, reason = schema_validate("Here is a complete implementation of the requested feature with tests.", "execute")
        assert valid is True


class TestShouldDecompose:
    def test_short_coding_task_no_decompose(self):
        task = {"prompt": "Write fizzbuzz", "task_type": "coding"}
        assert should_decompose(task) is False

    def test_long_coding_task_decomposes(self):
        task = {"prompt": "x " * 300, "task_type": "coding"}
        assert should_decompose(task) is True

    def test_multi_signal_task_decomposes(self):
        task = {"prompt": "Build a web app and also add tests. Additionally, deploy it.\n- step 1\n- step 2", "task_type": "coding"}
        assert should_decompose(task) is True
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd /root/ai-agency && SUPABASE_URL=https://test.supabase.co SUPABASE_SERVICE_KEY=test python -m pytest tests/test_agency.py -v`
Expected: All tests PASS (these test pure functions with no mocking needed)

- [ ] **Step 3: Commit**

```bash
cd /root/ai-agency
git add tests/test_agency.py
git commit -m "test: add agency.py tests — confidence scoring, schema validation, decomposition"
```

---

### Task 3: Tests for api.py — FastAPI Endpoints

**Files:**
- Create: `tests/test_api.py`

- [ ] **Step 1: Write failing tests for API endpoints**

```python
# tests/test_api.py
"""Tests for api.py — FastAPI endpoint tests."""
import os
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")

from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from api import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_agent_card():
    response = client.get("/.well-known/agent.json")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "AI Factory v2"
    assert len(data["skills"]) == 5


def test_dashboard_serves_html():
    response = client.get("/")
    assert response.status_code == 200
    assert "AI FACTORY" in response.text


@patch("celery_app.app.send_task")
@patch("api.requests")
def test_create_task(mock_requests, mock_celery):
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = [{"id": "test-123", "title": "Test"}]
    mock_requests.post.return_value = mock_resp

    response = client.post("/tasks", json={
        "title": "Test task",
        "prompt": "Do something",
        "task_type": "coding",
    })
    assert response.status_code == 200


@patch("api.requests")
def test_list_tasks(mock_requests):
    mock_resp = MagicMock()
    mock_resp.json.return_value = [{"id": "1", "title": "Task 1", "status": "completed"}]
    mock_requests.get.return_value = mock_resp

    response = client.get("/tasks")
    assert response.status_code == 200


@patch("api.requests")
def test_dashboard_stats(mock_requests):
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = [
        {"status": "completed", "cost_usd": 0.003},
        {"status": "failed", "cost_usd": 0.001},
    ]
    mock_requests.get.return_value = mock_resp

    response = client.get("/dashboard")
    assert response.status_code == 200
    data = response.json()
    assert data["total_tasks"] == 2
    assert "completed" in data["by_status"]


@patch("api.requests")
def test_webhook_github(mock_requests):
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = [{"id": "wh-1"}]
    mock_requests.post.return_value = mock_resp

    with patch("celery_app.app.send_task") as mock_celery:
        response = client.post("/webhooks/github", json={
            "action": "opened",
            "issue": {"title": "Bug: login broken", "body": "Steps to reproduce..."},
        })
    assert response.status_code == 200
    assert response.json()["source"] == "github"
```

- [ ] **Step 2: Run tests**

Run: `cd /root/ai-agency && SUPABASE_URL=https://test.supabase.co SUPABASE_SERVICE_KEY=test python -m pytest tests/test_api.py -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
cd /root/ai-agency
git add tests/test_api.py
git commit -m "test: add api.py endpoint tests — health, A2A, dashboard, webhooks, task CRUD"
```

---

### Task 4: Wire browser-use into Research Department

**Files:**
- Modify: `agency.py` — add browser-use call for research execute stage
- Modify: `config.py` — add ENABLE_BROWSER flag

- [ ] **Step 1: Add ENABLE_BROWSER to config.py**

Add after the existing config:
```python
# ── Browser ──
ENABLE_BROWSER = os.environ.get("ENABLE_BROWSER", "false").lower() == "true"
```

- [ ] **Step 2: Modify agency.py — add browser enhancement for research execute**

In `process_stage`, before the LLM call for the execute stage when task_type is "research", add a web fetch to enhance the prompt with real data:

```python
# In process_stage, after building prompt `p` but before calling call_llm:
if stage == "execute" and task_type == "research":
    from config import ENABLE_BROWSER
    if ENABLE_BROWSER:
        from browser_agent import web_fetch
        # Extract a search-worthy query from the title
        search_url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(title)}"
        fetch_result = web_fetch(search_url, extract_text=True)
        if fetch_result["success"] and fetch_result["output"]:
            web_context = fetch_result["output"][:2000]
            p = f"Web research results:\n{web_context}\n\n---\n\n{p}"
```

- [ ] **Step 3: Write test for browser-enhanced research**

Add to `tests/test_agency.py`:
```python
class TestBrowserResearchIntegration:
    def test_web_fetch_returns_dict(self):
        """Verify web_fetch returns correct structure without live HTTP."""
        from unittest.mock import patch, MagicMock
        from browser_agent import web_fetch
        mock_resp = MagicMock()
        mock_resp.text = "<html><body>Hello world</body></html>"
        with patch("browser_agent.requests.get", return_value=mock_resp):
            result = web_fetch("https://example.com", extract_text=True)
        assert result["success"] is True
        assert "Hello world" in result["output"]
```

- [ ] **Step 4: Run all tests**

Run: `cd /root/ai-agency && SUPABASE_URL=https://test.supabase.co SUPABASE_SERVICE_KEY=test python -m pytest tests/ --tb=short -q`
Expected: All tests pass (browser integration is behind ENABLE_BROWSER flag, off by default)

- [ ] **Step 5: Commit**

```bash
cd /root/ai-agency
git add agency.py config.py tests/test_agency.py
git commit -m "feat: wire browser-use into research execute stage (behind ENABLE_BROWSER flag)"
```

---

### Task 5: Add REDIS_URL to config.py + Move Remaining Env Vars

**Files:**
- Modify: `config.py`
- Modify: `celery_app.py`
- Modify: `agency.py`

- [ ] **Step 1: Move remaining env var reads to config.py**

Add to `config.py`:
```python
# ── Redis / Celery ──
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# ── Worker ──
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "20"))
MAX_RETRIES = int(os.environ.get("MAX_STAGE_RETRIES", "3"))
MAX_SUBTASKS = int(os.environ.get("MAX_DECOMPOSED_SUBTASKS", "5"))
```

- [ ] **Step 2: Update celery_app.py to import from config**

Replace `REDIS_URL = os.environ.get(...)` with `from config import REDIS_URL`

- [ ] **Step 3: Update agency.py to import from config**

Replace any remaining `os.environ.get` calls for `POLL_INTERVAL`, `MAX_RETRIES`, `MAX_SUBTASKS` with imports from config. Remove `import os` if no longer needed.

- [ ] **Step 4: Run all tests**

Run: `cd /root/ai-agency && SUPABASE_URL=https://test.supabase.co SUPABASE_SERVICE_KEY=test python -m pytest tests/ --tb=short -q`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
cd /root/ai-agency
git add config.py celery_app.py agency.py
git commit -m "refactor: centralize all env vars in config.py (REDIS_URL, POLL_INTERVAL, MAX_RETRIES)"
```

---

### Task 6: Deploy to Railway

**Files:**
- Existing: `Procfile`, `railway.toml`, `requirements.txt`

- [ ] **Step 1: Verify Procfile is correct**

```
web: uvicorn api:app --host 0.0.0.0 --port ${PORT:-8000}
worker: celery -A celery_app.app worker --loglevel=info --concurrency=2
beat: celery -A celery_app.app beat --loglevel=info
```

- [ ] **Step 2: Push to GitHub**

```bash
cd /root/ai-agency && git push origin main
```

- [ ] **Step 3: Create Railway Redis addon**

Via Railway dashboard or CLI:
```bash
railway add --plugin redis
```

- [ ] **Step 4: Set environment variables on Railway**

Required:
```
SUPABASE_URL=https://upximucxncuajnakylyf.supabase.co
SUPABASE_SERVICE_KEY=<key>
MINIMAX_API_KEY=<key>
REDIS_URL=<from Railway Redis addon>
DISCORD_WEBHOOK_URL=<optional>
ENABLE_BROWSER=false
```

- [ ] **Step 5: Create 3 Railway services**

1. **web** — runs Procfile `web:` command
2. **worker** — runs Procfile `worker:` command (custom start command: `celery -A celery_app.app worker --loglevel=info --concurrency=2`)
3. **beat** — runs Procfile `beat:` command (custom start command: `celery -A celery_app.app beat --loglevel=info`)

All 3 share the same env vars and Redis addon.

- [ ] **Step 6: Verify deployment**

```bash
# Check health
curl https://<railway-url>/health

# Check dashboard
curl https://<railway-url>/dashboard

# Check A2A card
curl https://<railway-url>/.well-known/agent.json

# Submit a test task
curl -X POST https://<railway-url>/tasks \
  -H 'Content-Type: application/json' \
  -d '{"title":"Write hello world","task_type":"coding"}'
```

- [ ] **Step 7: Commit any deployment fixes**

```bash
cd /root/ai-agency && git add -A && git commit -m "fix: deployment adjustments" && git push origin main
```

---

## Phase 3 Complete Checklist

After all 6 tasks:
- [ ] Shared test fixtures in `conftest.py`
- [ ] `agency.py` has tests: confidence scoring, schema validation, decomposition (13+ tests)
- [ ] `api.py` has tests: health, A2A, dashboard, webhooks, CRUD (7+ tests)
- [ ] browser-use wired into research execute (behind `ENABLE_BROWSER` flag)
- [ ] All env vars centralized in `config.py`
- [ ] Deployed to Railway with Redis, 3 services running
- [ ] All tests pass: `pytest tests/ -v`
- [ ] E2E verified: submit task via Railway URL → completes

---

## What's Next (Phase 4)

1. **E2B sandbox** — `sandbox_executor.py` for safe code execution in Firecracker VMs
2. **pgvector memory** — `migrations/003_pgvector.sql` for embedding-based task similarity search
3. **OpenHands SDK** — integrate as a coding executor for SWE-bench-level tasks
4. **Multi-provider routing** — add DeepSeek, Groq, OpenRouter API keys for full model fleet
