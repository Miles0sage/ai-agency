"""
Celery app — task queue with hard kill support.
Hard kill: app.control.revoke(task_id, terminate=True, signal='SIGKILL')
Watchdog runs as periodic Celery Beat task.
"""
from celery import Celery
from config import REDIS_URL
from kill_switch import install_signal_handlers


def make_celery() -> Celery:
    app = Celery("ai-agency", broker=REDIS_URL, backend=REDIS_URL)
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        task_track_started=True,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        task_soft_time_limit=900,   # 15 min soft
        task_time_limit=960,        # 16 min hard SIGKILL
        beat_schedule={
            "watchdog-sweep": {
                "task": "celery_app.watchdog_sweep",
                "schedule": 30.0,
            },
        },
    )

    @app.task(name="celery_app.process_task_async", bind=True, max_retries=2)
    def process_task_async(self, task_data: dict):
        install_signal_handlers()
        from agency import process_task
        return process_task(task_data)

    @app.task(name="celery_app.watchdog_sweep")
    def watchdog_sweep():
        from stuck_detector import run_watchdog_sweep
        from config import SUPABASE_URL, SUPABASE_KEY, STUCK_TIMEOUT_SECONDS
        return run_watchdog_sweep(SUPABASE_URL, SUPABASE_KEY, timeout_seconds=STUCK_TIMEOUT_SECONDS)

    return app


app = make_celery()
