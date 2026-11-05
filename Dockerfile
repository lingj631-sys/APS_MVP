# ==============================================================================
# APS MVP - Production Dockerfile
# Optimized for Python 3.12-slim runtime with Google OR-Tools & openpyxl
# ==============================================================================

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PORT=8000 \
    HOST=0.0.0.0 \
    APP_ENV=production

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd -g 10001 appuser && \
    useradd -u 10001 -g appuser -m -s /bin/bash appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code and simulation database files
COPY aps/ ./aps/
COPY data/ ./data/
COPY ["APS_DataSource_Template - WL -V2.xlsx", "./"]
COPY Specification/ ./Specification/
COPY design-system/ ./design-system/
COPY README.md .
COPY run_tests.py .

RUN mkdir -p /app/data && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["python", "-m", "aps.cli", "--help"]
