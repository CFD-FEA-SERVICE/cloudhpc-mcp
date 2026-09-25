# Optional: run the server in remote (streamable HTTP) mode in a container.
#   docker build -t cloudhpc-mcp .
#   docker run -p 8080:8080 cloudhpc-mcp          # endpoint: http://localhost:8080/mcp
# Clients send their cloudHPC API key with every request in the
# X-API-Key (or Authorization: Bearer) header. Local-only tools
# (inspect_case, upload_folder, download_results) are not available in this mode.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CLOUDHPC_MCP_MODE=remote \
    PORT=8080

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd -r -u 10001 app
USER app

EXPOSE 8080
CMD ["cloudhpc-mcp"]
