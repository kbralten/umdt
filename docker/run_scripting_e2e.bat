@echo off
REM Run UMDT scripting E2E tests (Windows)
REM Uses docker-compose.scripting.yml with script-enabled services

echo ==============================================
echo UMDT Scripting E2E Test Runner
echo ==============================================

REM Build and start containers
echo Building and starting containers with scripts enabled...
docker compose -f docker-compose.scripting.yml up --build -d
if %ERRORLEVEL% neq 0 (
    echo Failed to start containers
    exit /b 1
)

REM Wait for health checks
echo Waiting for services to become healthy...
timeout /t 5 /nobreak > nul

REM Run the scripting E2E tests
echo Running scripting E2E tests...
docker compose -f docker-compose.scripting.yml exec -T cli python docker/e2e_scripting_test.py --host bridge --port 5020
set TEST_EXIT_CODE=%ERRORLEVEL%

REM Show logs on failure
if %TEST_EXIT_CODE% neq 0 (
    echo.
    echo ==============================================
    echo Tests failed - showing container logs
    echo ==============================================
    echo --- Mock Server Logs ---
    docker compose -f docker-compose.scripting.yml logs mock-server
    echo.
    echo --- Bridge Logs ---
    docker compose -f docker-compose.scripting.yml logs bridge
)

REM Cleanup
echo.
echo Stopping containers...
docker compose -f docker-compose.scripting.yml down

exit /b %TEST_EXIT_CODE%
