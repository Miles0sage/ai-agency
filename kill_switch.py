# kill_switch.py
"""
Global kill switch — ported from OpenHands stop_if_should_exit pattern.
Set the flag via request_shutdown() or SIGTERM/SIGINT.
"""
import signal
import threading

_shutdown_requested = threading.Event()


def should_exit() -> bool:
    return _shutdown_requested.is_set()


def request_shutdown(*args):
    _shutdown_requested.set()


def reset_shutdown():
    _shutdown_requested.clear()


def install_signal_handlers():
    import threading
    if threading.current_thread() is not threading.main_thread():
        return  # Signal handlers only work in main thread
    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
