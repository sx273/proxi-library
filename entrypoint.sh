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

# Ensure data directories exist
mkdir -p "${DASHBOARD_DATA:-/data/dashboard}" "${MUSIC_ROOT:-/music}"
echo "✔  Data directories ready"

echo "--------------------------------------------------------"
echo "  Starting Xvfb + Uvicorn on port 6067..."
echo "========================================================"

exec xvfb-run -a -s "-screen 0 1920x1080x24" \
    uvicorn app.main:app --host 0.0.0.0 --port 6067
