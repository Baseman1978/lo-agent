FROM python:3.11-slim

WORKDIR /app

# Node + Claude Code CLI: nodig voor de Claude Agent SDK (subscription-auth).
# De SDK spawnt de `claude`-binary; daarom moeten Node + de CLI in de image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && npm install -g @anthropic-ai/claude-code \
    && npm cache clean --force \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# D: reproduceerbare builds — alle pip-installs volgen de exacte versies uit
# constraints.txt (bewezen werkend in productie) i.p.v. "nieuwste die past".
COPY constraints.txt ./
ENV PIP_CONSTRAINT=/app/constraints.txt

# Zware, zelden-wijzigende deps + Piper-stem vóór COPY src, zodat een
# src-wijziging (bv. een HUD-bestand) ze niet invalideert -> snelle rebuilds.
RUN pip install --no-cache-dir \
      "faster-whisper>=1.0" "piper-tts>=1.4" claude-agent-sdk
RUN mkdir -p /app/voices && cd /app/voices \
    && python -m piper.download_voices nl_NL-mls-medium

COPY pyproject.toml README.md ./
COPY src ./src
# installeert het span-pakket zelf (deps hierboven al voldaan -> snel)
RUN pip install --no-cache-dir ".[stt,tts]" claude-agent-sdk

EXPOSE 8472

CMD ["uvicorn", "span.server.app:app", "--host", "0.0.0.0", "--port", "8472"]
