#!/bin/sh
set -e

echo "========================================================"
echo "  PROXI LIBRARY — container bootstrap"
echo "========================================================"

# Check xvfb-run and xauth before attempting to start
if command -v xvfb-run >/dev/null 2>&1; then
    echo "✔  xvfb-run found"
else
    echo "✖  xvfb-run NOT found — server cannot start"
    exit 1
fi

if command -v xauth >/dev/null 2>&1; then
    echo "✔  xauth found"
else
    echo "✖  xauth NOT found — xvfb-run will fail"
    exit 1
fi

# Ensure data directories exist and clean stale Chromium profile locks
mkdir -p "${DASHBOARD_DATA:-/data/dashboard}" "${MUSIC_ROOT:-/music}" "${LUCIDADL_HOME:-/data/lucidadl}/profile"
rm -f \
    "${LUCIDADL_HOME:-/data/lucidadl}/profile/SingletonLock" \
    "${LUCIDADL_HOME:-/data/lucidadl}/profile/SingletonSocket" \
    "${LUCIDADL_HOME:-/data/lucidadl}/profile/SingletonCookie"
echo "✔  Data directories ready"

echo "--------------------------------------------------------"
echo "  Starting Xvfb + Uvicorn on port 6067..."
echo "========================================================"

# Do NOT use exec here: xvfb-run relies on SIGUSR1 + wait, which hangs when
# xvfb-run runs as PID 1 in a container. Running it as a child process keeps
# /bin/sh as PID 1 and lets xvfb-run start Xvfb and Uvicorn normally.
xvfb-run -e /dev/stderr -a -s "-screen 0 1440x960x24" \
    uvicorn app.main:app --host 0.0.0.0 --port 6067 &
child_pid=$!

trap 'kill -TERM "$child_pid" 2>/dev/null || true; wait "$child_pid" 2>/dev/null || true' TERM INT
wait "$child_pid"
