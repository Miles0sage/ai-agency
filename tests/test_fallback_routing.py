# tests/test_fallback_routing.py
"""Tests for multi-provider fallback routing."""
import os
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")

from unittest.mock import patch, MagicMock, call
from litellm_gateway import call_llm, _is_retriable_provider_error


def test_is_retriable_auth_error():
    assert _is_retriable_provider_error("AuthenticationError: invalid key") is True


def test_is_retriable_timeout():
    assert _is_retriable_provider_error("Connection timeout after 30s") is True


def test_not_retriable_content_error():
    assert _is_retriable_provider_error("Content policy violation") is False


@patch("litellm_gateway.litellm_completion")
def test_fallback_on_auth_error(mock_completion):
    """When primary model gets auth error, fall back to next model."""
    import litellm

    # First call fails with auth error
    mock_completion.side_effect = [
        litellm.AuthenticationError(message="Invalid key", llm_provider="test", model="test"),
        # Second call (fallback) succeeds
        MagicMock(
            choices=[MagicMock(message=MagicMock(content="Hello from fallback"))],
            usage=MagicMock(prompt_tokens=10, completion_tokens=5),
        ),
    ]

    result = call_llm("test", system="test", model_override="minimax/MiniMax-M2.7")
    assert result["success"] is True
    assert result["output"]  # got a response


@patch("litellm_gateway.litellm_completion")
def test_all_fallbacks_fail(mock_completion):
    """When all models fail, return error."""
    mock_completion.side_effect = Exception("AuthenticationError: all dead")

    result = call_llm("test", system="test", model_override="minimax/MiniMax-M2.7")
    assert result["success"] is False
