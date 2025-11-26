#!/bin/bash
# Run UMDT E2E tests using Docker Compose
#
# Usage:
#   ./docker/run_e2e.sh
#
# This script:
# 1. Builds the Docker images
# 2. Starts mock-server container
# 3. Runs E2E tests from cli container
# 4. Cleans up

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "=========================================="
echo "UMDT Docker E2E Test Runner"
echo "=========================================="

# Clean up any previous containers
echo "Cleaning up previous containers..."
docker compose down --remove-orphans 2>/dev/null || true

# Build images
echo "Building Docker images..."
docker compose build

# Start mock server in background
echo "Starting mock server..."
docker compose up -d mock-server

# Wait for mock server to be healthy
echo "Waiting for mock server to be ready..."
for i in {1..30}; do
    if docker compose exec -T mock-server python -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('localhost',5020)); s.close()" 2>/dev/null; then
        echo "Mock server is ready!"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "ERROR: Mock server failed to start"
        docker compose logs mock-server
        docker compose down
        exit 1
    fi
    echo "  Waiting... ($i/30)"
    sleep 1
done

# Run E2E tests from cli container
echo ""
echo "Running E2E tests..."
docker compose run --rm cli python docker/e2e_test.py --host mock-server --port 5020
TEST_EXIT_CODE=$?

# Cleanup
echo ""
echo "Cleaning up..."
docker compose down

# Report result
if [ $TEST_EXIT_CODE -eq 0 ]; then
    echo ""
    echo "=========================================="
    echo "E2E TESTS PASSED"
    echo "=========================================="
else
    echo ""
    echo "=========================================="
    echo "E2E TESTS FAILED (exit code: $TEST_EXIT_CODE)"
    echo "=========================================="
fi

exit $TEST_EXIT_CODE
