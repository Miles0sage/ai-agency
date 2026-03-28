# tests/test_episodic_memory.py
"""Tests for episodic memory with pgvector."""
import os
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")

from episodic_memory import _simple_embedding, build_memory_context, find_similar_episodes
from unittest.mock import patch, MagicMock


def test_simple_embedding_returns_384_dim():
    emb = _simple_embedding("hello world")
    assert len(emb) == 384


def test_simple_embedding_is_normalized():
    emb = _simple_embedding("test input")
    magnitude = sum(v * v for v in emb) ** 0.5
    assert abs(magnitude - 1.0) < 0.01


def test_simple_embedding_deterministic():
    emb1 = _simple_embedding("same input")
    emb2 = _simple_embedding("same input")
    assert emb1 == emb2


def test_simple_embedding_different_for_different_input():
    emb1 = _simple_embedding("hello")
    emb2 = _simple_embedding("goodbye")
    assert emb1 != emb2


def test_find_similar_episodes_returns_empty_on_error():
    with patch("episodic_memory.requests") as mock_req:
        mock_req.post.side_effect = Exception("fail")
        mock_req.get.side_effect = Exception("fail")
        result = find_similar_episodes("test query")
        assert result == []


def test_build_memory_context_empty_when_no_episodes():
    with patch("episodic_memory.find_similar_episodes", return_value=[]):
        result = build_memory_context("test", "coding")
        assert result == ""


def test_build_memory_context_with_episodes():
    mock_episodes = [
        {"title": "Write fizzbuzz", "confidence": 0.9, "output_summary": "def fizz..."},
        {"title": "Build API", "confidence": 0.8, "output_summary": "from fastapi..."},
    ]
    with patch("episodic_memory.find_similar_episodes", return_value=mock_episodes):
        result = build_memory_context("Write code", "coding")
        assert "fizzbuzz" in result
        assert "past experiences" in result
