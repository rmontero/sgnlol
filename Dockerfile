FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock pyproject.toml ./
RUN python -m pip install --no-cache-dir -r requirements.lock
COPY triage ./triage
RUN python -m pip install --no-cache-dir --no-deps . \
    && useradd --uid 10001 --create-home triage \
    && mkdir -p /app/data /app/config \
    && chown -R triage:triage /app/data /app/config
USER triage
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"
CMD ["uvicorn", "triage.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
