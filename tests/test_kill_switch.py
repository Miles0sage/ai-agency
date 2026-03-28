# tests/test_kill_switch.py
"""Tests for global kill switch."""
from kill_switch import should_exit, request_shutdown, reset_shutdown


def test_should_exit_starts_false():
    reset_shutdown()
    assert should_exit() is False


def test_request_shutdown_sets_flag():
    reset_shutdown()
    request_shutdown()
    assert should_exit() is True


def test_reset_clears_flag():
    request_shutdown()
    reset_shutdown()
    assert should_exit() is False
