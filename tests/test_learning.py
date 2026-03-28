from learning import record_outcome, get_past_successes, get_best_model_from_history, build_context_from_history
from unittest.mock import patch, MagicMock


def test_record_outcome_does_not_raise():
    """record_outcome is best-effort — should never raise."""
    with patch("learning.sb_post", side_effect=Exception("fail")):
        record_outcome("http://fake", "key", "coding", "test", "model", 0.8, 0.001, True)


def test_get_past_successes_returns_empty_on_error():
    with patch("learning.sb_get", side_effect=Exception("fail")):
        result = get_past_successes("http://fake", "key", "coding")
        assert result == []


def test_get_best_model_from_history_empty():
    with patch("learning.get_past_successes", return_value=[]):
        result = get_best_model_from_history("http://fake", "key", "coding")
        assert result is None


def test_get_best_model_from_history_picks_highest():
    mock_data = [
        {"model_used": "modelA", "confidence": 0.9},
        {"model_used": "modelA", "confidence": 0.85},
        {"model_used": "modelB", "confidence": 0.7},
    ]
    with patch("learning.get_past_successes", return_value=mock_data):
        result = get_best_model_from_history("http://fake", "key", "coding")
        assert result == "modelA"


def test_build_context_empty():
    with patch("learning.get_past_successes", return_value=[]):
        result = build_context_from_history("http://fake", "key", "coding")
        assert result == ""


def test_build_context_with_history():
    mock_data = [
        {"prompt_summary": "Write fizzbuzz", "output_preview": "def fizz...", "confidence": 0.8},
    ]
    with patch("learning.get_past_successes", return_value=mock_data):
        result = build_context_from_history("http://fake", "key", "coding")
        assert "past successful outcomes" in result
        assert "fizzbuzz" in result
