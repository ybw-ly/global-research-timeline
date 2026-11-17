FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8770 \
    RESEARCH_DB=/app/data/research.db

WORKDIR /app

COPY server ./server
COPY web ./web
COPY config ./config

RUN mkdir -p /app/data

EXPOSE 8770

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8770/api/health', timeout=3)"

CMD ["python", "server/app.py"]
