FROM node:22-slim AS web-builder
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    openssl \
    && rm -rf /var/lib/apt/lists/*

# Install GitHub CLI
ARG GH_VERSION=2.74.1
RUN curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_$(dpkg --print-architecture).tar.gz" \
    | tar -xz --strip-components=1 -C /usr/local

# Install Node.js (required for Claude and Codex CLIs)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install Claude CLI
RUN npm install -g @anthropic-ai/claude-code

# Install Codex CLI
RUN npm install -g @openai/codex

# Install OpenCode CLI
RUN npm install -g opencode-ai

# Install uv for fast Python dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Allow git operations in any directory and use gh for HTTPS auth
RUN git config --system --add safe.directory '*' \
    && git config --system credential.https://github.com.helper '!/usr/local/bin/gh auth git-credential'

# Create non-root user
RUN groupadd -g 1000 appuser && useradd -u 1000 -g 1000 -m appuser

WORKDIR /app

# Install Python dependencies
COPY pyproject.toml README.md ./
RUN uv sync --no-dev --no-install-project

# Copy application source
COPY src/ src/
COPY config.example.toml config.example.toml
RUN uv sync --no-dev

# Copy built frontend
COPY --from=web-builder /web/dist web/dist/

# Set ownership for non-root user
RUN chown -R 1000:1000 /app

USER 1000

# Install Antigravity CLI (agy) and seed headless-safe settings.
# artifactReviewPolicy=always-proceed is required: without it agy generates the
# review as an artifact and waits forever for interactive approval in -p mode.
RUN curl -fsSL https://antigravity.google/cli/install.sh | bash \
    && /home/appuser/.local/bin/agy --version \
    && mkdir -p /home/appuser/.gemini/antigravity-cli \
    && printf '{"toolPermission": "always-proceed", "artifactReviewPolicy": "always-proceed", "enableTelemetry": false}\n' \
        > /home/appuser/.gemini/antigravity-cli/settings.json
ENV PATH="/home/appuser/.local/bin:${PATH}"

ENTRYPOINT ["uv", "run", "code-reviewer"]
CMD ["start"]
