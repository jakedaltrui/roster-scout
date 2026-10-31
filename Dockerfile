FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ROSTER_SCOUT_CACHE_DIR=/var/cache/roster-scout \
    NCAA_BROWSER_HEADLESS=0 \
    NCAA_BROWSER_CHANNEL=chrome

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl xauth xvfb \
    && curl --fail --silent --show-error --location \
        https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
        --output /tmp/google-chrome.deb \
    && apt-get install -y --no-install-recommends /tmp/google-chrome.deb \
    && rm -f /tmp/google-chrome.deb \
    && rm -rf /var/lib/apt/lists/*

COPY app.py roster_scraper.py usage_limits.py ./
COPY templates/ ./templates/
COPY data/ ./data/
COPY static/ ./static/

RUN useradd --create-home --uid 10001 roster-scout \
    && mkdir -p /var/cache/roster-scout \
    && chown -R roster-scout:roster-scout /var/cache/roster-scout /app

USER roster-scout

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1:5000/healthz || exit 1

CMD ["xvfb-run", "-a", "gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "180", "app:app"]
