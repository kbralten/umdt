@echo off
setlocal enabledelayedexpansion

echo Starting UMDT UI tests (Windows)...

rem Start the mock server container
docker compose up -d mock-server
if %ERRORLEVEL% NEQ 0 (
  echo Failed to start mock server container.
  exit /b 1
)

rem Wait for mock server to accept connections on localhost:5020
powershell -Command "for ($i=0; $i -lt 30; $i++) { try { $tcp = New-Object System.Net.Sockets.TcpClient('localhost',5020); $tcp.Close(); Write-Host 'Mock server ready'; exit 0 } catch { Start-Sleep -Seconds 1 } } exit 1"
if %ERRORLEVEL% NEQ 0 (
  echo Mock server did not become ready in time.
  docker compose down
  exit /b 2
)

rem Run UI tests
python -m pytest tests/ui/ -v --tb=short
set "pytest_exit=%ERRORLEVEL%"

rem Tear down mock server
docker compose down

endlocal
exit /b %pytest_exit%
