# Disposable hosted-CI packaging fixture; NOT a production release.
# No Atlas, tunnel keys, registry login, GitHub token or Docker socket.
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 HOME=/tmp
WORKDIR /app
RUN python -m pip install --no-cache-dir --disable-pip-version-check 'mcp==2.3.0' \
 && groupadd --system --gid 10001 alfred \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /tmp alfred
COPY agentgateway /usr/local/bin/agentgateway
RUN printf '496ed27e6a96b55a9ad7cabe5c2be7172511f44a3cb113e533d22bf93a7f079d  /usr/local/bin/agentgateway\n' | sha256sum -c - \
 && chmod 0755 /usr/local/bin/agentgateway
COPY peer_backend.py /app/peer_backend.py
COPY entrypoint.sh /usr/local/bin/alfred-peer-start
RUN chmod 0555 /usr/local/bin/alfred-peer-start \
 && chmod 0444 /app/peer_backend.py
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
 CMD python -c "import socket; [(lambda s: (s.connect(('127.0.0.1',p)),s.close()))(socket.socket()) for p in (8765,18765)]" || exit 1
ENTRYPOINT ["/usr/local/bin/alfred-peer-start"]
