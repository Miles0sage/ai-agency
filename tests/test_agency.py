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


class TestBrowserResearchIntegration:
    def test_web_fetch_returns_dict(self):
        from unittest.mock import patch, MagicMock
        from browser_agent import web_fetch
        mock_resp = MagicMock()
        mock_resp.text = "<html><body>Hello world</body></html>"
        with patch("requests.get", return_value=mock_resp):
            result = web_fetch("https://example.com", extract_text=True)
        assert result["success"] is True
        assert "Hello world" in result["output"]
