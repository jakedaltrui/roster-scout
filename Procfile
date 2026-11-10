web: gunicorn --workers 1 --threads 4 --timeout 180 app:app
worker: python scripts/run_worker.py
scheduler: python scripts/run_scheduler.py
purge: python scripts/run_purge.py
release: alembic upgrade head
