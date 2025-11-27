@echo off
REM Run UMDT E2E tests using Docker Compose (Windows)
REM
REM Usage:
REM   docker\run_e2e.bat
REM
REM This script:
REM 1. Builds the Docker images (mock-server, bridge, cli)
REM 2. Starts mock-server and bridge containers
REM 3. Runs E2E tests from cli container (via bridge)
REM 4. Cleans up
REM
REM Topology:
REM   cli -> bridge:5020 -> mock-server:5021

setlocal enabledelayedexpansion

cd /d "%~dp0\.."

echo ==========================================
echo UMDT Docker E2E Test Runner
echo ==========================================
echo Topology: cli -^> bridge:5020 -^> mock-server:5021
echo.

REM Clean up any previous containers
echo Cleaning up previous containers...
docker compose down --remove-orphans 2>nul

REM Build images
echo Building Docker images...
docker compose build
if errorlevel 1 (
    echo ERROR: Docker build failed
    exit /b 1
)

REM Start mock server in background
echo Starting mock server...
docker compose up -d mock-server
if errorlevel 1 (
    echo ERROR: Failed to start mock server
    exit /b 1
)

REM Wait for mock server to be healthy
echo Waiting for mock server to be ready...
set READY=0
for /L %%i in (1,1,30) do (
    if !READY! equ 0 (
        docker compose exec -T mock-server python -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('localhost',5021)); s.close()" 2>nul
        if not errorlevel 1 (
            echo Mock server is ready!
            set READY=1
        ) else (
            echo   Waiting... ^(%%i/30^)
            timeout /t 1 /nobreak >nul
        )
    )
)

if !READY! equ 0 (
    echo ERROR: Mock server failed to start
    docker compose logs mock-server
    docker compose down
    exit /b 1
)

REM Start bridge
echo.
echo Starting bridge...
docker compose up -d bridge
if errorlevel 1 (
    echo ERROR: Failed to start bridge
    docker compose down
    exit /b 1
)

REM Wait for bridge to be healthy
echo Waiting for bridge to be ready...
set READY=0
for /L %%i in (1,1,30) do (
    if !READY! equ 0 (
        docker compose exec -T bridge python -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('localhost',5020)); s.close()" 2>nul
        if not errorlevel 1 (
            echo Bridge is ready!
            set READY=1
        ) else (
            echo   Waiting... ^(%%i/30^)
            timeout /t 1 /nobreak >nul
        )
    )
)

if !READY! equ 0 (
    echo ERROR: Bridge failed to start
    docker compose logs bridge
    docker compose down
    exit /b 1
)

REM Run E2E tests from cli container via bridge
echo.
echo Running E2E tests via bridge...
docker compose run --rm cli python docker/e2e_test.py --host bridge --port 5020
set TEST_EXIT_CODE=%errorlevel%

REM Show bridge logs on failure
if not %TEST_EXIT_CODE% equ 0 (
    echo.
    echo Bridge logs:
    docker compose logs bridge
)

REM Cleanup
echo.
echo Cleaning up...
docker compose down

REM Report result
if %TEST_EXIT_CODE% equ 0 (
    echo.
    echo ==========================================
    echo E2E TESTS PASSED
    echo ==========================================
) else (
    echo.
    echo ==========================================
    echo E2E TESTS FAILED ^(exit code: %TEST_EXIT_CODE%^)
    echo ==========================================
)

exit /b %TEST_EXIT_CODE%
