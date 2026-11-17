ARG BASE_IMAGE=python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
FROM ${BASE_IMAGE}
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY . .
RUN pip install --no-build-isolation --no-deps . && python -m opspilot.cli ingest && useradd -m pilot && chown -R pilot:pilot /app
USER pilot
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health',timeout=2)"
CMD ["uvicorn", "opspilot.api:app", "--host", "0.0.0.0", "--port", "8765"]
