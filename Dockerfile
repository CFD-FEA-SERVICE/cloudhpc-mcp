# Optional: run the server in remote (streamable HTTP) mode in a container.
#   docker build -t cloudhpc-mcp .
#   docker run -p 8080:8080 cloudhpc-mcp          # endpoint: http://localhost:8080/mcp
# Clients send their cloudHPC API key with every request in the
# X-API-Key (or Authorization: Bearer) header. Local-only tools
# (inspect_case, upload_folder, download_results) are not available in this mode.
#
# Two stages: dependencies are installed in a full Python image, then only the
# installed packages are copied into a distroless image (no shell, no package
# manager, no pip, no perl/tar/util-linux...), which keeps the attack surface
# and the vulnerability-scan findings to a minimum. The builder Python version
# must match the distroless one (3.13 on debian13).

# ---------------------------------------------------------------- build stage
FROM python:3.13-slim-trixie AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --target /opt/app .

# ---------------------------------------------------------------- run stage
FROM gcr.io/distroless/python3-debian13:nonroot

ENV PYTHONPATH=/opt/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CLOUDHPC_MCP_MODE=remote \
    PORT=8080

COPY --from=build /opt/app /opt/app

USER nonroot
EXPOSE 8080
ENTRYPOINT ["python3", "-m", "cloudhpc_mcp"]
