# FIRST core-release candidate ONLY; not authorized for VPS deployment.
# No credentials, JWT issuer/private key, CI identity fixtures, Atlas or Docker socket.
# Official amd64 Agentgateway 1.6.0 binary pinned by full SHA-256.
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ARG SOURCE_SHA
ARG AGENTGATEWAY_SHA256=496ed27e6a96b55a9ad7cabe5c2be7172511f44a3cb113e533d22bf93a7f079d
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONNOUSERSITE=1 \
    HOME=/tmp \
    PYTHONPATH=/app
WORKDIR /app
RUN set -eux; \
    test "$(uname -m)" = x86_64; \
    python -m pip install --no-cache-dir --disable-pip-version-check 'mcp==2.3.0' 'PyYAML==6.0.3'; \
    groupadd --system --gid 10001 alfred; \
    useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /tmp alfred
RUN python - <<'PY'
from hashlib import sha256
from pathlib import Path
from urllib.request import urlopen
source = "https://github.com/agentgateway/agentgateway/releases/download/v1.6.0/agentgateway-linux-amd64"
expected = "496ed27e6a96b55a9ad7cabe5c2be7172511f44a3cb113e533d22bf93a7f079d"
with urlopen(source, timeout=45) as response:
    binary = response.read(64 * 1024 * 1024 + 1)
if len(binary) > 64 * 1024 * 1024 or sha256(binary).hexdigest() != expected:
    raise SystemExit("Agentgateway binary digest mismatch")
target = Path("/usr/local/bin/agentgateway")
target.write_bytes(binary)
target.chmod(0o755)
PY
COPY alfred_gateway_core/ /app/alfred_gateway_core/
COPY release/agentgateway-peer/entrypoint.sh /usr/local/bin/alfred-peer-start
RUN chmod 0555 /usr/local/bin/alfred-peer-start \
 && test -n "$SOURCE_SHA" \
 && test "$(printf '%s' "$SOURCE_SHA" | wc -c)" -eq 40 \
 && ! printf '%s' "$SOURCE_SHA" | grep -q '[^0-9a-f]' \
 && python -m compileall -q /app/alfred_gateway_core
LABEL org.opencontainers.image.source="https://github.com/Dmitry-dev-pet/truthrail-mcp" \
      org.opencontainers.image.revision="$SOURCE_SHA" \
      alfred.release_role="agentgateway-peer-core-no-issuer" \
      alfred.gateway_binary="agentgateway-v1.6.0" \
      alfred.gateway_binary_sha256="496ed27e6a96b55a9ad7cabe5c2be7172511f44a3cb113e533d22bf93a7f079d"
USER 10001:10001
EXPOSE 8765 8766
# No stock HTTP HEALTHCHECK: both MCP listeners require JWT, and the
# actual owner-only bind IP is configured by an independently reviewed operator.
# Deployment health must verify the real authenticated tools/list and rollback.
ENTRYPOINT ["/usr/local/bin/alfred-peer-start"]
