from unittest.mock import patch, MagicMock
from litellm_gateway import call_llm, get_model_for_task, strip_thinking_tags


def test_get_model_for_coding():
    model = get_model_for_task("coding")
    assert model  # returns a valid model string


def test_get_model_for_default():
    model = get_model_for_task("writing")
    assert model  # returns a valid model string


def test_get_model_for_unknown_falls_back():
    model = get_model_for_task("nonexistent_type")
    assert model  # falls back to default model


def test_strip_thinking_tags():
    assert strip_thinking_tags("Hello <think>reason</think> World") == "Hello  World"


def test_strip_thinking_tags_no_tags():
    assert strip_thinking_tags("No tags here") == "No tags here"


@patch("litellm_gateway.litellm_completion")
def test_call_llm_success(mock_completion):
    mock_choice = MagicMock()
    mock_choice.message.content = "Hello world"
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage.prompt_tokens = 10
    mock_response.usage.completion_tokens = 5
    mock_completion.return_value = mock_response
    result = call_llm("Test prompt", system="You are helpful", task_type="writing")
    assert result["success"] is True
    assert result["output"] == "Hello world"


@patch("litellm_gateway.litellm_completion")
def test_call_llm_failure(mock_completion):
    mock_completion.side_effect = Exception("API error")
    result = call_llm("Test prompt", system="You are helpful", task_type="writing")
    assert result["success"] is False
    assert "API error" in result["error"]
