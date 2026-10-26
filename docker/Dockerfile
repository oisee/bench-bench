# syntax=docker/dockerfile:1
FROM node:22.14.0-bookworm-slim
ARG CLAUDE_VERSION=2.1.309
ARG CODEX_VERSION=0.162.1
ARG PI_VERSION=1.0.2
RUN --mount=type=secret,id=proxy_ca \
    apt_ca=""; if [ -f /run/secrets/proxy_ca ]; then apt_ca="-o Acquire::https::CaInfo=/run/secrets/proxy_ca"; fi; \
    apt-get $apt_ca update && apt-get $apt_ca install -y --no-install-recommends \
      python3 git bubblewrap ca-certificates curl procps \
    && rm -rf /var/lib/apt/lists/*
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca; fi; \
    npm install -g "@anthropic-ai/claude-code@${CLAUDE_VERSION}" \
      "@openai/codex@${CODEX_VERSION}" "@earendil-works/pi-coding-agent@${PI_VERSION}" \
    && npm cache clean --force \
    && printf 'claude=%s\ncodex=%s\npi=%s\n' "$CLAUDE_VERSION" "$CODEX_VERSION" "$PI_VERSION" > /opt/agent-versions.txt
WORKDIR /opt/abap-bench
COPY bin/ bin/
COPY tasks-abap/PACKAGE tasks-abap/PACKAGE
COPY tasks-abap/tiers/ tasks-abap/tiers/
COPY matrix.yaml matrix.yaml
COPY gateway/codex-models.json gateway/codex-models.json
RUN mkdir -p /out /pkg && chown -R node:node /opt/abap-bench /out
USER node
ENTRYPOINT ["/opt/abap-bench/bin/abap-bench"]
