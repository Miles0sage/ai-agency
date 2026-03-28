from celery_app import make_celery


def test_celery_app_creates():
    app = make_celery()
    assert app.main == "ai-agency"


def test_process_task_is_registered():
    app = make_celery()
    assert "celery_app.process_task_async" in app.tasks


def test_watchdog_sweep_is_registered():
    app = make_celery()
    assert "celery_app.watchdog_sweep" in app.tasks
