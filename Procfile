web: uvicorn main:app --host 0.0.0.0 --port 8080
worker: celery -A worker.celery_app worker --loglevel=info --pool=solo
beat: celery -A worker.celery_app beat --loglevel=info