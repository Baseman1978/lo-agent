FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[stt]"

EXPOSE 8472

CMD ["uvicorn", "span.server.app:app", "--host", "0.0.0.0", "--port", "8472"]
