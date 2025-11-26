#!/usr/bin/env bash
set -euo pipefail

echo "Starting UMDT UI tests (Linux/macOS)..."

# Start mock server container
docker compose up -d mock-server

# Wait for mock server readiness
for i in {1..30}; do
  if docker compose exec -T mock-server python -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('localhost',5020)); s.close()" 2>/dev/null; then
    echo "Mock server is ready"
    break
  fi
  echo "Waiting for mock server... ($i/30)"
  sleep 1
done

# Start Xvfb (headless display) and set DISPLAY
if command -v Xvfb >/dev/null 2>&1; then
  Xvfb :99 -screen 0 1024x768x24 &
  XVFB_PID=$!
  export DISPLAY=:99
  sleep 1
fi

# Run UI tests
python -m pytest tests/ui/ -v --tb=short
PYTEST_EXIT=$?

# Teardown
if [ -n "${XVFB_PID-}" ]; then
  kill "$XVFB_PID" || true
fi

docker compose down || true

exit $PYTEST_EXIT
