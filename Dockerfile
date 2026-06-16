FROM python:3.11-slim

WORKDIR /app

# Node + Claude Code CLI: nodig voor de Claude Agent SDK (subscription-auth).
# De SDK spawnt de `claude`-binary; daarom moeten Node + de CLI in de image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && npm install -g @anthropic-ai/claude-code \
    && npm cache clean --force \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
# claude-agent-sdk erbij; de chat-backend kiest tussen ORQ (default) en de SDK
RUN pip install --no-cache-dir ".[stt]" claude-agent-sdk

EXPOSE 8472

CMD ["uvicorn", "span.server.app:app", "--host", "0.0.0.0", "--port", "8472"]
