web: gunicorn --workers 1 --threads 4 --timeout 180 app:app
worker: python scripts/run_worker.py
release: alembic upgrade head
