FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY omm ./omm
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir '.[mcp,backup]'

EXPOSE 8000 8001
CMD ["python", "-m", "omm.mcp_server", "--root", "/data", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
