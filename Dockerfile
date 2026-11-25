FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY app ./app

ENV LUCIDADL_HOME=/data/lucidadl \
    DASHBOARD_DATA=/data/dashboard \
    MUSIC_ROOT=/music

EXPOSE 6067

CMD ["xvfb-run", "-a", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "6067"]