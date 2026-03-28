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
    assert "AI FACTORY" in response.text or "factory" in response.text.lower()


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


@patch("celery_app.app.send_task")
@patch("api.requests")
def test_webhook_github(mock_requests, mock_celery):
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = [{"id": "wh-1"}]
    mock_requests.post.return_value = mock_resp

    response = client.post("/webhooks/github", json={
        "action": "opened",
        "issue": {"title": "Bug: login broken", "body": "Steps to reproduce..."},
    })
    assert response.status_code == 200
    assert response.json()["source"] == "github"
