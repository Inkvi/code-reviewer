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

# Install Node.js (required for Claude, Codex, and Gemini CLIs)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install Claude CLI
RUN npm install -g @anthropic-ai/claude-code

# Install Codex CLI
RUN npm install -g @openai/codex

# Install Gemini CLI
RUN npm install -g @google/gemini-cli

# Install uv for fast Python dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Allow git operations in any directory (for cloned repos)
RUN git config --system --add safe.directory '*'

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

# Set ownership for non-root user
RUN chown -R 1000:1000 /app

USER 1000

# Bootstrap Gemini config dir and install code-review extension
RUN mkdir -p /home/appuser/.gemini \
    && echo '{}' > /home/appuser/.gemini/settings.json \
    && echo '{}' > /home/appuser/.gemini/projects.json \
    && GEMINI_API_KEY=dummy gemini extensions install https://github.com/gemini-cli-extensions/code-review

ENTRYPOINT ["uv", "run", "code-reviewer"]
CMD ["start"]
