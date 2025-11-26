@echo off
setlocal enabledelayedexpansion

echo Starting UMDT UI tests (Windows)...

rem Start the mock server as a host background process and write PID to mock_server.pid
powershell -Command "try { $p = Start-Process -FilePath python -ArgumentList 'mock_server_cli.py start --config docker/e2e_config.yaml --tcp-port 5020' -NoNewWindow -PassThru; $p.Id | Out-File -Encoding ASCII mock_server.pid; Write-Host ('Started mock server PID=' + $p.Id); exit 0 } catch { Write-Host 'Failed to start mock server process'; exit 1 }"
if %ERRORLEVEL% NEQ 0 (
  echo Failed to start mock server process.
  exit /b 1
)

rem Wait for mock server to accept connections on localhost:5020
powershell -Command "for ($i=0; $i -lt 30; $i++) { try { $tcp = New-Object System.Net.Sockets.TcpClient('localhost',5020); $tcp.Close(); Write-Host 'Mock server ready'; exit 0 } catch { Start-Sleep -Seconds 1 } } exit 1"
if %ERRORLEVEL% NEQ 0 (
  echo Mock server did not become ready in time.
  powershell -Command "if (Test-Path 'mock_server.pid') { Stop-Process -Id (Get-Content 'mock_server.pid') -ErrorAction SilentlyContinue }; Remove-Item -Force mock_server.pid -ErrorAction SilentlyContinue"
  exit /b 2
)

rem Run UI tests
python -m pytest tests/ui/ -v --tb=short
set "pytest_exit=%ERRORLEVEL%"

rem Tear down mock server (kill background process if we started it)
powershell -Command "if (Test-Path 'mock_server.pid') { try { Stop-Process -Id (Get-Content 'mock_server.pid') -Force -ErrorAction SilentlyContinue; Write-Host 'Stopped mock server'; } catch { Write-Host 'Failed to stop mock server process' } ; Remove-Item -Force mock_server.pid -ErrorAction SilentlyContinue }"

endlocal
exit /b %pytest_exit%
