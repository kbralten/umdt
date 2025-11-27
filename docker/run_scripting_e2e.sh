#!/bin/bash
# Run UMDT scripting E2E tests
# Uses docker-compose.scripting.yml with script-enabled services

set -e

echo "=============================================="
echo "UMDT Scripting E2E Test Runner"
echo "=============================================="

# Build and start containers
echo "Building and starting containers with scripts enabled..."
docker compose -f docker-compose.scripting.yml up --build -d

# Wait for health checks
echo "Waiting for services to become healthy..."
sleep 5

# Run the scripting E2E tests
echo "Running scripting E2E tests..."
docker compose -f docker-compose.scripting.yml exec -T cli python docker/e2e_scripting_test.py --host bridge --port 5020
TEST_EXIT_CODE=$?

# Show logs on failure
if [ $TEST_EXIT_CODE -ne 0 ]; then
    echo ""
    echo "=============================================="
    echo "Tests failed - showing container logs"
    echo "=============================================="
    echo "--- Mock Server Logs ---"
    docker compose -f docker-compose.scripting.yml logs mock-server | tail -50
    echo ""
    echo "--- Bridge Logs ---"
    docker compose -f docker-compose.scripting.yml logs bridge | tail -50
fi

# Cleanup
echo ""
echo "Stopping containers..."
docker compose -f docker-compose.scripting.yml down

exit $TEST_EXIT_CODE
