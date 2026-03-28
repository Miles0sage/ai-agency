# tests/test_watchdog.py
"""Tests for StuckDetector — ported from OpenHands 5 heuristics."""
from stuck_detector import StuckDetector


def test_not_stuck_with_no_history():
    detector = StuckDetector()
    assert detector.is_stuck() is False


def test_not_stuck_with_varied_actions():
    detector = StuckDetector()
    detector.record_action("search files")
    detector.record_observation("found 3 files")
    detector.record_action("read file.py")
    detector.record_observation("file contents...")
    assert detector.is_stuck() is False


def test_stuck_on_identical_action_repeated_4_times():
    detector = StuckDetector()
    for _ in range(4):
        detector.record_action("search files")
        detector.record_observation("found 3 files")
    assert detector.is_stuck() is True
    assert detector.stuck_reason == "identical_action_observation"


def test_stuck_on_error_loop_3_times():
    detector = StuckDetector()
    for _ in range(3):
        detector.record_action("run code")
        detector.record_error("SyntaxError: unexpected EOF")
    assert detector.is_stuck() is True
    assert detector.stuck_reason == "repeating_action_error"


def test_stuck_on_empty_output_3_times():
    detector = StuckDetector()
    for _ in range(3):
        detector.record_action("generate response")
        detector.record_observation("")
    assert detector.is_stuck() is True
    assert detector.stuck_reason == "empty_output_loop"


def test_reset_clears_history():
    detector = StuckDetector()
    for _ in range(4):
        detector.record_action("same")
        detector.record_observation("same")
    assert detector.is_stuck() is True
    detector.reset()
    assert detector.is_stuck() is False
