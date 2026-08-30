FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY mimama ./mimama

# uvicorn is intentionally installed here only (not a project dependency)
RUN pip install --no-cache-dir . uvicorn

EXPOSE 8000

ENV MIMAMA_CONFIG_FILENAME=/config/mimama.json

CMD ["uvicorn", "mimama.server.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
