# Host-side runner for Grafana o11y-bench (Harbor) on machines where its Python dependencies have no
# prebuilt wheels - notably Intel Macs: cryptography>=50 ships macOS wheels for arm64 only, so a native
# `uv sync` compiles Rust and needs the Xcode license accepted plus OpenSSL headers. In Linux every
# dependency has a manylinux wheel. The container drives the host's Docker through the mounted socket.
FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim@sha256:76c7449e8bd509a860b61eb060599fd9b1750bcbafe28008f769d35db6109a3d

ARG DOCKER_VERSION=29.9.2
ARG COMPOSE_VERSION=v5.6.1
ARG BUILDX_VERSION=v0.38.1

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl git bash \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_VERSION}.tgz" \
       | tar xz --strip-components=1 -C /usr/local/bin docker/docker \
    && mkdir -p /usr/local/lib/docker/cli-plugins \
    && curl -fsSL -o /usr/local/lib/docker/cli-plugins/docker-compose \
       "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64" \
    && chmod +x /usr/local/lib/docker/cli-plugins/docker-compose \
    && curl -fsSL -o /usr/local/lib/docker/cli-plugins/docker-buildx \
       "https://github.com/docker/buildx/releases/download/${BUILDX_VERSION}/buildx-${BUILDX_VERSION}.linux-amd64" \
    && chmod +x /usr/local/lib/docker/cli-plugins/docker-buildx \
    && docker --version && docker compose version && docker buildx version
