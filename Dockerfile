# ==============================================================================
# APS MVP - Production Dockerfile
# Optimized for Python 3.12-slim runtime with Google OR-Tools & FastAPI
# Designed for Valeo Central Ingress Gateway (Traefik v3 + Kerberos ForwardAuth)
# ==============================================================================

FROM python:3.12-slim-bookworm AS runtime

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    HOST=0.0.0.0 \
    APP_ENV=production

# Install curl for Docker & Traefik health checking
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Create dedicated non-root application user for secure execution
RUN groupadd -g 10001 appuser && \
    useradd -u 10001 -g appuser -m -s /bin/bash appuser

# Set working directory
WORKDIR /app

# Install Python dependencies first for optimal Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code and simulation database files
COPY src/ ./src/
COPY data/ ./data/
COPY config/ ./config/
COPY run_web.py .
COPY ["APS Database- Simulation draft.xlsx", "./"]
COPY ["Production Plan Validation.xlsx", "./"]
COPY ["dataSource.xlsx", "./"]
COPY Specification/ ./Specification/
COPY README.md .

# Ensure proper permissions across application and data directories for non-root user
RUN mkdir -p /app/data && chown -R appuser:appuser /app

# Switch to unprivileged user
USER appuser

# Expose internal HTTP service port
EXPOSE 8000

# Container healthcheck querying the /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Launch the FastAPI application via the robust entry script
CMD ["python", "run_web.py"]
