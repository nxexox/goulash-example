FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml ./
COPY goulash ./goulash
RUN pip install .

COPY data/seed ./data/seed

RUN mkdir -p data/registry data/snapshots && useradd --create-home app && chown -R app /app
USER app

# Render/Koyeb pass $PORT. Stateless: all data lives in PostgreSQL (DATABASE_URL).
CMD ["sh", "-c", "uvicorn goulash.web.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
