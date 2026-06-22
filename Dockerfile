# beauty-soul-service — HTTP entry for the beauty_soul beautification engine.
# Runs as a sidecar in the KiX soul image; the worker calls it over the compose
# network at http://beauty-soul-service:9102 (see 03-beauty_soul-HTTP接口需求.md §1).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# System libs required by Chromium (visual audit + smoke test).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
        libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
        libgbm1 libasound2 libpango-1.0-0 libcairo2 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Vendored trinity_protocol wheel (offline install — core analytical dependency).
COPY vendor/ ./vendor/
COPY pyproject.toml README.md ./
COPY beauty_soul/ ./beauty_soul/
COPY cli.py ./

RUN pip install ./vendor/trinity_protocol-*.whl \
    && pip install . \
    && python -m playwright install --with-deps chromium

EXPOSE 9102

# Container-level liveness probe (mirrors KiX worker pre-flight, §5.1).
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9102/v1/health').status==200 else 1)" || exit 1

CMD ["uvicorn", "beauty_soul.server:app", "--host", "0.0.0.0", "--port", "9102"]
