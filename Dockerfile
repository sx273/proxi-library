FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb xauth x11vnc novnc libatomic1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

RUN python -m playwright install-deps chromium \
    && python -m playwright install chromium \
    && rm -rf /var/lib/apt/lists/*

COPY app ./app
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

ENV LUCIDADL_HOME=/data/lucidadl \
    DASHBOARD_DATA=/data/dashboard \
    MUSIC_ROOT=/music

EXPOSE 6067

CMD ["./entrypoint.sh"]