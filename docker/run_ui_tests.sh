#!/usr/bin/env bash
set -euo pipefail

echo "Starting UMDT UI tests (Linux/macOS)..."

# Start mock server as a background host process
echo "Starting mock server on host..."
python mock_server_cli.py start --config docker/e2e_config.yaml --port 5020 &
MOCK_PID=$!
echo "$MOCK_PID" > mock_server.pid

# Wait for mock server readiness
for i in {1..30}; do
  if python -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('localhost',5020)); s.close()" 2>/dev/null; then
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

# Teardown mock server
if [ -f mock_server.pid ]; then
  kill "$(cat mock_server.pid)" || true
  rm -f mock_server.pid || true
fi

exit $PYTEST_EXIT

exit $PYTEST_EXIT
