# =========================================================================
# OPS_ENGINE - Dockerfile
# =========================================================================
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/app/pw-browsers

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN playwright install-deps firefox && \
    playwright install firefox && \
    rm -rf /var/lib/apt/lists/*

COPY dados/ ./dados/
COPY fluxos/ ./fluxos/
COPY utils/ ./utils/
COPY workers/ ./workers/
COPY main.py poller.py writer.py ./

RUN mkdir -p logs

CMD ["python", "main.py"]