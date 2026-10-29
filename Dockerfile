FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    ROSTER_SCOUT_CACHE_DIR=/var/cache/roster-scout \
    NCAA_BROWSER_HEADLESS=0

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m patchright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright

RUN apt-get update \
    && apt-get install -y --no-install-recommends xauth \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN useradd --create-home --uid 10001 roster-scout \
    && mkdir -p /var/cache/roster-scout \
    && chown -R roster-scout:roster-scout /var/cache/roster-scout /app

USER roster-scout

EXPOSE 5000

CMD ["xvfb-run", "-a", "gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "180", "app:app"]
