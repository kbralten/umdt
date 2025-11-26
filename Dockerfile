# UMDT Docker image for CLI tools (mock server and main CLI)
FROM python:3.13-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install Python dependencies (skip PySide6 for CLI-only image)
RUN pip install --no-cache-dir \
    typer \
    pymodbus \
    rich \
    pyserial \
    pyserial-asyncio \
    PyYAML

# Copy application code
COPY umdt/ ./umdt/
COPY main_cli.py .
COPY mock_server_cli.py .
COPY docker/ ./docker/

# Default to bash so we can run either entrypoint
CMD ["bash"]
