# Judah Scanner — Cloud Run Dockerfile
# Python 3.11 slim base — small image, fast cold start

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system deps (only what's needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (better Docker layer caching)
COPY requirements.txt .

# Install Python deps
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app
COPY . .

# Cloud Run expects port 8080 by default
# (backend/config.py PORT=8000 is for local dev; gunicorn binds 8080 here)
ENV PORT=8080

# Expose the port
EXPOSE 8080

# Run with gunicorn (production WSGI server) wrapping uvicorn workers
# Use shell form so $PORT gets expanded
CMD exec gunicorn backend.main:app \
    --bind 0.0.0.0:$PORT \
    --workers 1 \
    --worker-class uvicorn.workers.UvicornWorker \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
