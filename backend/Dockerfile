FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH

RUN python -m venv /opt/venv \
    && addgroup --system --gid 10001 rag \
    && adduser --system --uid 10001 --ingroup rag --home /app rag

WORKDIR /app
COPY backend/requirements.prod.lock backend/requirements.prod.lock
COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/alembic.ini backend/alembic.ini
COPY backend/alembic backend/alembic
COPY backend/app backend/app

RUN pip install --no-cache-dir "pip==25.2" \
    && pip install --no-cache-dir -r backend/requirements.prod.lock \
    && pip install --no-cache-dir --no-deps ./backend \
    && chown -R rag:rag /app

USER rag
WORKDIR /app/backend
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
