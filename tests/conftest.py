# tests/conftest.py
"""Shared test fixtures for AI Agency tests."""
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def mock_supabase():
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
    return {
        "id": "test-task-001",
        "title": "Write fizzbuzz",
        "prompt": "Write a Python function that prints fizzbuzz for 1 to 15",
        "task_type": "coding",
        "priority": 5,
        "status": "pending",
    }
