web: uvicorn api:app --host 0.0.0.0 --port ${PORT:-8000}
worker: celery -A celery_app.app worker --loglevel=info --concurrency=2
beat: celery -A celery_app.app beat --loglevel=info
