# CI-only single-process Truthrail MCP + pinned PUBLIC Atlas source image.
# The build context must contain .pinned-atlas at the reviewed Atlas SHA.
# Building or publishing source is NOT authorization to deploy to a VPS.
FROM python:3.12-slim-bookworm AS atlas-data
WORKDIR /src
COPY .pinned-atlas/ /src/
RUN python -m scripts.atlas_db build >/dev/null \
 && python -c "import sqlite3; db=sqlite3.connect('dist/atlas.sqlite'); assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'; db.close()"

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONNOUSERSITE=1 \
    PYTHONPATH=/app \
    HOME=/tmp \
    ATLAS_SQLITE_PATH=/app/atlas.sqlite \
    ATLAS_MCP_BIND=127.0.0.1 \
    ATLAS_MCP_PORT=8765
WORKDIR /app
COPY requirements-shared.txt /app/requirements-shared.txt
RUN python -m pip install --no-cache-dir --disable-pip-version-check -r /app/requirements-shared.txt \
 && groupadd --system --gid 10001 truthrail \
 && useradd --system --uid 10001 --gid 10001 --home-dir /tmp --no-create-home truthrail
RUN mkdir -p /app/scripts /app/shared_alfred_gateway
COPY shared_alfred_gateway/ /app/shared_alfred_gateway/
COPY --from=atlas-data /src/scripts/atlas_mcp_query.py /app/scripts/atlas_mcp_query.py
COPY --from=atlas-data /src/dist/atlas.sqlite /app/atlas.sqlite
RUN chmod 0444 /app/atlas.sqlite
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD python -c "import socket; socket.create_connection(('127.0.0.1',8765),timeout=2).close()" || exit 1
CMD ["python", "-m", "shared_alfred_gateway.atlas_local"]
