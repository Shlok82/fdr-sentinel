FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install minimal OS dependencies required for PostgreSQL connectivity and health checks
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy project source code
COPY . .

# Ensure entrypoint has LF line endings and is executable
RUN tr -d '\r' < scripts/entrypoint.sh > /tmp/entrypoint.sh && \
    mv /tmp/entrypoint.sh scripts/entrypoint.sh && \
    chmod +x scripts/entrypoint.sh

# Ensure storage directory exists
RUN mkdir -p /app/storage /app/uploads

EXPOSE 8000

ENTRYPOINT ["/bin/sh", "/app/scripts/entrypoint.sh"]
